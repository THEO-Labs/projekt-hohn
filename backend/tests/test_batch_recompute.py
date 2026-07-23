"""Portfolio-Batch: Queue-Endpoint startet, Status zaehlt, Idempotenz."""

import time

from app.auth.models import User
from app.auth.security import hash_password


def _setup(client, db, n_companies=3):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="batch@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": "batch@example.com", "password": "pw1234"})
    pid = client.post("/api/portfolios", json={"name": "DAX"}).json()["id"]
    for i in range(n_companies):
        client.post(
            f"/api/portfolios/{pid}/companies",
            json={"name": f"C{i}", "ticker": f"T{i}", "currency": "EUR"},
        )
    return pid


def test_batch_runs_all_companies(client, db, monkeypatch):
    import app.values.batch as batch

    calls: list = []
    monkeypatch.setattr(batch, "_recompute_one",
                        lambda cid, oid, keys, year: calls.append(cid))
    pid = _setup(client, db)

    r = client.post(f"/api/portfolios/{pid}/full-recompute")
    assert r.status_code == 200
    assert r.json()["total"] == 3

    for _ in range(50):
        s = client.get(f"/api/portfolios/{pid}/full-recompute-status").json()
        if s["status"] == "done":
            break
        time.sleep(0.1)
    assert s["status"] == "done"
    assert s["done"] == 3
    assert s["failed"] == []
    assert len(calls) == 3


def test_batch_records_failures(client, db, monkeypatch):
    import app.values.batch as batch

    def boom(cid, oid, keys, year):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(batch, "_recompute_one", boom)
    pid = _setup(client, db, n_companies=2)
    client.post(f"/api/portfolios/{pid}/full-recompute")
    for _ in range(50):
        s = client.get(f"/api/portfolios/{pid}/full-recompute-status").json()
        if s["status"] == "done":
            break
        time.sleep(0.1)
    assert len(s["failed"]) == 2


def test_foreign_portfolio_404(client, db):
    _setup(client, db)
    r = client.post("/api/portfolios/00000000-0000-0000-0000-000000000000/full-recompute")
    assert r.status_code == 404
