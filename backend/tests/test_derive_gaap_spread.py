"""derive_gaap_from_adjusted_spread: GAAP-EPS/NI-Schaetzungen werden aus
der Non-GAAP-Schaetzung abgeleitet — Spread = Durchschnitt (adjusted - gaap)
der berichteten Quartale (beide Spuren). Offenes Quartal: gaap = adjusted -
Spread; FY = Summe der berichteten GAAP-Quartale + abgeleitetes Quartal
(die berichteten Quartale sind exakt). eps auf 2 Nachkommastellen,
net_income ganzzahlig. Nur adjusted-only-/ersetzbare Slots; manual/PDF
bleiben; 0 < gaap < adjusted als Gate."""
from decimal import Decimal

import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.values.consistency import derive_gaap_from_adjusted_spread
from app.values.models import CompanyValue

YEAR = 2026


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="spread@example.com", password_hash=hash_password("pw1234"))
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


def _fc_row(db, comp, key, ptype):
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


def _seed_visa_eps(db, company):
    """Visa-FY2026-Muster: Spreads 0.14/0.17/0.35 -> 0.22; GAAP-Summe 9.14."""
    for q, g, a in (("Q1", "3.00", "3.14"), ("Q2", "3.02", "3.19"),
                    ("Q3", "3.12", "3.47")):
        _seed(db, company, "eps_diluted", q, g, adjusted=a)
    q4 = _seed(db, company, "eps_diluted", "Q4", None, adjusted="3.33",
               is_forecast=True, primary_method="calculated")
    fy = _seed(db, company, "eps_diluted", "FY", None, adjusted="13.13",
               is_forecast=True, primary_method="web_guidance")
    return q4, fy


def test_happy_path_eps(db, company):
    """Beispiel-Erwartung: Spread 0.22, Q4 gaap = 3.33 - 0.22 = 3.11,
    FY gaap = 9.14 + 3.11 = 12.25."""
    q4, fy = _seed_visa_eps(db, company)

    written = derive_gaap_from_adjusted_spread(db, company.id, YEAR)
    db.commit()

    assert written == 2
    db.refresh(q4)
    assert q4.numeric_value == Decimal("3.11")
    assert q4.primary_method == "calculated"
    assert q4.numeric_value_adjusted == Decimal("3.33")
    assert "(0.22)" in q4.source_name
    assert q4.source_name.startswith(
        "Abgeleitet: Non-GAAP-Schaetzung minus mittlerer GAAP/Non-GAAP-Abstand"
    )
    db.refresh(fy)
    assert fy.numeric_value == Decimal("12.25")
    assert fy.primary_method == "calculated"


def test_happy_path_net_income(db, company):
    """NI analog: Spreads 271/321/668 Mio -> 420 Mio; Q4 gaap =
    5696319908 - 420000000 = 5276319908 (ganzzahlig)."""
    for q, g, a in (
        ("Q1", 4700000000, 4971000000),
        ("Q2", 4900000000, 5221000000),
        ("Q3", 5000000000, 5668000000),
    ):
        _seed(db, company, "net_income", q, g, adjusted=a)
    q4 = _seed(db, company, "net_income", "Q4", None, adjusted=5696319908,
               is_forecast=True, primary_method="calculated")
    fy = _seed(db, company, "net_income", "FY", None, adjusted=21556319908,
               is_forecast=True, primary_method="web_guidance")

    written = derive_gaap_from_adjusted_spread(db, company.id, YEAR)
    db.commit()

    assert written == 2
    db.refresh(q4)
    assert q4.numeric_value == Decimal("5276319908")
    assert "(420000000)" in q4.source_name
    db.refresh(fy)
    # 4700000000 + 4900000000 + 5000000000 + 5276319908
    assert fy.numeric_value == Decimal("19876319908")


