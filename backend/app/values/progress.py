import threading
from datetime import datetime, timezone
from uuid import UUID

_LOCK = threading.Lock()
_JOBS: dict[UUID, dict] = {}


def start_job(company_id: UUID, total_keys: int) -> None:
    with _LOCK:
        _JOBS[company_id] = {
            "company_id": str(company_id),
            "total": total_keys,
            "completed": 0,
            "successful": 0,
            "current_key": None,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        }


def update_job(company_id: UUID, current_key: str, completed_delta: int = 1, success: bool = False) -> None:
    with _LOCK:
        job = _JOBS.get(company_id)
        if job:
            job["completed"] += completed_delta
            job["current_key"] = current_key
            if success:
                job["successful"] = job.get("successful", 0) + 1


def mark_success(company_id: UUID) -> None:
    with _LOCK:
        job = _JOBS.get(company_id)
        if job:
            job["successful"] = job.get("successful", 0) + 1


def finish_job(company_id: UUID, status: str = "done") -> None:
    with _LOCK:
        job = _JOBS.get(company_id)
        if job:
            job["status"] = status
            job["finished_at"] = datetime.now(timezone.utc).isoformat()


def get_job(company_id: UUID) -> dict | None:
    with _LOCK:
        job = _JOBS.get(company_id)
        if not job:
            return None
        return dict(job)


def cleanup_old_jobs(max_age_seconds: int = 600) -> None:
    now = datetime.now(timezone.utc)
    with _LOCK:
        to_remove = []
        for cid, job in _JOBS.items():
            finished = job.get("finished_at")
            if finished:
                finished_dt = datetime.fromisoformat(finished)
                if (now - finished_dt).total_seconds() > max_age_seconds:
                    to_remove.append(cid)
        for cid in to_remove:
            del _JOBS[cid]
