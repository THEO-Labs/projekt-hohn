"""derive_open_quarter_from_fy_estimate: das offene Rest-Quartal wird
deterministisch aus dem FY-Estimate (Guidance/Konsens) berechnet:
Q_offen = FY_est - Summe(berichtete Quartale). Ersetzt LLM-Schaetzungen
des offenen Quartals; manuelle/PDF-Zeilen bleiben unangetastet."""
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
    user = User(email="openq@example.com", password_hash=hash_password("pw1234"))
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


def _seed(db, comp, key, ptype, value, is_forecast=False, **kw):
    row = CompanyValue(
        company_id=comp.id, value_key=key, period_type=ptype, period_year=YEAR,
        numeric_value=Decimal(str(value)) if value is not None else None,
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


def test_derives_open_q4_and_overwrites_llm_estimate(db, company):
    """FY-Estimate + drei berichtete Quartale: das LLM-geschaetzte Q4 wird
    durch das deterministische Residuum ersetzt."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "net_income", q, v)
    _seed(db, company, "net_income", "Q4", 999, is_forecast=True,
          primary_method="web_guidance")
    _seed(db, company, "net_income", "FY", 460, is_forecast=True,
          primary_method="two_stage_confirmed")

    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 1
    row = _open_q_row(db, company, "net_income", "Q4")
    assert row.numeric_value == Decimal("130")
    assert row.primary_method == "calculated"
    assert row.is_forecast is True
    assert row.source_name.startswith("FY-Guidance minus berichtete Quartale")
    assert "460" in row.source_name


def test_creates_missing_open_quarter_row(db, company):
    """Fehlt die Zeile des offenen Quartals ganz, wird sie angelegt."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "revenue", q, v)
    _seed(db, company, "revenue", "FY", 500, is_forecast=True,
          primary_method="web_guidance")

    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 1
    row = _open_q_row(db, company, "revenue", "Q4")
    assert row is not None
    assert row.numeric_value == Decimal("170")
    assert row.currency == "USD"


def test_manual_estimate_not_overwritten(db, company):
    """Manuelle Zeilen des offenen Quartals sind authoritative."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "net_income", q, v)
    manual = _seed(db, company, "net_income", "Q4", 200, is_forecast=True,
                   primary_method="manual", manually_overridden=True)
    _seed(db, company, "net_income", "FY", 460, is_forecast=True)

    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 0
    db.refresh(manual)
    assert manual.numeric_value == Decimal("200")
    assert manual.primary_method == "manual"


def test_skips_when_more_than_one_quarter_open(db, company):
    """Zwei offene Quartale: das Residuum ist nicht eindeutig verteilbar."""
    for q, v in (("Q1", 100), ("Q2", 110)):
        _seed(db, company, "net_income", q, v)
    _seed(db, company, "net_income", "FY", 460, is_forecast=True)

    assert derive_open_quarter_from_fy_estimate(db, company.id, YEAR) == 0
    assert _open_q_row(db, company, "net_income", "Q3") is None
    assert _open_q_row(db, company, "net_income", "Q4") is None


def test_skips_when_fy_actual_exists(db, company):
    """Abgeschlossenes Jahr (FY-Actual mit Wert): nichts abzuleiten."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "net_income", q, v)
    _seed(db, company, "net_income", "FY", 470)
    _seed(db, company, "net_income", "FY", 460, is_forecast=True)

    assert derive_open_quarter_from_fy_estimate(db, company.id, YEAR) == 0


def test_skips_when_no_fy_estimate(db, company):
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "net_income", q, v)

    assert derive_open_quarter_from_fy_estimate(db, company.id, YEAR) == 0


def test_negative_residual_for_always_positive_key_skipped(db, company):
    """Stale FY-Guidance unter der Summe der berichteten Quartale wuerde
    ein negatives Dividenden-Quartal ergeben — wird nicht persistiert."""
    for q, v in (("Q1", 200), ("Q2", 200), ("Q3", 200)):
        _seed(db, company, "dividends", q, v)
    _seed(db, company, "dividends", "FY", 500, is_forecast=True)

    assert derive_open_quarter_from_fy_estimate(db, company.id, YEAR) == 0
    assert _open_q_row(db, company, "dividends", "Q4") is None


def test_negative_residual_for_net_income_allowed(db, company):
    """Fuer nicht-always-positive Keys (net_income) ist ein negatives
    Residuum legitim (Verlustquartal laut Guidance)."""
    for q, v in (("Q1", 200), ("Q2", 200), ("Q3", 200)):
        _seed(db, company, "net_income", q, v)
    _seed(db, company, "net_income", "FY", 550, is_forecast=True)

    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 1
    row = _open_q_row(db, company, "net_income", "Q4")
    assert row.numeric_value == Decimal("-50")
