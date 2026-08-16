"""_refresh_fy_from_quarters: Produktregel-Umkehr (User-Entscheid).

Sind alle 4 Quartale (bzw. Q4 bei POINT_IN_TIME) gefuellt, GEWINNEN die
Quartale — der FY wird IMMER aus ihnen neu berechnet und ueberschreibt
jeden bestehenden Direkt-/Provider-/Guidance-FY. Gesperrt bleiben nur
manuell ueberschriebene und from_ir_pdf-FY-Zeilen. Fehlen Quartale,
bleibt der FY-Direkt-Wert unangetastet (Fallback).

Kehrt die fruehere "authoritative FY report value beats quarter sums"-
Regel (Commit efbd81e) bewusst um.
"""
from decimal import Decimal

import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.values.models import CompanyValue
from app.values.routes import _refresh_fy_from_quarters

YEAR = 2026


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="fyagg@example.com", password_hash=hash_password("pw1234"))
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


def _seed(db, comp, ptype, value, key="revenue", is_forecast=False,
          year=YEAR, **kw):
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


def _fy(db, comp, key="revenue", year=YEAR):
    rows = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == year,
        )
        .all()
    )
    assert len(rows) == 1
    return rows[0]


def test_full_quarters_overwrite_provider_fy_actual(db, company):
    """Alle 4 Quartale + abweichender FY-Direktwert (provider,
    is_forecast=False): FY wird zur Quartalssumme ueberschrieben."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120), ("Q4", 130)):
        _seed(db, company, q, v)
    _seed(db, company, "FY", 500, primary_method="provider", is_forecast=False)

    _refresh_fy_from_quarters(db, company.id, "revenue", YEAR)
    db.commit()

    fy = _fy(db, company)
    assert fy.numeric_value == Decimal("460")
    assert fy.primary_method == "provider"
    assert fy.source_name.startswith("Derived Annual")


def test_full_quarters_overwrite_statement_research_fy_actual(db, company):
    """statement_research-FY-Actual verliert ebenfalls gegen die Summe."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120), ("Q4", 130)):
        _seed(db, company, q, v)
    _seed(db, company, "FY", 999, primary_method="statement_research",
          is_forecast=False)

    _refresh_fy_from_quarters(db, company.id, "revenue", YEAR)
    db.commit()

    assert _fy(db, company).numeric_value == Decimal("460")


def test_full_quarters_overwrite_forecast_fy(db, company):
    """FY-Guidance-Forecast (web_guidance, is_forecast=True) wird ebenfalls
    von der Quartalssumme ueberschrieben."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120), ("Q4", 130)):
        _seed(db, company, q, v)
    _seed(db, company, "FY", 500, primary_method="web_guidance",
          is_forecast=True)

    _refresh_fy_from_quarters(db, company.id, "revenue", YEAR)
    db.commit()

    assert _fy(db, company).numeric_value == Decimal("460")


def test_manual_fy_stays(db, company):
    """Manuell ueberschriebene FY-Zeile bleibt gesperrt."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120), ("Q4", 130)):
        _seed(db, company, q, v)
    _seed(db, company, "FY", 500, primary_method="manual",
          manually_overridden=True)

    _refresh_fy_from_quarters(db, company.id, "revenue", YEAR)
    db.commit()

    assert _fy(db, company).numeric_value == Decimal("500")


def test_from_ir_pdf_fy_stays(db, company):
    """from_ir_pdf-FY (authoritatives Berichtsdokument) bleibt gesperrt."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120), ("Q4", 130)):
        _seed(db, company, q, v)
    _seed(db, company, "FY", 500, primary_method="pdf", from_ir_pdf=True)

    _refresh_fy_from_quarters(db, company.id, "revenue", YEAR)
    db.commit()

    assert _fy(db, company).numeric_value == Decimal("500")


def test_incomplete_quarters_keep_fy_direct(db, company):
    """Fehlt ein Quartal, bleibt der FY-Direktwert unangetastet."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, q, v)
    _seed(db, company, "FY", 500, primary_method="provider")

    _refresh_fy_from_quarters(db, company.id, "revenue", YEAR)
    db.commit()

    assert _fy(db, company).numeric_value == Decimal("500")


def test_point_in_time_fy_equals_q4(db, company):
    """POINT_IN_TIME-Key (net_debt): FY = Q4-Stichtag, nicht die Summe."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120), ("Q4", 130)):
        _seed(db, company, q, v, key="net_debt")
    _seed(db, company, "FY", 999, key="net_debt", primary_method="provider")

    _refresh_fy_from_quarters(db, company.id, "net_debt", YEAR)
    db.commit()

    assert _fy(db, company, key="net_debt").numeric_value == Decimal("130")


def test_guidance_fy_400_overwritten_by_quarter_sum_425(db, company):
    """Dynatrace-Fall: FY-Guidance 400M vs Summe der Quartals-Schaetzungen
    425M -> FY soll 425M (=Summe) werden, nicht 400M."""
    for q, v in (
        ("Q1", 100_000_000),
        ("Q2", 105_000_000),
        ("Q3", 110_000_000),
        ("Q4", 110_000_000),
    ):
        _seed(db, company, q, v, key="buyback_volume", is_forecast=True,
              primary_method="web_guidance")
    _seed(db, company, "FY", 400_000_000, key="buyback_volume",
          is_forecast=True, primary_method="web_guidance")

    _refresh_fy_from_quarters(db, company.id, "buyback_volume", YEAR)
    db.commit()

    assert _fy(db, company, key="buyback_volume").numeric_value == Decimal("425000000")


def test_protected_adjusted_survives_overwrite(db, company):
    """GAAP-FY wird ueberschrieben, ein geschuetzter Adjusted-Beleg
    (adjustments_source='Manual') bleibt aber erhalten."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120), ("Q4", 130)):
        _seed(db, company, q, v)
    _seed(db, company, "FY", 500, primary_method="provider",
          numeric_value_adjusted=Decimal("777"),
          adjustments_note="Manuell", adjustments_source="Manual")

    _refresh_fy_from_quarters(db, company.id, "revenue", YEAR)
    db.commit()

    fy = _fy(db, company)
    assert fy.numeric_value == Decimal("460")
    assert fy.numeric_value_adjusted == Decimal("777")
    assert fy.adjustments_source == "Manual"
