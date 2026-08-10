"""derive_balance_carry_forward: Point-in-Time-Bilanz-Keys
(cash_and_equivalents, st_investments, st_debt, lt_debt) — unberichtete
Q4-/FY-Slots werden mit dem letzten berichteten Quartalsstichtag
fortgeschrieben (is_forecast=True, calculated). Ersetzbare Slots
(two_stage_*/not_found/leer) werden ueberschrieben, manual/PDF bleiben.
net_debt rechnet danach derive_net_debt_from_components neu."""
from decimal import Decimal

import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.values.consistency import (
    derive_balance_carry_forward,
    derive_net_debt_from_components,
)
from app.values.models import CompanyValue

YEAR = 2026


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="carryfwd@example.com", password_hash=hash_password("pw1234"))
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


def test_q4_and_fy_from_q3(db, company):
    """Happy Path: Q3-Stichtag berichtet -> Q4- und FY-Slot als
    calculated-Forecast fortgeschrieben."""
    _seed(db, company, "cash_and_equivalents", "Q3", 500)

    written = derive_balance_carry_forward(db, company.id, YEAR)
    db.commit()

    assert written == 2
    for pt in ("Q4", "FY"):
        (row,) = _rows(db, company, "cash_and_equivalents", pt)
        assert row.numeric_value == Decimal("500")
        assert row.is_forecast is True
        assert row.primary_method == "calculated"
        assert row.source_name == "Fortschreibung letzter Bilanzstichtag (Q3)"
        assert row.currency == "USD"


def test_latest_reported_quarter_wins(db, company):
    """Mehrere berichtete Stichtage: der juengste (Q3) gewinnt."""
    _seed(db, company, "lt_debt", "Q1", 400)
    _seed(db, company, "lt_debt", "Q3", 380)

    written = derive_balance_carry_forward(db, company.id, YEAR)
    db.commit()

    assert written == 2
    (q4,) = _rows(db, company, "lt_debt", "Q4")
    assert q4.numeric_value == Decimal("380")
    assert "(Q3)" in q4.source_name


def test_two_stage_estimates_replaced(db, company):
    """two_stage-Bilanz-Schaetzungen sind mit der Fortschreibung obsolet —
    Forecast- wie Actual-Slot-Reste werden ueberschrieben."""
    _seed(db, company, "st_debt", "Q3", 50)
    stale_q4 = _seed(db, company, "st_debt", "Q4", 999,
                     primary_method="two_stage_confirmed")
    stale_fy = _seed(db, company, "st_debt", "FY", 888, is_forecast=True,
                     primary_method="two_stage_insufficient")

    written = derive_balance_carry_forward(db, company.id, YEAR)
    db.commit()

    assert written == 2
    db.refresh(stale_q4)
    db.refresh(stale_fy)
    assert stale_q4.numeric_value == Decimal("50")
    assert stale_q4.primary_method == "calculated"
    assert stale_q4.is_forecast is True
    assert stale_fy.numeric_value == Decimal("50")
    assert stale_fy.primary_method == "calculated"


def test_manual_and_pdf_slots_stay(db, company):
    """Manuelle und PDF-Zeilen sind authoritative — kein Carry-Forward
    in deren Slot; der jeweils andere Slot wird trotzdem gefuellt."""
    _seed(db, company, "cash_and_equivalents", "Q3", 500)
    manual = _seed(db, company, "cash_and_equivalents", "Q4", 555,
                   is_forecast=True, primary_method="manual",
                   manually_overridden=True)
    _seed(db, company, "st_investments", "Q3", 100)
    pdf = _seed(db, company, "st_investments", "FY", 111,
                primary_method="pdf", from_ir_pdf=True)

    written = derive_balance_carry_forward(db, company.id, YEAR)
    db.commit()

    # cash: nur FY; st_investments: nur Q4.
    assert written == 2
    db.refresh(manual)
    assert manual.numeric_value == Decimal("555")
    db.refresh(pdf)
    assert pdf.numeric_value == Decimal("111")
    (cash_fy,) = _rows(db, company, "cash_and_equivalents", "FY")
    assert cash_fy.numeric_value == Decimal("500")
    (sti_q4,) = _rows(db, company, "st_investments", "Q4")
    assert sti_q4.numeric_value == Decimal("100")


def test_reported_q4_actual_feeds_fy_only(db, company):
    """Q4 selbst berichtet (Provider-Actual): Q4 bleibt, nur der leere
    FY-Slot wird vom juengsten Stichtag (Q4) gefuellt."""
    _seed(db, company, "lt_debt", "Q3", 400)
    q4 = _seed(db, company, "lt_debt", "Q4", 380)

    written = derive_balance_carry_forward(db, company.id, YEAR)
    db.commit()

    assert written == 1
    db.refresh(q4)
    assert q4.numeric_value == Decimal("380")
    assert q4.primary_method == "provider"
    assert q4.is_forecast is False
    (fy,) = _rows(db, company, "lt_debt", "FY")
    assert fy.numeric_value == Decimal("380")
    assert "(Q4)" in fy.source_name


def test_no_reported_quarter_noop(db, company):
    """Ohne berichteten Stichtag gibt es nichts fortzuschreiben."""
    assert derive_balance_carry_forward(db, company.id, YEAR) == 0


def test_net_debt_recomputed_from_carried_components(db, company):
    """Integration: alle vier Komponenten aus Q3 fortgeschrieben, danach
    rechnet derive_net_debt_from_components net_debt Q4/FY als Forecast."""
    _seed(db, company, "cash_and_equivalents", "Q3", 500)
    _seed(db, company, "st_investments", "Q3", 100)
    _seed(db, company, "st_debt", "Q3", 50)
    _seed(db, company, "lt_debt", "Q3", 400)
    stale_nd = _seed(db, company, "net_debt", "Q4", 999, is_forecast=True,
                     primary_method="two_stage_confirmed")

    written = derive_balance_carry_forward(db, company.id, YEAR)
    derive_net_debt_from_components(db, company.id, YEAR)
    db.commit()

    assert written == 8
    db.refresh(stale_nd)
    # 50 + 400 - 500 - 100 = -150, Forecast weil die Komponenten
    # fortgeschriebene Forecasts sind.
    assert stale_nd.numeric_value == Decimal("-150")
    assert stale_nd.primary_method == "calculated"
    assert stale_nd.is_forecast is True
    fy_nd = _rows(db, company, "net_debt", "FY")
    assert len(fy_nd) == 1
    assert fy_nd[0].numeric_value == Decimal("-150")
    assert fy_nd[0].is_forecast is True
