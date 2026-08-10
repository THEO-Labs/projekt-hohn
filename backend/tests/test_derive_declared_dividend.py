"""derive_declared_dividend_quarter: das offene Dividenden-Quartal des
laufenden FY wird aus der zuletzt berichteten Quartalsrate fortgeschrieben
(Visa-Muster: Rate aendert sich nur einmal pro FY). Nur leere/ersetzbare
Slots; manual/PDF bleiben. Sind damit alle 4 Quartale voll, zieht
_refresh_fy_from_quarters FY = Summe nach."""
from decimal import Decimal

import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.values.consistency import derive_declared_dividend_quarter
from app.values.models import CompanyValue

YEAR = 2026


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="divq@example.com", password_hash=hash_password("pw1234"))
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


def _seed(db, comp, ptype, value, is_forecast=False, year=YEAR, key="dividends", **kw):
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


def _rows(db, comp, ptype, year=YEAR):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key == "dividends",
            CompanyValue.period_type == ptype,
            CompanyValue.period_year == year,
        )
        .order_by(CompanyValue.is_forecast.asc())
        .all()
    )


def test_happy_path_carries_declared_rate_and_refreshes_fy(db, company):
    """Q1-Q3 berichtet (Rate zuletzt 110): Q4 = 110 als calculated-Forecast,
    FY = Summe ueber den bestehenden Mechanismus."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 110)):
        _seed(db, company, q, v)

    written = derive_declared_dividend_quarter(db, company.id, YEAR)
    db.commit()

    assert written == 1
    (q4,) = _rows(db, company, "Q4")
    assert q4.numeric_value == Decimal("110")
    assert q4.is_forecast is True
    assert q4.primary_method == "calculated"
    assert q4.source_name.startswith("Fortschreibung deklarierte Quartalsdividende")
    assert q4.currency == "USD"
    (fy,) = _rows(db, company, "FY")
    assert fy.numeric_value == Decimal("430")


def test_zero_dividend_carries_zero(db, company):
    """Firma ohne Dividende: 0 -> 0 ist korrekt (keine Sonderbehandlung)."""
    for q in ("Q1", "Q2", "Q3"):
        _seed(db, company, q, 0)

    written = derive_declared_dividend_quarter(db, company.id, YEAR)
    db.commit()

    assert written == 1
    (q4,) = _rows(db, company, "Q4")
    assert q4.numeric_value == Decimal("0")
    (fy,) = _rows(db, company, "FY")
    assert fy.numeric_value == Decimal("0")


def test_replaceable_estimate_slot_overwritten(db, company):
    """Alte LLM-Schaetzung (web_guidance) im Q4-Slot ist ersetzbar."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 110)):
        _seed(db, company, q, v)
    est = _seed(db, company, "Q4", 999, is_forecast=True,
                primary_method="web_guidance")

    written = derive_declared_dividend_quarter(db, company.id, YEAR)
    db.commit()

    assert written == 1
    db.refresh(est)
    assert est.numeric_value == Decimal("110")
    assert est.primary_method == "calculated"


def test_occupied_pdf_slot_stays(db, company):
    """PDF-belegter Slot (from_ir_pdf mit Wert) ist authoritative."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 110)):
        _seed(db, company, q, v)
    pdf = _seed(db, company, "Q4", 120, is_forecast=True,
                primary_method="pdf", from_ir_pdf=True)

    written = derive_declared_dividend_quarter(db, company.id, YEAR)
    db.commit()

    assert written == 0
    db.refresh(pdf)
    assert pdf.numeric_value == Decimal("120")
    assert pdf.primary_method == "pdf"


def test_manual_slot_stays(db, company):
    """Manuelle Zeilen bleiben unangetastet."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 110)):
        _seed(db, company, q, v)
    manual = _seed(db, company, "Q4", 200, is_forecast=True,
                   primary_method="manual", manually_overridden=True)

    written = derive_declared_dividend_quarter(db, company.id, YEAR)
    db.commit()

    assert written == 0
    db.refresh(manual)
    assert manual.numeric_value == Decimal("200")
    assert manual.manually_overridden is True


def test_more_than_one_open_quarter_skips(db, company):
    """Zwei offene Quartale: keine Fortschreibung."""
    for q, v in (("Q1", 100), ("Q2", 110)):
        _seed(db, company, q, v)

    assert derive_declared_dividend_quarter(db, company.id, YEAR) == 0
    assert _rows(db, company, "Q3") == []
    assert _rows(db, company, "Q4") == []


def test_fy_actual_blocks_rate_carry(db, company):
    """Abgeschlossenes Jahr (FY-Actual): das exakte Residuum ist Sache
    anderer Ableitungen — keine Raten-Fortschreibung."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 110)):
        _seed(db, company, q, v)
    _seed(db, company, "FY", 430)

    assert derive_declared_dividend_quarter(db, company.id, YEAR) == 0
    assert _rows(db, company, "Q4") == []


def test_reported_quarter_not_carried(db, company):
    """US-Filer, Q4 beendet und Karenz abgelaufen: das Quartal gilt als
    berichtet — das echte Actual kommt per Bruecke/XBRL, keine
    Fortschreibung darueber."""
    from datetime import date, timedelta

    p_end = date.today() - timedelta(days=60)
    company.fiscal_year_end_month = p_end.month
    company.fiscal_year_end_day = p_end.day
    db.commit()
    year = p_end.year
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 110)):
        _seed(db, company, q, v, year=year)

    assert derive_declared_dividend_quarter(db, company.id, year) == 0
    assert _rows(db, company, "Q4", year=year) == []
