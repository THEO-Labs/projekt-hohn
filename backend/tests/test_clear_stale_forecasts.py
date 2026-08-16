"""derive_clear_stale_forecasts: Forecast-Werte BERICHTETER Perioden
werden geleert (Allianz-Fall: Q4 laengst berichtet, Recherche liefert
keinen Actual — die alte Schaetzung stand weiter in der Anzeige und sah
wie eine aktuelle Zahl aus). Manuelle Forecasts und geschuetzte
Adjusted-Werte bleiben; Actuals werden nie angefasst."""
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.values.consistency import derive_clear_stale_forecasts
from app.values.models import CompanyValue

CLEARED_SOURCE = "Geraeumt: Periode berichtet, Schaetzung veraltet"


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="stale@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.flush()
    portfolio = Portfolio(name="P", owner_user_id=user.id)
    db.add(portfolio)
    db.flush()
    comp = Company(
        portfolio_id=portfolio.id, name="TestCo", ticker="TST",
        currency="EUR", isin="DE0001234567",
    )
    db.add(comp)
    db.commit()
    return comp


def _set_fy_end(db, comp, days_back):
    """FY-Ende so setzen, dass Q4 vor `days_back` Tagen endete. Karenz
    (REPORTING_GRACE_DAYS=45): days_back>=45 -> alle Perioden berichtet,
    days_back<45 -> Q4/FY noch in der Karenz (offen), Q1-Q3 berichtet."""
    p_end = date.today() - timedelta(days=days_back)
    comp.fiscal_year_end_month = p_end.month
    comp.fiscal_year_end_day = p_end.day
    db.commit()
    return p_end.year


def _seed(db, comp, key, ptype, value, year, is_forecast=False, **kw):
    row = CompanyValue(
        company_id=comp.id, value_key=key, period_type=ptype, period_year=year,
        numeric_value=Decimal(str(value)) if value is not None else None,
        is_forecast=is_forecast, source_name=kw.pop("source_name", "seed"),
        primary_method=kw.pop("primary_method", "two_stage_confirmed"),
        currency=kw.pop("currency", "EUR"), **kw,
    )
    db.add(row)
    db.commit()
    return row


def test_reported_quarter_forecast_cleared(db, company):
    """Berichtete Periode (Karenz abgelaufen) + Forecast mit Wert: die
    Zeile wird geleert — auch wenn ein Actual daneben existiert
    (Datenhygiene, die Zelle zeigt danach den Actual)."""
    year = _set_fy_end(db, company, days_back=60)
    _seed(db, company, "net_income", "Q4", 2664, year)  # Actual
    fc = _seed(db, company, "net_income", "Q4", 1290, year, is_forecast=True)

    cleared = derive_clear_stale_forecasts(db, company.id, year)
    db.commit()

    assert cleared == 1
    db.refresh(fc)
    assert fc.numeric_value is None
    assert fc.source_name == CLEARED_SOURCE
    assert fc.is_forecast is True


def test_reported_fy_forecast_cleared(db, company):
    """FY-Kriterium analog ueber das FY-Ende (= Q4-Stichtag) + Karenz."""
    year = _set_fy_end(db, company, days_back=60)
    fc = _seed(db, company, "revenue", "FY", 100000, year, is_forecast=True)

    assert derive_clear_stale_forecasts(db, company.id, year) == 1
    db.commit()
    db.refresh(fc)
    assert fc.numeric_value is None
    assert fc.source_name == CLEARED_SOURCE


def test_unreported_period_stays(db, company):
    """Q4/FY noch in der Karenz (offen): deren Forecasts bleiben — nur
    das berichtete Q1 wird geraeumt."""
    year = _set_fy_end(db, company, days_back=20)
    q4 = _seed(db, company, "net_income", "Q4", 500, year, is_forecast=True)
    fy = _seed(db, company, "net_income", "FY", 2000, year, is_forecast=True)
    q1 = _seed(db, company, "net_income", "Q1", 400, year, is_forecast=True)

    cleared = derive_clear_stale_forecasts(db, company.id, year)
    db.commit()

    assert cleared == 1
    db.refresh(q4)
    db.refresh(fy)
    db.refresh(q1)
    assert q4.numeric_value == Decimal("500")
    assert fy.numeric_value == Decimal("2000")
    assert q1.numeric_value is None
    assert q1.source_name == CLEARED_SOURCE


def test_manual_forecast_stays(db, company):
    """Manuelle Forecasts sind die bewusste Nutzer-Entscheidung — die
    Lock-Regel laesst sie von Actual-Writern abloesen, nicht von hier."""
    year = _set_fy_end(db, company, days_back=60)
    manual = _seed(db, company, "net_income", "Q4", 1500, year,
                   is_forecast=True, manually_overridden=True,
                   primary_method="manual")

    assert derive_clear_stale_forecasts(db, company.id, year) == 0
    db.commit()
    db.refresh(manual)
    assert manual.numeric_value == Decimal("1500")
    assert manual.source_name == "seed"


