"""Prev-Year-Backfill des FY-Refresh: Frische-Kriterium fuer FY N-1,
Robustheit gegen Actual+Forecast-Paare und der Konsistenz-Pass, der nach
einem Backfill auch fuer das Vorjahr laufen muss."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from app.auth.models import User
from app.auth.security import hash_password
from app.values.models import CompanyValue
from app.values.routes import _prev_year_needs_backfill

PREV = 2025


def _setup(client, db, email="backfill@example.com"):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]
    c = client.post(
        f"/api/portfolios/{pid}/companies",
        json={"name": "TestCo", "ticker": "TST", "currency": "EUR"},
    ).json()
    return UUID(c["id"])


def _fy_row(cid, key, year, pm, is_forecast=False, value=Decimal("1")):
    return CompanyValue(
        company_id=cid, value_key=key, period_type="FY", period_year=year,
        numeric_value=value, primary_method=pm, is_forecast=is_forecast,
    )


def test_no_row_needs_backfill(client, db):
    cid = _setup(client, db)
    assert _prev_year_needs_backfill(db, cid, "net_income", PREV) is True


def test_two_stage_row_is_fresh(client, db):
    cid = _setup(client, db)
    db.add(_fy_row(cid, "net_income", PREV, "two_stage_verified"))
    db.commit()
    assert _prev_year_needs_backfill(db, cid, "net_income", PREV) is False


def test_provider_row_is_fresh(client, db):
    """Geankerte FY N-1 (primary_method='provider') gilt als frisch — sonst
    recherchiert jeder Refresh die XBRL-geankerte Zeile erneut per LLM."""
    cid = _setup(client, db)
    db.add(_fy_row(cid, "net_income", PREV, "provider"))
    db.commit()
    assert _prev_year_needs_backfill(db, cid, "net_income", PREV) is False


def test_stale_method_needs_backfill(client, db):
    cid = _setup(client, db)
    db.add(_fy_row(cid, "net_income", PREV, "estimate"))
    db.commit()
    assert _prev_year_needs_backfill(db, cid, "net_income", PREV) is True


def test_none_method_needs_backfill(client, db):
    cid = _setup(client, db)
    db.add(_fy_row(cid, "net_income", PREV, None))
    db.commit()
    assert _prev_year_needs_backfill(db, cid, "net_income", PREV) is True


def test_actual_forecast_pair_no_crash_actual_decides(client, db):
    """Actual+Forecast koennen als Paar koexistieren — one_or_none() haette
    hier MultipleResultsFound geworfen. Die Actual-Zeile entscheidet."""
    cid = _setup(client, db)
    db.add(_fy_row(cid, "net_income", PREV, "estimate", is_forecast=True))
    db.add(_fy_row(cid, "net_income", PREV, "provider", is_forecast=False))
    db.commit()
    assert _prev_year_needs_backfill(db, cid, "net_income", PREV) is False


def _patch_consistency_spies(monkeypatch):
    import app.values.consistency as cons
    import app.values.routes as routes

    validated: list[int] = []
    monkeypatch.setattr(cons, "validate_cross_metrics",
                        lambda db_, cid_, y, **kw: validated.append(y))
    monkeypatch.setattr(cons, "derive_net_debt_from_components", lambda db_, cid_, y: None)
    monkeypatch.setattr(cons, "derive_missing_ocf", lambda db_, cid_, y: None)
    monkeypatch.setattr(cons, "derive_sbc_quarters", lambda db_, cid_, y: None)
    monkeypatch.setattr(routes, "_run_and_persist_calculations", lambda *a, **kw: [])
    monkeypatch.setattr(routes, "_ensure_previous_year_inputs", lambda *a, **kw: None)
    return validated


def test_refresh_runs_consistency_for_prev_year_after_backfill(client, db, monkeypatch):
    """Backfill hat FY N-1 geschrieben -> Konsistenz-Pass (inkl.
    validate_cross_metrics) laeuft fuer N-1 UND N — sonst blieben
    FY/Quartals-Mismatches im Vorjahr ungeflaggt."""
    cid = _setup(client, db)
    year = date.today().year
    # FY N-1 stale (primary_method='estimate') -> Backfill-Key.
    db.add(_fy_row(cid, "net_income", year - 1, "estimate"))
    db.commit()

    validated = _patch_consistency_spies(monkeypatch)

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={"keys": ["net_income"], "period_type": "FY", "period_year": year},
    )

    assert r.status_code == 200
    assert validated == [year - 1, year]


def test_refresh_without_backfill_validates_only_current_year(client, db, monkeypatch):
    cid = _setup(client, db)
    year = date.today().year
    # FY N-1 bereits frisch -> kein Backfill.
    db.add(_fy_row(cid, "net_income", year - 1, "two_stage_verified"))
    db.commit()

    validated = _patch_consistency_spies(monkeypatch)

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={"keys": ["net_income"], "period_type": "FY", "period_year": year},
    )

    assert r.status_code == 200
    assert validated == [year]
