"""derive_q4_instant_from_fy: Instant-Keys (Bilanz) — leeres/ersetzbares
Q4 wird aus dem FY-Actual gefuellt (gleicher Stichtag, Q4 = FY,
calculated, is_forecast=False). Greift nur bei FY-Actual mit Wert
(de facto abgeschlossenes Jahr); Forecast-FY zaehlt nicht. Besetzte
authoritative Q4-Slots bleiben. Generisch, nicht DE-gegated."""
from datetime import date
from decimal import Decimal

import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.values.consistency import derive_q4_instant_from_fy
from app.values.models import CompanyValue

YEAR = date.today().year - 1


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="q4instant@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.flush()
    portfolio = Portfolio(name="P", owner_user_id=user.id)
    db.add(portfolio)
    db.flush()
    comp = Company(
        portfolio_id=portfolio.id, name="Deutsche TestCo", ticker="DTC",
        currency="EUR", isin="DE0001234567",
        fiscal_year_end_month=12, fiscal_year_end_day=31,
    )
    db.add(comp)
    db.commit()
    return comp


def _seed(db, comp, key, ptype, value, is_forecast=False, year=YEAR, **kw):
    row = CompanyValue(
        company_id=comp.id, value_key=key, period_type=ptype, period_year=year,
        numeric_value=Decimal(str(value)) if value is not None else None,
        is_forecast=is_forecast, source_name=kw.pop("source_name", "seed"),
        primary_method=kw.pop("primary_method", "provider"),
        currency=kw.pop("currency", "EUR"), **kw,
    )
    db.add(row)
    db.commit()
    return row


def _rows(db, comp, key, ptype):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == ptype,
            CompanyValue.period_year == YEAR,
        )
        .order_by(CompanyValue.is_forecast.asc())
        .all()
    )


def test_empty_q4_filled_from_fy_actual(db, company):
    """Happy Path: FY-Actual vorhanden, Q4 leer -> Q4 = FY als
    calculated-Actual (gleicher Stichtag)."""
    _seed(db, company, "cash_and_equivalents", "FY", 8_220_000_000,
          primary_method="statement_research")

    written = derive_q4_instant_from_fy(db, company.id, YEAR)
    db.commit()

    assert written == 1
    (q4,) = _rows(db, company, "cash_and_equivalents", "Q4")
    assert q4.numeric_value == Decimal("8220000000")
    assert q4.is_forecast is False
    assert q4.primary_method == "calculated"
    assert q4.currency == "EUR"
    assert q4.source_name.startswith("Q4 = FY-Bilanzstichtag")


def test_occupied_q4_stays(db, company):
    """Besetztes Q4 (provider/statement_research-Actual) ist
    authoritative — kein Overwrite."""
    _seed(db, company, "cash_and_equivalents", "FY", 8_220_000_000)
    provider_q4 = _seed(db, company, "cash_and_equivalents", "Q4", 8_300_000_000)
    _seed(db, company, "lt_debt", "FY", 5_000_000_000)
    stmt_q4 = _seed(db, company, "lt_debt", "Q4", 5_100_000_000,
                    primary_method="statement_research")

    written = derive_q4_instant_from_fy(db, company.id, YEAR)
    db.commit()

    assert written == 0
    db.refresh(provider_q4)
    assert provider_q4.numeric_value == Decimal("8300000000")
    assert provider_q4.primary_method == "provider"
    db.refresh(stmt_q4)
    assert stmt_q4.numeric_value == Decimal("5100000000")
    assert stmt_q4.primary_method == "statement_research"


def test_forecast_fy_does_not_count(db, company):
    """Forecast-FY (Guidance/Fortschreibung) ist kein Stichtags-Actual —
    keine Ableitung, keine Q4-Zeile."""
    _seed(db, company, "cash_and_equivalents", "FY", 8_500_000_000,
          is_forecast=True, primary_method="web_guidance")

    assert derive_q4_instant_from_fy(db, company.id, YEAR) == 0
    assert _rows(db, company, "cash_and_equivalents", "Q4") == []


def test_stale_carry_forward_forecast_replaced(db, company):
    """SAP-Regression: eine alte Bilanz-Fortschreibungs-Forecast-Zeile
    im Q4-Slot (calculated, ersetzbar) wird zum Q4 = FY-Actual
    umgezogen — es bleibt genau EINE Zeile."""
    _seed(db, company, "cash_and_equivalents", "FY", 8_220_000_000,
          primary_method="statement_research")
    stale = _seed(db, company, "cash_and_equivalents", "Q4", 8_554_000_000,
                  is_forecast=True, primary_method="calculated",
                  source_name="Fortschreibung letzter Bilanzstichtag (Q3)")

    written = derive_q4_instant_from_fy(db, company.id, YEAR)
    db.commit()

    assert written == 1
    rows = _rows(db, company, "cash_and_equivalents", "Q4")
    assert len(rows) == 1
    db.refresh(stale)
    assert stale.numeric_value == Decimal("8220000000")
    assert stale.is_forecast is False
    assert stale.primary_method == "calculated"


def test_manual_q4_stays(db, company):
    """Manuelle Q4-Zeilen sind authoritative."""
    _seed(db, company, "st_debt", "FY", 1_000_000_000)
    manual = _seed(db, company, "st_debt", "Q4", 999,
                   primary_method="manual", manually_overridden=True)

    assert derive_q4_instant_from_fy(db, company.id, YEAR) == 0
    db.refresh(manual)
    assert manual.numeric_value == Decimal("999")
