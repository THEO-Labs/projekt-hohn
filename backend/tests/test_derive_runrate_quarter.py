"""derive_runrate_quarter: SBC/Buybacks — offenes Quartal des laufenden FY
als Durchschnitt der berichteten Quartale fortschreiben, aber NUR als
Fallback ohne FY-Konsens/Guidance-Forecast (der Konsens-Pfad via
derive_open_quarter_from_fy_estimate hat Vorrang). Danach FY = Summe der
4 Quartale mit Konventions-Quelle."""
from decimal import Decimal

import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.values.consistency import derive_runrate_quarter
from app.values.models import CompanyValue

YEAR = 2026


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="runrate@example.com", password_hash=hash_password("pw1234"))
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


def _seed(db, comp, key, ptype, value, is_forecast=False, year=YEAR, **kw):
    row = CompanyValue(
        company_id=comp.id, value_key=key, period_type=ptype, period_year=year,
        numeric_value=Decimal(str(value)) if value is not None else None,
        is_forecast=is_forecast, source_name=kw.pop("source_name", "seed"),
        primary_method=kw.pop("primary_method", "provider"),
        currency=kw.pop("currency", "USD"), **kw,
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


def test_happy_path_sbc_runrate_and_fy_sum(db, company):
    """Q1-Q3 berichtet, kein FY-Forecast: Q4 = Durchschnitt (100), FY =
    Summe der 4 Quartale mit Konventions-Quelle."""
    for q, v in (("Q1", 90), ("Q2", 100), ("Q3", 110)):
        _seed(db, company, "sbc", q, v)

    written = derive_runrate_quarter(db, company.id, YEAR)
    db.commit()

    assert written == 1
    (q4,) = _rows(db, company, "sbc", "Q4")
    assert q4.numeric_value == Decimal("100")
    assert q4.is_forecast is True
    assert q4.primary_method == "calculated"
    assert q4.source_name == "Fortschreibung Quartals-Runrate (Q1-Qn-Durchschnitt)"
    (fy,) = _rows(db, company, "sbc", "FY")
    assert fy.numeric_value == Decimal("400")
    assert fy.source_name == "Summe der Quartale (inkl. Fortschreibung)"


def test_happy_path_buybacks(db, company):
    """buyback_volume analog — auch 0-Quartale gehen in den Durchschnitt."""
    for q, v in (("Q1", 300), ("Q2", 0), ("Q3", 300)):
        _seed(db, company, "buyback_volume", q, v)

    written = derive_runrate_quarter(db, company.id, YEAR)
    db.commit()

    assert written == 1
    (q4,) = _rows(db, company, "buyback_volume", "Q4")
    assert q4.numeric_value == Decimal("200")
    (fy,) = _rows(db, company, "buyback_volume", "FY")
    assert fy.numeric_value == Decimal("800")


def test_fy_consensus_forecast_blocks_runrate(db, company):
    """FY-Konsens/Guidance-Forecast mit Wert vorhanden: der Q4-Rest-Pfad
    (derive_open_quarter) hat Vorrang — Runrate greift nicht."""
    for q, v in (("Q1", 90), ("Q2", 100), ("Q3", 110)):
        _seed(db, company, "sbc", q, v)
    fy_est = _seed(db, company, "sbc", "FY", 420, is_forecast=True,
                   primary_method="web_guidance")

    written = derive_runrate_quarter(db, company.id, YEAR)
    db.commit()

    assert written == 0
    assert _rows(db, company, "sbc", "Q4") == []
    db.refresh(fy_est)
    assert fy_est.numeric_value == Decimal("420")
    assert fy_est.source_name == "seed"


def test_manual_slot_stays(db, company):
    """Manuelle Zeilen des offenen Quartals sind authoritative."""
    for q, v in (("Q1", 90), ("Q2", 100), ("Q3", 110)):
        _seed(db, company, "sbc", q, v)
    manual = _seed(db, company, "sbc", "Q4", 500, is_forecast=True,
                   primary_method="manual", manually_overridden=True)

    written = derive_runrate_quarter(db, company.id, YEAR)
    db.commit()

    assert written == 0
    db.refresh(manual)
    assert manual.numeric_value == Decimal("500")
    assert manual.manually_overridden is True


def test_replaceable_estimate_overwritten(db, company):
    """Alte LLM-Schaetzung (two_stage_*) im offenen Slot ist ersetzbar."""
    for q, v in (("Q1", 90), ("Q2", 100), ("Q3", 110)):
        _seed(db, company, "buyback_volume", q, v)
    est = _seed(db, company, "buyback_volume", "Q4", 999, is_forecast=True,
                primary_method="two_stage_insufficient")

    written = derive_runrate_quarter(db, company.id, YEAR)
    db.commit()

    assert written == 1
    db.refresh(est)
    assert est.numeric_value == Decimal("100")
    assert est.primary_method == "calculated"


def test_more_than_one_open_quarter_skips(db, company):
    for q, v in (("Q1", 90), ("Q2", 100)):
        _seed(db, company, "sbc", q, v)

    assert derive_runrate_quarter(db, company.id, YEAR) == 0
    assert _rows(db, company, "sbc", "Q4") == []


def test_fy_actual_blocks_runrate(db, company):
    """Abgeschlossenes Jahr (FY-Actual mit Wert): kein Runrate-Fall."""
    for q, v in (("Q1", 90), ("Q2", 100), ("Q3", 110)):
        _seed(db, company, "sbc", q, v)
    _seed(db, company, "sbc", "FY", 400)

    assert derive_runrate_quarter(db, company.id, YEAR) == 0
    assert _rows(db, company, "sbc", "Q4") == []
