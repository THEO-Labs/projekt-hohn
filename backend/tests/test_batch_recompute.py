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
            json={"name": f"C{i}", "ticker": f"T{i}",
                  "isin": "US0378331005", "currency": "EUR"},
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
    """Nach dem Firmen-Loop laufen gap_fill (Platzhalter) und report,
    dann phase=done. write_not_found_placeholders fuellt alle erwarteten
    Zellen, der Report weist sie als not_found aus.
    """
    import app.values.batch as batch
    import scripts.fill_gaps as fg

    monkeypatch.setattr(batch, "_recompute_one", lambda cid, oid, keys, year: None)

    # Phase zum Zeitpunkt der Abschluss-Schritte mitschneiden.
    phases = []
    orig_placeholders = fg.write_not_found_placeholders
    orig_report = fg.build_completeness_report

    def spy_placeholders(db_, pid_, years):
        phases.append(batch.get_batch_status(pid_)["phase"])
        return orig_placeholders(db_, pid_, years)

    def spy_report(db_, pid_, years):
        phases.append(batch.get_batch_status(pid_)["phase"])
        return orig_report(db_, pid_, years)

    monkeypatch.setattr(fg, "write_not_found_placeholders", spy_placeholders)
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
    # (kein Recompute-Write), keine strukturellen Ausnahmen (keine Banken).
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


def test_finalize_survives_placeholder_crash(client, db, monkeypatch):
    """Wirft write_not_found_placeholders, laeuft der Report trotzdem —
    der Batch endet mit phase='done' und Report."""
    import app.values.batch as batch
    import scripts.fill_gaps as fg

    monkeypatch.setattr(batch, "_recompute_one", lambda cid, oid, keys, year: None)

    def boom(*a, **kw):
        raise RuntimeError("placeholder kaputt")

    monkeypatch.setattr(fg, "write_not_found_placeholders", boom)

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
    # Kein Platzhalter geschrieben (Crash) — der Report zaehlt die Zellen
    # als fehlend, aber nicht als not_found.
    for stats in report["per_key"].values():
        assert stats["expected"] == 3 * 2 * 5
        assert stats["not_found"] == 0
        assert stats["with_value"] == 0


def test_foreign_portfolio_404(client, db):
    _setup(client, db)
    r = client.post("/api/portfolios/00000000-0000-0000-0000-000000000000/full-recompute")
    assert r.status_code == 404


# --- Smart Recompute (only_stale) ---------------------------------------

def _companies_by_ticker(db, pid):
    from uuid import UUID as _UUID
    from app.companies.models import Company
    rows = db.query(Company).filter(Company.portfolio_id == _UUID(pid)).all()
    return {c.ticker: c for c in rows}


def _mark_two_stage(db, company_id, fetched_at):
    """Simuliert einen frueheren Two-Stage-Lauf fuer die Firma."""
    from app.values.models import CompanyValue
    db.add(CompanyValue(
        company_id=company_id, value_key="market_cap",
        period_year=2025, period_type="FY",
        primary_method="two_stage_verified", fetched_at=fetched_at,
    ))
    db.commit()


def test_select_stale_companies(client, db):
    """Stale = Earnings in der Vergangenheit UND nach dem letzten
    Two-Stage-Lauf; nie gerechnete Firmen sind immer stale."""
    from datetime import date, datetime, timedelta, timezone
    from uuid import UUID as _UUID

    from app.values.batch import select_stale_companies

    pid = _setup(client, db, n_companies=6)
    by_ticker = _companies_by_ticker(db, pid)
    today = date.today()
    last_week = datetime.now(timezone.utc) - timedelta(days=7)
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)

    # T0: Earnings nach letztem Lauf -> stale
    _mark_two_stage(db, by_ticker["T0"].id, last_week)
    by_ticker["T0"].next_earnings_date = today - timedelta(days=3)
    # T1: Earnings vor letztem Lauf -> nicht stale
    _mark_two_stage(db, by_ticker["T1"].id, yesterday)
    by_ticker["T1"].next_earnings_date = today - timedelta(days=7)
    # T2: kein Earnings-Datum -> nicht stale
    _mark_two_stage(db, by_ticker["T2"].id, last_week)
    # T3: keine two_stage-Zeilen -> stale (nie gerechnet)
    by_ticker["T3"].next_earnings_date = today - timedelta(days=3)
    # T4: Earnings in der Zukunft -> nicht stale
    _mark_two_stage(db, by_ticker["T4"].id, last_week)
    by_ticker["T4"].next_earnings_date = today + timedelta(days=3)
    # T5: Earnings heute -> nicht stale (koennen noch ausstehen)
    _mark_two_stage(db, by_ticker["T5"].id, last_week)
    by_ticker["T5"].next_earnings_date = today
    db.commit()

    stale = select_stale_companies(db, _UUID(pid))
    assert sorted(c.ticker for c in stale) == ["T0", "T3"]