def test_protected_adjusted_stays_gaap_cleared(db, company):
    """adjusted_is_protected (URL-belegt): die Adjusted-Spur bleibt, die
    GAAP-Spur wird geleert."""
    year = _set_fy_end(db, company, days_back=60)
    fc = _seed(db, company, "eps_diluted", "Q4", Decimal("1.29"), year,
               is_forecast=True,
               numeric_value_adjusted=Decimal("1.50"),
               adjustments_source="https://ir.example.com/q4.pdf")

    assert derive_clear_stale_forecasts(db, company.id, year) == 1
    db.commit()
    db.refresh(fc)
    assert fc.numeric_value is None
    assert fc.numeric_value_adjusted == Decimal("1.50")
    assert fc.source_name == CLEARED_SOURCE


def test_unprotected_adjusted_only_cleared(db, company):
    """Adjusted-only-Forecast ohne Schutz (Two-Stage-Format): wird
    ebenfalls geraeumt."""
    year = _set_fy_end(db, company, days_back=60)
    fc = _seed(db, company, "eps_diluted", "Q4", None, year,
               is_forecast=True,
               numeric_value_adjusted=Decimal("1.50"),
               adjustments_source="quote | https://example.com")

    assert derive_clear_stale_forecasts(db, company.id, year) == 1
    db.commit()
    db.refresh(fc)
    assert fc.numeric_value is None
    assert fc.numeric_value_adjusted is None


def test_actual_rows_untouched(db, company):
    """Actuals sind nie Ziel der Raeumung."""
    year = _set_fy_end(db, company, days_back=60)
    actual = _seed(db, company, "net_income", "Q4", 2664, year,
                   primary_method="provider")

    assert derive_clear_stale_forecasts(db, company.id, year) == 0
    db.commit()
    db.refresh(actual)
    assert actual.numeric_value == Decimal("2664")
    assert actual.source_name == "seed"


def test_idempotent_second_run_clears_nothing(db, company):
    """Bereits geleerte Zeilen (und geschuetzte Adjusted-Reste) zaehlen
    beim zweiten Lauf nicht erneut."""
    year = _set_fy_end(db, company, days_back=60)
    _seed(db, company, "net_income", "Q4", 1290, year, is_forecast=True)
    protected = _seed(db, company, "eps_diluted", "Q4", None, year,
                      is_forecast=True,
                      numeric_value_adjusted=Decimal("1.50"),
                      adjustments_source="Manual", source_name="alt")

    assert derive_clear_stale_forecasts(db, company.id, year) == 1
    db.commit()
    assert derive_clear_stale_forecasts(db, company.id, year) == 0
    db.refresh(protected)
    # Geschuetztes adjusted allein ist kein Leerungs-Fall — die Zeile
    # wird gar nicht angefasst.
    assert protected.numeric_value_adjusted == Decimal("1.50")
    assert protected.source_name == "alt"


# --- Wiring: der Refresh-Flow ruft die Raeumung pro consistency_year auf,
# VOR validate_cross_metrics. ----------------------------------------------


def test_refresh_flow_clears_before_validate(client, db, monkeypatch):
    import app.values.consistency as cons
    import app.values.routes as routes

    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="wiring@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login",
                json={"email": "wiring@example.com", "password": "pw1234"})
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]
    # Nicht-US-Firma direkt per ORM (API-Neuanlage gesperrt); ohne ISIN
    # bleibt is_us_company==False (Statement-Pfad).
    from app.companies.models import Company
    company = Company(portfolio_id=UUID(pid), name="TestCo", ticker="TST",
                      currency="EUR")
    db.add(company)
    db.commit()
    cid = company.id

    events: list[tuple[str, int]] = []
    monkeypatch.setattr(cons, "derive_clear_stale_forecasts",
                        lambda db_, cid_, y: events.append(("clear", y)))
    monkeypatch.setattr(cons, "validate_cross_metrics",
                        lambda db_, cid_, y, **kw: events.append(("validate", y)))
    monkeypatch.setattr(routes, "_run_and_persist_calculations",
                        lambda *a, **kw: [])
    monkeypatch.setattr(routes, "_ensure_previous_year_inputs",
                        lambda *a, **kw: None)

    year = date.today().year
    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={"keys": ["net_income"], "period_type": "FY", "period_year": year},
    )

    assert r.status_code == 200
    clear_events = [e for e in events if e[0] == "clear"]
    assert ("clear", year) in clear_events
    # Pro Jahr: Raeumung VOR validate_cross_metrics.
    for _, y in clear_events:
        assert events.index(("clear", y)) < events.index(("validate", y))
