from uuid import uuid4

import pytest

from app.values.progress import (
    _JOBS,
    cleanup_old_jobs,
    finish_job,
    get_job,
    start_job,
    update_job,
)


@pytest.fixture(autouse=True)
def clear_jobs():
    _JOBS.clear()
    yield
    _JOBS.clear()


def test_start_job_creates_entry():
    cid = uuid4()
    start_job(cid, 10)
    job = get_job(cid)
    assert job is not None
    assert job["total"] == 10
    assert job["completed"] == 0
    assert job["status"] == "running"
    assert job["current_key"] is None
    assert job["finished_at"] is None


def test_update_job_increments_completed():
    cid = uuid4()
    start_job(cid, 5)
    update_job(cid, "eps_growth")
    update_job(cid, "stock_price")
    job = get_job(cid)
    assert job["completed"] == 2
    assert job["current_key"] == "stock_price"


def test_finish_job_sets_status_and_timestamp():
    cid = uuid4()
    start_job(cid, 3)
    finish_job(cid)
    job = get_job(cid)
    assert job["status"] == "done"
    assert job["finished_at"] is not None


def test_finish_job_failed_status():
    cid = uuid4()
    start_job(cid, 3)
    finish_job(cid, status="failed")
    job = get_job(cid)
    assert job["status"] == "failed"


def test_get_job_returns_none_for_unknown():
    assert get_job(uuid4()) is None


def test_get_job_returns_copy():
    cid = uuid4()
    start_job(cid, 2)
    job = get_job(cid)
    job["status"] = "tampered"
    assert get_job(cid)["status"] == "running"


def test_cleanup_removes_old_finished_jobs():
    cid = uuid4()
    start_job(cid, 1)
    finish_job(cid)

    from datetime import datetime, timezone, timedelta
    from app.values import progress as prog
    old_time = (datetime.now(timezone.utc) - timedelta(seconds=700)).isoformat()
    prog._JOBS[cid]["finished_at"] = old_time

    cleanup_old_jobs(max_age_seconds=600)
    assert get_job(cid) is None


def test_cleanup_keeps_recent_finished_jobs():
    cid = uuid4()
    start_job(cid, 1)
    finish_job(cid)
    cleanup_old_jobs(max_age_seconds=600)
    assert get_job(cid) is not None


def test_cleanup_keeps_running_jobs():
    cid = uuid4()
    start_job(cid, 1)
    cleanup_old_jobs(max_age_seconds=0)
    assert get_job(cid) is not None