def test_smart_recompute_filters_selection(client, db, monkeypatch):
    """Endpoint rechnet nur die stale Firmen und meldet sie als selected."""
    from datetime import date, datetime, timedelta, timezone

    import app.values.batch as batch

    calls: list = []
    monkeypatch.setattr(batch, "_recompute_one",
                        lambda cid, oid, keys, year: calls.append(cid))

    pid = _setup(client, db, n_companies=3)
    by_ticker = _companies_by_ticker(db, pid)
    last_week = datetime.now(timezone.utc) - timedelta(days=7)

    # T0 stale (Earnings nach letztem Lauf), T1 und T2 aktuell.
    _mark_two_stage(db, by_ticker["T0"].id, last_week)
    by_ticker["T0"].next_earnings_date = date.today() - timedelta(days=2)
    _mark_two_stage(db, by_ticker["T1"].id, last_week)
    by_ticker["T1"].next_earnings_date = date.today() + timedelta(days=30)
    _mark_two_stage(db, by_ticker["T2"].id, last_week)
    db.commit()

    r = client.post(f"/api/portfolios/{pid}/smart-recompute")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "stale_only"
    assert body["total"] == 1
    assert body["selected"] == ["T0"]

    for _ in range(100):
        s = client.get(f"/api/portfolios/{pid}/full-recompute-status").json()
        if s["status"] == "done":
            break
        time.sleep(0.1)
    assert s["status"] == "done"
    assert s["mode"] == "stale_only"
    assert s["done"] == 1
    assert s["failed"] == []
    assert calls == [by_ticker["T0"].id]


def test_smart_recompute_empty_selection_done_immediately(client, db, monkeypatch):
    """Keine stale Firma: Batch ist sofort done mit total=0, kein Recompute."""
    from datetime import datetime, timedelta, timezone

    import app.values.batch as batch

    calls: list = []
    monkeypatch.setattr(batch, "_recompute_one",
                        lambda cid, oid, keys, year: calls.append(cid))

    pid = _setup(client, db, n_companies=2)
    by_ticker = _companies_by_ticker(db, pid)
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    for c in by_ticker.values():
        _mark_two_stage(db, c.id, yesterday)  # gerechnet, keine Earnings-Info

    r = client.post(f"/api/portfolios/{pid}/smart-recompute")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done"
    assert body["mode"] == "stale_only"
    assert body["total"] == 0
    assert body["selected"] == []

    s = client.get(f"/api/portfolios/{pid}/full-recompute-status").json()
    assert s["status"] == "done"
    assert s["total"] == 0
    assert calls == []


def test_smart_recompute_foreign_portfolio_404(client, db):
    _setup(client, db)
    r = client.post("/api/portfolios/00000000-0000-0000-0000-000000000000/smart-recompute")
    assert r.status_code == 404


def test_stale_when_run_and_earnings_same_day(client, db, monkeypatch):
    """Lauf am Earnings-Tag VOR dem Release: Firma muss stale sein (>=)."""
    from datetime import date, datetime, timedelta, timezone
    from decimal import Decimal
    import app.values.batch as batch
    from app.companies.models import Company
    from app.values.models import CompanyValue

    pid = _setup(client, db, n_companies=1)
    comp = db.query(Company).filter(Company.portfolio_id == pid).one()
    yesterday = date.today() - timedelta(days=1)
    comp.next_earnings_date = yesterday
    db.add(CompanyValue(
        company_id=comp.id, value_key="revenue", period_type="FY", period_year=2025,
        numeric_value=Decimal("1"), primary_method="two_stage_confirmed",
        fetched_at=datetime.combine(yesterday, datetime.min.time()).replace(tzinfo=timezone.utc),
    ))
    db.commit()
    stale = batch.select_stale_companies(db, pid)
    assert [c.id for c in stale] == [comp.id]
