"""derive_open_quarter_from_fy_estimate, adjusted-only-Anker: hat der
FY-Forecast KEINEN GAAP-Wert (numeric_value=None), aber einen adjusted-Wert,
wird der GAAP-Pfad uebersprungen und nur das adjusted-Residuum gerechnet
(FY_adj - Sigma Q_adj der berichteten Quartale). Die Orphan-Raeumung greift
NUR, wenn weder GAAP- noch adjusted-Anker existieren."""
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
    user = User(email="adjonly@example.com", password_hash=hash_password("pw1234"))
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


def test_adjusted_only_anchor_writes_adjusted_residual(db, company):
    """FY nur adjusted (Non-GAAP-Konsens ohne GAAP-Basis): Q4 bekommt das
    adjusted-Residuum, die GAAP-Spur bleibt leer."""
    for q, v, a in (("Q1", 100, 105), ("Q2", 110, 115), ("Q3", 120, 125)):
        _seed(db, company, "net_income", q, v, adjusted=a)
    _seed(db, company, "net_income", "FY", None, adjusted=480,
          is_forecast=True, primary_method="web_guidance")

    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 1
    row = _open_q_row(db, company, "net_income", "Q4")
    assert row is not None
    assert row.numeric_value is None
    assert row.numeric_value_adjusted == Decimal("135")
    assert row.adjustments_note == (
        "Abgeleitet: FY-Schaetzung minus berichtete Quartale"
    )
    assert row.adjustments_source is None
    assert row.primary_method == "calculated"


def test_existing_gaap_estimate_row_untouched_on_gaap_track(db, company):
    """Bestehende Q4-Forecast-Zeile mit GAAP-Schaetzwert: GAAP-Wert und
    primary_method bleiben, nur die adjusted-Spur wird gesetzt."""
    for q, v, a in (("Q1", 100, 105), ("Q2", 110, 115), ("Q3", 120, 125)):
        _seed(db, company, "net_income", q, v, adjusted=a)
    est = _seed(db, company, "net_income", "Q4", 999, is_forecast=True,
                primary_method="web_guidance")
    _seed(db, company, "net_income", "FY", None, adjusted=480, is_forecast=True)

    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 1
    db.refresh(est)
    assert est.numeric_value == Decimal("999")
    assert est.primary_method == "web_guidance"
    assert est.numeric_value_adjusted == Decimal("135")
    assert est.adjustments_note == (
        "Abgeleitet: FY-Schaetzung minus berichtete Quartale"
    )


def test_orphan_cleanup_not_triggered_by_adjusted_anchor(db, company):
    """Ein altes GAAP-Residuum bleibt stehen, solange ein adjusted-Anker
    existiert — die Orphan-Raeumung braucht das Fehlen BEIDER Anker."""
    for q, v, a in (("Q1", 100, 105), ("Q2", 110, 115), ("Q3", 120, 125)):
        _seed(db, company, "net_income", q, v, adjusted=a)
    residual = _seed(
        db, company, "net_income", "Q4", 130, is_forecast=True,
        primary_method="calculated",
        source_name="FY-Guidance minus berichtete Quartale: FY 460 - ...",
    )
    _seed(db, company, "net_income", "FY", None, adjusted=480, is_forecast=True)

    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 1
    db.refresh(residual)
    assert residual.numeric_value == Decimal("130")
    assert residual.numeric_value_adjusted == Decimal("135")


def test_orphan_cleanup_still_fires_without_any_anchor(db, company):
    """Kontrolle: ohne FY-Forecast (weder GAAP noch adjusted) raeumt die
    Hygiene das verwaiste Residuum weiterhin."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "net_income", q, v)
    residual = _seed(
        db, company, "net_income", "Q4", 130, is_forecast=True,
        primary_method="calculated",
        source_name="FY-Guidance minus berichtete Quartale: FY 460 - ...",
    )

    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 0
    db.refresh(residual)
    assert residual.numeric_value is None
    assert residual.source_name == "Geraeumt: FY-Anker der Ableitung entfallen"


def test_missing_quarter_adjusted_blocks_adjusted_only_derivation(db, company):
    """Fehlt einem Quartal der adjusted-Wert, sind zwei Quartale 'offen'
    im adjusted-Sinn — kein eindeutiges Residuum, kein Write."""
    _seed(db, company, "net_income", "Q1", 100, adjusted=105)
    _seed(db, company, "net_income", "Q2", 110)
    _seed(db, company, "net_income", "Q3", 120, adjusted=125)
    _seed(db, company, "net_income", "FY", None, adjusted=480, is_forecast=True)

    assert derive_open_quarter_from_fy_estimate(db, company.id, YEAR) == 0
    assert _open_q_row(db, company, "net_income", "Q4") is None


def test_negative_adjusted_residual_always_positive_skipped(db, company):
    """dividends (always positive): negatives adjusted-Residuum wird nicht
    persistiert."""
    for q in ("Q1", "Q2", "Q3"):
        _seed(db, company, "dividends", q, 100, adjusted=100)
    _seed(db, company, "dividends", "FY", None, adjusted=250, is_forecast=True)

    assert derive_open_quarter_from_fy_estimate(db, company.id, YEAR) == 0
    assert _open_q_row(db, company, "dividends", "Q4") is None


def test_protected_adjusted_stays(db, company):
    """Manueller Adjusted-Override auf der Q4-Zeile bleibt unangetastet."""
    for q, v, a in (("Q1", 100, 105), ("Q2", 110, 115), ("Q3", 120, 125)):
        _seed(db, company, "net_income", q, v, adjusted=a)
    est = _seed(db, company, "net_income", "Q4", None, adjusted=140,
                is_forecast=True, primary_method="web_guidance",
                adjustments_note="Manuell gesetzt", adjustments_source="Manual")
    _seed(db, company, "net_income", "FY", None, adjusted=480, is_forecast=True)

    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 0
    db.refresh(est)
    assert est.numeric_value_adjusted == Decimal("140")
    assert est.adjustments_source == "Manual"


def test_manual_slot_blocks_adjusted_only_derivation(db, company):
    """Manuelle Zeilen des offenen Quartals sind authoritative."""
    for q, v, a in (("Q1", 100, 105), ("Q2", 110, 115), ("Q3", 120, 125)):
        _seed(db, company, "net_income", q, v, adjusted=a)
    manual = _seed(db, company, "net_income", "Q4", 200, adjusted=210,
                   is_forecast=True, primary_method="manual",
                   manually_overridden=True)
    _seed(db, company, "net_income", "FY", None, adjusted=480, is_forecast=True)

    assert derive_open_quarter_from_fy_estimate(db, company.id, YEAR) == 0
    db.refresh(manual)
    assert manual.numeric_value_adjusted == Decimal("210")
