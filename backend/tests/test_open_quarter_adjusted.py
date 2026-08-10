"""derive_open_quarter_from_fy_estimate, adjusted-Spur: hat der FY-Forecast
einen adjusted-Wert und haben ALLE berichteten Quartale adjusted-Werte,
wird auch Q_offen adjusted = FY_adj - Sigma(Q_adj) in dieselbe
Q-Forecast-Zeile geschrieben. Geschuetzte Adjusted-Felder (Manual/URL)
bleiben; negative Residuen auf Always-Positive-Keys wie im GAAP-Pfad."""
from decimal import Decimal

import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.values.consistency import derive_open_quarter_from_fy_estimate
from app.values.models import CompanyValue

YEAR = 2026


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="openqadj@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.flush()
    portfolio = Portfolio(name="P", owner_user_id=user.id)
    db.add(portfolio)
    db.flush()
    comp = Company(
        portfolio_id=portfolio.id, name="TestCo", ticker="TST",
        currency="USD", isin="US0001234567",
    )
    db.add(comp)
    db.commit()
    return comp


def _seed(db, comp, key, ptype, value, adjusted=None, is_forecast=False,
          year=YEAR, **kw):
    row = CompanyValue(
        company_id=comp.id, value_key=key, period_type=ptype, period_year=year,
        numeric_value=Decimal(str(value)) if value is not None else None,
        numeric_value_adjusted=(
            Decimal(str(adjusted)) if adjusted is not None else None
        ),
        is_forecast=is_forecast, source_name=kw.pop("source_name", "seed"),
        primary_method=kw.pop("primary_method", "provider"),
        currency=kw.pop("currency", "USD"), **kw,
    )
    db.add(row)
    db.commit()
    return row


def _open_q_row(db, comp, key, ptype):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == ptype,
            CompanyValue.period_year == YEAR,
            CompanyValue.is_forecast.is_(True),
        )
        .one_or_none()
    )


def test_adjusted_residual_written_with_gaap(db, company):
    """Happy Path: GAAP- und adjusted-Residuum landen in derselben
    Q4-Forecast-Zeile; Note gesetzt, Source NICHT 'Manual'."""
    for q, v, a in (("Q1", 100, 105), ("Q2", 110, 115), ("Q3", 120, 125)):
        _seed(db, company, "net_income", q, v, adjusted=a)
    _seed(db, company, "net_income", "FY", 460, adjusted=480,
          is_forecast=True, primary_method="two_stage_confirmed")

    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 1
    row = _open_q_row(db, company, "net_income", "Q4")
    assert row.numeric_value == Decimal("130")
    assert row.numeric_value_adjusted == Decimal("135")
    assert row.adjustments_note == (
        "Abgeleitet: FY-Schaetzung minus berichtete Quartale"
    )
    assert row.adjustments_source is None
    assert row.primary_method == "calculated"


def test_missing_quarter_adjusted_prevents_adjusted_derivation(db, company):
    """Fehlt einem berichteten Quartal der adjusted-Wert, wird nur die
    GAAP-Spur abgeleitet."""
    _seed(db, company, "net_income", "Q1", 100, adjusted=105)
    _seed(db, company, "net_income", "Q2", 110)
    _seed(db, company, "net_income", "Q3", 120, adjusted=125)
    _seed(db, company, "net_income", "FY", 460, adjusted=480, is_forecast=True)

    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 1
    row = _open_q_row(db, company, "net_income", "Q4")
    assert row.numeric_value == Decimal("130")
    assert row.numeric_value_adjusted is None
    assert row.adjustments_note is None


def test_missing_fy_adjusted_prevents_adjusted_derivation(db, company):
    for q, v, a in (("Q1", 100, 105), ("Q2", 110, 115), ("Q3", 120, 125)):
        _seed(db, company, "net_income", q, v, adjusted=a)
    _seed(db, company, "net_income", "FY", 460, is_forecast=True)

    assert derive_open_quarter_from_fy_estimate(db, company.id, YEAR) == 1
    db.commit()
    row = _open_q_row(db, company, "net_income", "Q4")
    assert row.numeric_value == Decimal("130")
    assert row.numeric_value_adjusted is None


def test_protected_manual_adjusted_stays(db, company):
    """Manueller Adjusted-Override (adjustments_source 'Manual') auf der
    Q-Forecast-Zeile bleibt; die GAAP-Spur wird trotzdem ersetzt."""
    for q, v, a in (("Q1", 100, 105), ("Q2", 110, 115), ("Q3", 120, 125)):
        _seed(db, company, "net_income", q, v, adjusted=a)
    est = _seed(db, company, "net_income", "Q4", 999, adjusted=140,
                is_forecast=True, primary_method="two_stage_confirmed",
                adjustments_note="Manuell gesetzt", adjustments_source="Manual")
    _seed(db, company, "net_income", "FY", 460, adjusted=480, is_forecast=True)

    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 1
    db.refresh(est)
    assert est.numeric_value == Decimal("130")
    assert est.numeric_value_adjusted == Decimal("140")
    assert est.adjustments_note == "Manuell gesetzt"
    assert est.adjustments_source == "Manual"


def test_negative_adjusted_residual_on_always_positive_key_skipped(db, company):
    """dividends (always positive): GAAP-Residuum positiv, adjusted-Residuum
    negativ — nur die GAAP-Spur wird geschrieben."""
    for q in ("Q1", "Q2", "Q3"):
        _seed(db, company, "dividends", q, 100, adjusted=200)
    _seed(db, company, "dividends", "FY", 450, adjusted=500, is_forecast=True)

    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 1
    row = _open_q_row(db, company, "dividends", "Q4")
    assert row.numeric_value == Decimal("150")
    assert row.numeric_value_adjusted is None