def test_too_few_reported_quarters_skips(db, company):
    """Nur EIN berichtetes Quartal mit beiden Spuren: keine Spread-Basis."""
    _seed(db, company, "eps_diluted", "Q1", "3.00", adjusted="3.14")
    _seed(db, company, "eps_diluted", "Q2", "3.02")
    q4 = _seed(db, company, "eps_diluted", "Q4", None, adjusted="3.33",
               is_forecast=True, primary_method="calculated")

    assert derive_gaap_from_adjusted_spread(db, company.id, YEAR) == 0
    db.refresh(q4)
    assert q4.numeric_value is None


def test_manual_slot_stays(db, company):
    """Manuelle Zeilen des Zielquartals sind authoritative."""
    for q, g, a in (("Q1", "3.00", "3.14"), ("Q2", "3.02", "3.19"),
                    ("Q3", "3.12", "3.47")):
        _seed(db, company, "eps_diluted", q, g, adjusted=a)
    manual = _seed(db, company, "eps_diluted", "Q4", None, adjusted="3.33",
                   is_forecast=True, primary_method="manual",
                   manually_overridden=True)

    assert derive_gaap_from_adjusted_spread(db, company.id, YEAR) == 0
    db.refresh(manual)
    assert manual.numeric_value is None
    assert manual.manually_overridden is True


def test_gaap_must_stay_below_adjusted(db, company):
    """Negativer Spread (adjusted < gaap in den Actuals): das abgeleitete
    gaap laege UEBER adjusted — 0<gaap<adjusted-Gate blockt, kein Write."""
    for q, g, a in (("Q1", "3.20", "3.00"), ("Q2", "3.30", "3.10"),
                    ("Q3", "3.40", "3.20")):
        _seed(db, company, "eps_diluted", q, g, adjusted=a)
    q4 = _seed(db, company, "eps_diluted", "Q4", None, adjusted="3.33",
               is_forecast=True, primary_method="calculated")

    assert derive_gaap_from_adjusted_spread(db, company.id, YEAR) == 0
    db.refresh(q4)
    assert q4.numeric_value is None


def test_existing_gaap_consensus_not_overwritten(db, company):
    """Ein echter GAAP-Konsens (web_guidance mit Wert) im Q4-Slot bleibt —
    die Ableitung fuellt nur adjusted-only-Slots."""
    for q, g, a in (("Q1", "3.00", "3.14"), ("Q2", "3.02", "3.19"),
                    ("Q3", "3.12", "3.47")):
        _seed(db, company, "eps_diluted", q, g, adjusted=a)
    est = _seed(db, company, "eps_diluted", "Q4", "3.05", adjusted="3.33",
                is_forecast=True, primary_method="web_guidance")

    assert derive_gaap_from_adjusted_spread(db, company.id, YEAR) == 0
    db.refresh(est)
    assert est.numeric_value == Decimal("3.05")
    assert est.primary_method == "web_guidance"


def test_idempotent_second_run_overwrites_own_values(db, company):
    """Zweiter Lauf ueberschreibt die eigenen calculated-Werte konsistent —
    auch wenn sich die adjusted-Schaetzung zwischenzeitlich aendert."""
    q4, fy = _seed_visa_eps(db, company)

    assert derive_gaap_from_adjusted_spread(db, company.id, YEAR) == 2
    db.commit()
    # Gleiche Inputs -> gleiche Werte.
    assert derive_gaap_from_adjusted_spread(db, company.id, YEAR) == 2
    db.commit()
    db.refresh(q4)
    db.refresh(fy)
    assert q4.numeric_value == Decimal("3.11")
    assert fy.numeric_value == Decimal("12.25")

    # Neue adjusted-Schaetzung -> neue Ableitung auf denselben Zeilen.
    q4.numeric_value_adjusted = Decimal("3.43")
    db.commit()
    assert derive_gaap_from_adjusted_spread(db, company.id, YEAR) == 2
    db.commit()
    db.refresh(q4)
    db.refresh(fy)
    assert q4.numeric_value == Decimal("3.21")
    assert fy.numeric_value == Decimal("12.35")
