"""Single-worker queue for PDF extraction jobs.

Runs as one asyncio task in the FastAPI process. Each iteration claims the
oldest PENDING IRDocument via SELECT ... FOR UPDATE SKIP LOCKED and runs the
extraction synchronously in a worker thread. Serialising the workload prevents
multiple parallel Claude calls from drowning the uvicorn event loop.

State lives entirely in the IRDocument table (extraction_status). Restarting
the container cleanly resets in-flight jobs back to PENDING so they get
retried — no in-memory queue to lose.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.ir_documents.models import ExtractionStatus, IRDocument

logger = logging.getLogger(__name__)

_worker_task: asyncio.Task | None = None
_wake_event: asyncio.Event | None = None
_idle_sleep_seconds = 10.0
_stuck_threshold = timedelta(minutes=15)


def wake_worker() -> None:
    """Signal the worker to poll immediately instead of waiting for next idle tick."""
    if _wake_event is not None:
        _wake_event.set()


def _claim_next_job() -> tuple | None:
    """Synchronously claim the next PENDING job. Returns (doc_id, company_id) or None.
    Uses FOR UPDATE SKIP LOCKED so it's safe to run multiple workers, even though
    we only run one in practice."""
    db = SessionLocal()
    try:
        row = (
            db.query(IRDocument)
            .filter(IRDocument.extraction_status == ExtractionStatus.PENDING)
            .order_by(IRDocument.uploaded_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )
        if row is None:
            db.commit()
            return None
        row.extraction_status = ExtractionStatus.EXTRACTING
        row.extraction_error = None
        doc_id = row.id
        company_id = row.company_id
        db.commit()
        return (doc_id, company_id)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _recover_orphaned_jobs() -> None:
    """Reset jobs stuck in EXTRACTING (e.g. from previous crash/restart) back to PENDING.
    Also fails jobs that have been EXTRACTING way too long (likely deadlocked)."""
    db = SessionLocal()
    try:
        # On startup, anything still EXTRACTING is orphaned because the only producer
        # of that status is this worker, and there is no live worker yet.
        reset = (
            db.query(IRDocument)
            .filter(IRDocument.extraction_status == ExtractionStatus.EXTRACTING)
            .update(
                {"extraction_status": ExtractionStatus.PENDING, "extraction_error": None},
                synchronize_session=False,
            )
        )
        db.commit()
        if reset:
            logger.info("Recovered %d orphaned EXTRACTING jobs back to PENDING", reset)
    except Exception:
        db.rollback()
        logger.exception("Failed to recover orphaned jobs")
    finally:
        db.close()


def _fail_stuck_jobs() -> None:
    """Mark jobs that have been EXTRACTING longer than _stuck_threshold as FAILED.
    Protects against any single job hanging the queue indefinitely."""
    cutoff = datetime.now(timezone.utc) - _stuck_threshold
    db = SessionLocal()
    try:
        stuck = (
            db.query(IRDocument)
            .filter(
                IRDocument.extraction_status == ExtractionStatus.EXTRACTING,
                IRDocument.uploaded_at < cutoff,
            )
            .update(
                {
                    "extraction_status": ExtractionStatus.FAILED,
                    "extraction_error": f"Timeout: Job lief länger als {int(_stuck_threshold.total_seconds() // 60)} Minuten",
                },
                synchronize_session=False,
            )
        )
        db.commit()
        if stuck:
            logger.warning("Marked %d stuck EXTRACTING jobs as FAILED", stuck)
    except Exception:
        db.rollback()
    finally:
        db.close()


async def _worker_loop() -> None:
    global _wake_event
    _wake_event = asyncio.Event()
    logger.info("PDF extraction worker started")
    while True:
        try:
            claimed = await asyncio.to_thread(_claim_next_job)
            if claimed is None:
                # Queue empty — wait for wake signal or idle tick
                try:
                    await asyncio.wait_for(_wake_event.wait(), timeout=_idle_sleep_seconds)
                except asyncio.TimeoutError:
                    # Periodic stuck-job check while idle
                    await asyncio.to_thread(_fail_stuck_jobs)
                _wake_event.clear()
                continue

            doc_id, company_id = claimed
            logger.info("Worker picked up extraction job doc=%s company=%s", doc_id, company_id)
            # Lazy import to avoid circular dep at module load
            from app.ir_documents.routes import _run_extraction_job
            await asyncio.to_thread(_run_extraction_job, doc_id, company_id)
            logger.info("Worker finished extraction job doc=%s", doc_id)
        except asyncio.CancelledError:
            logger.info("PDF extraction worker shutting down")
            raise
        except Exception:
            logger.exception("Worker iteration failed; sleeping 5s")
            await asyncio.sleep(5)


async def start_worker() -> None:
    """Idempotent — safe to call from FastAPI lifespan."""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        await asyncio.to_thread(_recover_orphaned_jobs)
        _worker_task = asyncio.create_task(_worker_loop(), name="ir-pdf-extraction-worker")


async def stop_worker() -> None:
    global _worker_task
    if _worker_task is None or _worker_task.done():
        return
    _worker_task.cancel()
    try:
        await _worker_task
    except asyncio.CancelledError:
        pass
    finally:
        _worker_task = None


def queue_position(doc) -> int | None:
    """Compute the queue position (1-indexed) of a PENDING doc among other PENDING docs.
    Returns None for non-PENDING docs."""
    if doc.extraction_status != ExtractionStatus.PENDING:
        return None
    db = SessionLocal()
    try:
        ahead = (
            db.query(IRDocument.id)
            .filter(
                IRDocument.extraction_status == ExtractionStatus.PENDING,
                IRDocument.uploaded_at < doc.uploaded_at,
            )
            .count()
        )
        return ahead + 1
    finally:
        db.close()
