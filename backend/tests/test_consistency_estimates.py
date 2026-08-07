"""Vorjahres-Kopie- und Schaetzungs-Plausibilitaets-Checks.

Befunde aus dem DAX-Voll-Review, die diese Checks maschinell fangen:
- LLM-Recherche kopiert den FY-Wert des Vorjahres exakt ins neue Jahr
- Forecast-FY liegt >50% neben dem letzten Actual (grob implausibel)
- Umsatz-Schaetzung unter dem letzten Ist (veraltet gegenueber Guidance)
"""

from decimal import Decimal
from uuid import UUID

from app.auth.models import User
from app.auth.security import hash_password
from app.values.consistency import validate_cross_metrics
from app.values.models import CompanyValue


def _company(client, db, email):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]
    c = client.post(
        f"/api/portfolios/{pid}/companies",
        json={"name": "TestCo", "ticker": "TST", "currency": "EUR"},
    ).json()
    return UUID(c["id"])


def _seed(db, cid, key, period_type, value, year=2026, is_forecast=True, **kw):
    cv = CompanyValue(
        company_id=cid, value_key=key, period_type=period_type, period_year=year,
        numeric_value=Decimal(str(value)), source_name="seed", is_forecast=is_forecast, **kw,
    )
    db.add(cv)
    db.commit()
    return cv


def _row(db, cid, key, period_type, year=2026):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == cid, CompanyValue.value_key == key,
            CompanyValue.period_type == period_type, CompanyValue.period_year == year,
        )
        .one()
    )


# --- prior_year_copy ------------------------------------------------------


def test_prior_year_copy_flagged_on_llm_row(client, db):
    cid = _company(client, db, "pyc1@example.com")
    _seed(db, cid, "revenue", "FY", 24000e6, year=2025, is_forecast=False)
    _seed(db, cid, "revenue", "FY", 24000e6, year=2026,
          primary_method="two_stage_verified")
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    assert "prior_year_copy" in (_row(db, cid, "revenue", "FY").consistency_flags or "")


def test_prior_year_copy_manual_and_provider_not_flagged(client, db):
    cid = _company(client, db, "pyc2@example.com")
    _seed(db, cid, "revenue", "FY", 24000e6, year=2025, is_forecast=False)
    _seed(db, cid, "revenue", "FY", 24000e6, year=2026, primary_method="provider")
    _seed(db, cid, "net_income", "FY", 800e6, year=2025, is_forecast=False)
    _seed(db, cid, "net_income", "FY", 800e6, year=2026,
          manually_overridden=True, primary_method="manual")
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    assert _row(db, cid, "revenue", "FY").consistency_flags is None
    assert _row(db, cid, "net_income", "FY").consistency_flags is None


def test_prior_year_copy_zero_not_flagged(client, db):
    cid = _company(client, db, "pyc3@example.com")
    _seed(db, cid, "sbc", "FY", 0, year=2025, is_forecast=False)
    _seed(db, cid, "sbc", "FY", 0, year=2026, primary_method="two_stage_verified")
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    assert _row(db, cid, "sbc", "FY").consistency_flags is None


def test_prior_year_copy_cleared_when_value_changes(client, db):
    cid = _company(client, db, "pyc4@example.com")
    _seed(db, cid, "revenue", "FY", 24000e6, year=2025, is_forecast=False)
    row = _seed(db, cid, "revenue", "FY", 25100e6, year=2026,
                primary_method="two_stage_verified",
                consistency_flags="prior_year_copy")
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    db.refresh(row)
    assert row.consistency_flags is None


# --- estimate_jump --------------------------------------------------------


def test_estimate_jump_flagged_above_tolerance(client, db):
    cid = _company(client, db, "ej1@example.com")
    _seed(db, cid, "net_income", "FY", 1000e6, year=2025, is_forecast=False)
    _seed(db, cid, "net_income", "FY", 1600e6, year=2026, is_forecast=True)
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    assert "estimate_jump" in (_row(db, cid, "net_income", "FY").consistency_flags or "")


