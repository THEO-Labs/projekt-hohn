"""Daily-Refresh (stammdaten_only) rechnet die kursbasierten FY-Kennzahlen
nach: nach dem SNAPSHOT-Schluss-Calc laeuft _run_and_persist_calculations
fuer jedes FY-Jahr mit vorhandenen Zeilen. Hermetisch — der Calc ist als
Spy gepatcht, Provider-Kette leer."""

from decimal import Decimal
from uuid import UUID

from app.auth.models import User
from app.auth.security import hash_password
from app.values.models import CompanyValue


def _setup(client, db, email):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]
    # Firma direkt per ORM (API-Neuanlage gesperrt: ISIN-Pflicht).
    from app.companies.models import Company
    company = Company(portfolio_id=UUID(pid), name="TestCo", ticker="TST",
                      currency="EUR")
    db.add(company)
    db.commit()
    return company.id


def _seed_fy_row(db, cid, year, key="net_income"):
    db.add(CompanyValue(
        company_id=cid, value_key=key, period_type="FY", period_year=year,
        numeric_value=Decimal("1000"),
    ))
    db.commit()


def _patch_env(monkeypatch, calc_spy):
    import app.values.routes as routes

    monkeypatch.setattr(routes, "get_providers", lambda key: [])
    monkeypatch.setattr(routes, "_run_and_persist_calculations", calc_spy)


def _daily_refresh(client, cid):
    # Payload wie der "Daily Numbers"-Button im Frontend
    # (refreshCompanyDaily): SNAPSHOT + stammdaten_only.
    return client.post(
        f"/api/companies/{cid}/values/refresh",
        json={"keys": [], "period_type": "SNAPSHOT", "stammdaten_only": True},
    )


def test_daily_refresh_recalcs_all_fy_years(client, db, monkeypatch):
    """Nach dem SNAPSHOT-Calc laeuft der Calc fuer jedes FY-Jahr mit
    vorhandenen Zeilen (aufsteigend sortiert)."""
    cid = _setup(client, db, "dailyfy1@example.com")
    _seed_fy_row(db, cid, 2025)
    _seed_fy_row(db, cid, 2024)

    calls: list[tuple[str, int | None]] = []
    _patch_env(monkeypatch, lambda db_, cid_, pt, py: calls.append((pt, py)) or [])

    r = _daily_refresh(client, cid)

    assert r.status_code == 200
    assert calls == [("SNAPSHOT", None), ("FY", 2024), ("FY", 2025)]


def test_daily_refresh_without_fy_rows_only_snapshot(client, db, monkeypatch):
    """Keine FY-Zeilen -> nur der bisherige SNAPSHOT-Calc, kein FY-Lauf."""
    cid = _setup(client, db, "dailyfy2@example.com")

    calls: list[tuple[str, int | None]] = []
    _patch_env(monkeypatch, lambda db_, cid_, pt, py: calls.append((pt, py)) or [])

    r = _daily_refresh(client, cid)

    assert r.status_code == 200
    assert calls == [("SNAPSHOT", None)]


def test_fy_recalc_error_never_breaks_daily_job(client, db, monkeypatch):
    """Ein Fehler im FY-Nachrechnen bricht den Daily-Refresh nie ab —
    die uebrigen Jahre laufen weiter, die Antwort bleibt 200."""
    cid = _setup(client, db, "dailyfy3@example.com")
    _seed_fy_row(db, cid, 2024)
    _seed_fy_row(db, cid, 2025)

    calls: list[tuple[str, int | None]] = []

    def _calc(db_, cid_, pt, py):
        calls.append((pt, py))
        if py == 2024:
            raise RuntimeError("boom")
        return []

    _patch_env(monkeypatch, _calc)

    r = _daily_refresh(client, cid)

    assert r.status_code == 200
    assert calls == [("SNAPSHOT", None), ("FY", 2024), ("FY", 2025)]


def test_non_stammdaten_snapshot_refresh_skips_fy_recalc(client, db, monkeypatch):
    """stammdaten_only=False (SNAPSHOT-Refresh ohne Keys): der neue
    FY-Block laeuft nicht — er gehoert nur zum Daily-Pfad."""
    cid = _setup(client, db, "dailyfy4@example.com")
    _seed_fy_row(db, cid, 2025)

    calls: list[tuple[str, int | None]] = []
    _patch_env(monkeypatch, lambda db_, cid_, pt, py: calls.append((pt, py)) or [])

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={"keys": [], "period_type": "SNAPSHOT", "stammdaten_only": False},
    )

    assert r.status_code == 200
    assert calls == [("SNAPSHOT", None)]
