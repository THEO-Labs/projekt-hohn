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


def test_batch_finalize_phases_and_report(client, db, monkeypatch):
    """Nach dem Firmen-Loop laufen gap_fill und report, dann phase=done.

    Research ist gemockt (schlaegt fehl) -> Gap-Fill schreibt nichts,
    write_not_found_placeholders fuellt alle erwarteten Zellen, der Report
    weist sie als not_found aus.
    """
    import app.values.batch as batch
    import scripts.fill_gaps as fg

    monkeypatch.setattr(batch, "_recompute_one", lambda cid, oid, keys, year: None)

    def research_boom(**kw):
        raise RuntimeError("research in Tests blockiert")

    monkeypatch.setattr(fg, "research_two_stage", research_boom)

    # Phase zum Zeitpunkt der Abschluss-Schritte mitschneiden.
    phases = []
    orig_fill = fg.fill_portfolio_gaps
    orig_report = fg.build_completeness_report

    def spy_fill(db_, pid_, years, ticker=None, progress_cb=None, **kw):
        phases.append(batch.get_batch_status(pid_)["phase"])
        return orig_fill(db_, pid_, years, ticker=ticker, progress_cb=progress_cb, **kw)

    def spy_report(db_, pid_, years):
        phases.append(batch.get_batch_status(pid_)["phase"])
        return orig_report(db_, pid_, years)

    monkeypatch.setattr(fg, "fill_portfolio_gaps", spy_fill)
    monkeypatch.setattr(fg, "build_completeness_report", spy_report)

    pid = _setup(client, db)
    client.post(f"/api/portfolios/{pid}/full-recompute")
    for _ in range(200):
        s = client.get(f"/api/portfolios/{pid}/full-recompute-status").json()
        if s["status"] == "done":
            break
        time.sleep(0.1)
    assert s["status"] == "done"
    assert s["phase"] == "done"
    assert phases == ["gap_fill", "report"]

    # Report: 3 Firmen x 2 Jahre x 5 Perioden, alles not_found-Platzhalter
    # (Research fehlgeschlagen), keine strukturellen Ausnahmen (keine Banken).
    report = s["report"]
    assert report is not None
    assert len(report["years"]) == 2
    assert len(report["per_key"]) > 0
    for stats in report["per_key"].values():
        assert stats["expected"] == 3 * 2 * 5
        assert stats["not_found"] == stats["expected"]
        assert stats["with_value"] == 0
        assert stats["excluded"] == 0

    # Platzhalter liegen wirklich in der DB.
    from app.values.models import CompanyValue
    db.rollback()  # frischen Snapshot der Thread-Commits sehen
    n = db.query(CompanyValue).filter(CompanyValue.primary_method == "not_found").count()
    assert n == len(report["per_key"]) * 3 * 2 * 5


def test_finalize_survives_gap_fill_crash(client, db, monkeypatch):
    """Wirft fill_portfolio_gaps, laufen die not_found-Platzhalter und der
    Report trotzdem — der Batch endet mit phase='done' und Report."""
    import app.values.batch as batch
    import scripts.fill_gaps as fg

    monkeypatch.setattr(batch, "_recompute_one", lambda cid, oid, keys, year: None)

    def boom(*a, **kw):
        raise RuntimeError("gap-fill kaputt")

    monkeypatch.setattr(fg, "fill_portfolio_gaps", boom)

    pid = _setup(client, db)
    client.post(f"/api/portfolios/{pid}/full-recompute")
    for _ in range(200):
        s = client.get(f"/api/portfolios/{pid}/full-recompute-status").json()
        if s["status"] == "done":
            break
        time.sleep(0.1)

    assert s["status"] == "done"
    assert s["phase"] == "done"
    report = s["report"]
    assert report is not None
    # Platzhalter wurden trotz Gap-Fill-Crash geschrieben; der Report weist
    # alle erwarteten Zellen als not_found aus.
    for stats in report["per_key"].values():
        assert stats["expected"] == 3 * 2 * 5
        assert stats["not_found"] == stats["expected"]

    from app.values.models import CompanyValue
    db.rollback()  # frischen Snapshot der Thread-Commits sehen
    n = db.query(CompanyValue).filter(CompanyValue.primary_method == "not_found").count()
    assert n == len(report["per_key"]) * 3 * 2 * 5


def test_foreign_portfolio_404(client, db):
    _setup(client, db)
    r = client.post("/api/portfolios/00000000-0000-0000-0000-000000000000/full-recompute")
    assert r.status_code == 404
