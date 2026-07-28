"""Portfolio-weiter Full-Recompute: Queue ueber alle Firmen eines
Portfolios mit begrenzter Parallelitaet.

Reuse statt Neubau: pro Firma laeuft exakt der bestehende
refresh_company_values-Pfad (inkl. Two-Stage, Backfill, net_debt-Ableitung,
Konsistenz-Pass, Calc-Engine) in einem Worker-Thread mit eigener DB-Session.
Das globale LLM-Throttling uebernimmt der geteilte claude_limiter.
Fortschritt pro Firma kommt weiterhin aus dem bestehenden Job-Store
(die Cards pollen ihn schon) — hier gibt es nur den Portfolio-Ueberblick.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from uuid import UUID

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_BATCHES: dict[UUID, "BatchState"] = {}

MAX_PARALLEL_COMPANIES = 3


@dataclass
class BatchState:
    status: str = "running"  # running | done
    # Abschnitt des Batch-Laufs: companies | gap_fill | report | done
    phase: str = "companies"
    total: int = 0
    done: int = 0
    failed: list[str] = field(default_factory=list)
    current: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str | None = None
    # Vollstaendigkeits-Report ({"years": [...], "per_key": {key: {...}}}),
    # gesetzt am Ende des Laufs.
    report: dict | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status, "phase": self.phase,
            "total": self.total, "done": self.done,
            "failed": self.failed, "current": self.current,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "report": self.report,
        }


def get_batch_status(portfolio_id: UUID) -> dict:
    with _LOCK:
        state = _BATCHES.get(portfolio_id)
        return state.to_dict() if state else {"status": "idle"}


def _recompute_one(company_id: UUID, owner_id: UUID, api_keys: list[str], year: int) -> None:
    """Ein Full Refresh fuer eine Firma — eigene Session, eigener Job-Eintrag."""
    from app.auth.models import User
    from app.db import SessionLocal
    from app.values.routes import refresh_company_values
    from app.values.schemas import RefreshRequest

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == owner_id).one()
        payload = RefreshRequest(keys=api_keys, period_type="FY", period_year=year)
        refresh_company_values(company_id=company_id, payload=payload, user=user, db=db)
        db.commit()
    finally:
        db.close()


def start_portfolio_recompute(portfolio_id: UUID, owner_id: UUID) -> dict:
    """Startet den Batch als Daemon-Thread; idempotent solange einer laeuft."""
    from app.db import SessionLocal
    from app.companies.models import Company
    from app.values.models import SourceType, ValueDefinition

    with _LOCK:
        existing = _BATCHES.get(portfolio_id)
        if existing and existing.status == "running":
            return existing.to_dict()

    db = SessionLocal()
    try:
        companies = (
            db.query(Company)
            .filter(Company.portfolio_id == portfolio_id)
            .order_by(Company.name)
            .all()
        )
        company_ids = [(c.id, c.ticker) for c in companies]
        api_keys = [
            vd.key for vd in db.query(ValueDefinition)
            .filter(ValueDefinition.source_type == SourceType.API)
            .order_by(ValueDefinition.sort_order)
        ]
    finally:
        db.close()

    state = BatchState(
        total=len(company_ids),
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    with _LOCK:
        _BATCHES[portfolio_id] = state

    year = date.today().year

    def _tracked(cid: UUID, ticker: str) -> None:
        with _LOCK:
            state.current.append(ticker)
        try:
            _recompute_one(cid, owner_id, api_keys, year)
        finally:
            with _LOCK:
                if ticker in state.current:
                    state.current.remove(ticker)

    def _finalize() -> None:
        """Batch-Abschluss: Gap-Fill + not_found-Platzhalter + Report.

        Fehler werden geloggt, aber der Batch darf hier nie crashen —
        der Firmen-Recompute ist zu diesem Zeitpunkt bereits durch.
        """
        from scripts import fill_gaps

        years = [year - 1, year]

        with _LOCK:
            state.phase = "gap_fill"
        db = SessionLocal()
        try:
            # Getrennte try/except-Bloecke: die not_found-Platzhalter muessen
            # auch dann geschrieben werden, wenn der Gap-Fill wirft.
            try:
                import os as _os
                from scripts.two_stage_research import CostTracker
                # Budget-Deckel fuer die automatische Gap-Fill-Phase (Review-
                # Befund: sonst unbegrenzte LLM-Ausgaben bei vielen Luecken).
                cap = float(_os.environ.get("BATCH_GAPFILL_MAX_USD", "50"))
                stats = fill_gaps.fill_portfolio_gaps(
                    db, portfolio_id, years,
                    progress_cb=lambda msg: logger.info("batch gap-fill: %s", msg),
                    cost_tracker=CostTracker(max_usd=cap),
                )
                logger.info("batch gap-fill stats: %s", stats)
            except Exception:
                logger.exception("batch: gap-fill failed for portfolio %s", portfolio_id)
                db.rollback()
            try:
                placeholders = fill_gaps.write_not_found_placeholders(db, portfolio_id, years)
                logger.info("batch: %d not_found placeholders written", placeholders)
            except Exception:
                logger.exception("batch: placeholder write failed for portfolio %s", portfolio_id)
                db.rollback()
        finally:
            db.close()

        with _LOCK:
            state.phase = "report"
        db = SessionLocal()
        try:
            report = fill_gaps.build_completeness_report(db, portfolio_id, years)
            with _LOCK:
                state.report = report
            for key, s in report["per_key"].items():
                logger.info(
                    "batch report: %s expected=%d with_value=%d not_found=%d excluded=%d",
                    key, s["expected"], s["with_value"], s["not_found"], s["excluded"],
                )
        except Exception:
            logger.exception("batch: report phase failed for portfolio %s", portfolio_id)
        finally:
            db.close()

    def _run() -> None:
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_COMPANIES) as pool:
            futures = {}
            for cid, ticker in company_ids:
                futures[pool.submit(_tracked, cid, ticker)] = ticker
            for fut in as_completed(futures):
                ticker = futures[fut]
                with _LOCK:
                    state.done += 1
                try:
                    fut.result()
                    logger.info("batch: %s done (%d/%d)", ticker, state.done, state.total)
                except Exception as e:
                    with _LOCK:
                        state.failed.append(ticker)
                    logger.error("batch: %s FAILED: %s", ticker, e)
        _finalize()
        with _LOCK:
            state.status = "done"
            state.phase = "done"
            state.finished_at = datetime.now(timezone.utc).isoformat()
        logger.info("batch: portfolio %s finished, %d/%d ok, failed: %s",
                    portfolio_id, state.total - len(state.failed), state.total, state.failed)

    threading.Thread(target=_run, daemon=True, name=f"batch-{portfolio_id}").start()
    return state.to_dict()