def test_estimate_jump_within_tolerance_not_flagged(client, db):
    cid = _company(client, db, "ej2@example.com")
    _seed(db, cid, "net_income", "FY", 1000e6, year=2025, is_forecast=False)
    _seed(db, cid, "net_income", "FY", 1400e6, year=2026, is_forecast=True)
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    assert _row(db, cid, "net_income", "FY").consistency_flags is None


def test_estimate_jump_sign_flip_not_flagged(client, db):
    """Vorzeichenwechsel (bei Cashflows legitim) wird nicht geflaggt."""
    cid = _company(client, db, "ej3@example.com")
    _seed(db, cid, "fcf", "FY", -2000e6, year=2025, is_forecast=False)
    _seed(db, cid, "fcf", "FY", 1000e6, year=2026, is_forecast=True)
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    assert _row(db, cid, "fcf", "FY").consistency_flags is None


def test_estimate_jump_manual_forecast_not_flagged(client, db):
    cid = _company(client, db, "ej4@example.com")
    _seed(db, cid, "net_income", "FY", 1000e6, year=2025, is_forecast=False)
    _seed(db, cid, "net_income", "FY", 1600e6, year=2026, is_forecast=True,
          manually_overridden=True)
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    assert _row(db, cid, "net_income", "FY").consistency_flags is None


def test_estimate_jump_cleared_when_estimate_fixed(client, db):
    cid = _company(client, db, "ej5@example.com")
    _seed(db, cid, "net_income", "FY", 1000e6, year=2025, is_forecast=False)
    row = _seed(db, cid, "net_income", "FY", 1100e6, year=2026, is_forecast=True,
                consistency_flags="estimate_jump")
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    db.refresh(row)
    assert row.consistency_flags is None


# --- estimate_below_prior -------------------------------------------------


def test_estimate_below_prior_revenue_flagged(client, db):
    cid = _company(client, db, "ebp1@example.com")
    _seed(db, cid, "revenue", "FY", 24000e6, year=2025, is_forecast=False)
    _seed(db, cid, "revenue", "FY", 23000e6, year=2026, is_forecast=True)
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    assert "estimate_below_prior" in (_row(db, cid, "revenue", "FY").consistency_flags or "")


def test_estimate_below_prior_other_key_not_flagged(client, db):
    """Nur revenue: sinkendes net_income ist eine legitime Schaetzung."""
    cid = _company(client, db, "ebp2@example.com")
    _seed(db, cid, "net_income", "FY", 1000e6, year=2025, is_forecast=False)
    _seed(db, cid, "net_income", "FY", 900e6, year=2026, is_forecast=True)
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    assert _row(db, cid, "net_income", "FY").consistency_flags is None


def test_estimate_below_prior_cleared_when_raised(client, db):
    cid = _company(client, db, "ebp3@example.com")
    _seed(db, cid, "revenue", "FY", 24000e6, year=2025, is_forecast=False)
    row = _seed(db, cid, "revenue", "FY", 25000e6, year=2026, is_forecast=True,
                consistency_flags="estimate_below_prior")
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    db.refresh(row)
    assert row.consistency_flags is None


# --- Flag-Merge -----------------------------------------------------------


def test_new_flags_merge_with_existing_flags(client, db):
    """Neue Flags duerfen bestehende nicht zerstoeren: _set_flag merged
    per Flag-Name (qsum_mismatch wird ohne Q-Zeilen nicht neu bewertet
    und muss stehen bleiben)."""
    cid = _company(client, db, "merge1@example.com")
    _seed(db, cid, "revenue", "FY", 24000e6, year=2025, is_forecast=False)
    row = _seed(db, cid, "revenue", "FY", 24000e6, year=2026,
                primary_method="two_stage_verified",
                consistency_flags="qsum_mismatch")
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    db.refresh(row)
    flags = set((row.consistency_flags or "").split(","))
    assert "qsum_mismatch" in flags
    assert "prior_year_copy" in flags
