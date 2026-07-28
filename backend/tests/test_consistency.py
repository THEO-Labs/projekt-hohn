"""Cross-Metrik-Validator + net_debt-Ableitung aus Komponenten.

Befunde aus den adidas-Laeufen, die diese Checks maschinell fangen:
- OCF-FY 751 vs FCF 2800 + CapEx 500 (sollte ~3300 sein)
- OCF-Q-Reihe als Stale/Neu-Mix mit Summe != FY
- net_debt 5475 ('adjusted net borrowings') vs Komponenten-Summe 4681
- eps x shares (1663M) vs net_income (764M) im zweiten Lauf
"""

from decimal import Decimal
from uuid import UUID

from app.auth.models import User
from app.auth.security import hash_password
from app.values.consistency import derive_net_debt_from_components, validate_cross_metrics
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


def test_fcf_vs_ocf_capex_mismatch_flagged(client, db):
    cid = _company(client, db, "c1@example.com")
    _seed(db, cid, "operating_cash_flow", "FY", 751e6)
    _seed(db, cid, "capex", "FY", 500e6)
    _seed(db, cid, "fcf", "FY", 2800e6)
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    assert "fcf_vs_ocf_capex" in (_row(db, cid, "fcf", "FY").consistency_flags or "")


def test_fcf_consistent_clears_flag(client, db):
    cid = _company(client, db, "c2@example.com")
    _seed(db, cid, "operating_cash_flow", "FY", 3300e6)
    _seed(db, cid, "capex", "FY", 500e6)
    fcf = _seed(db, cid, "fcf", "FY", 2800e6, consistency_flags="fcf_vs_ocf_capex")
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    db.refresh(fcf)
    assert fcf.consistency_flags is None


def test_qsum_mismatch_flagged_on_fy_row(client, db):
    cid = _company(client, db, "c3@example.com")
    for q, v in (("Q1", -1100e6), ("Q2", 970e6), ("Q3", -800e6), ("Q4", 2200e6)):
        _seed(db, cid, "operating_cash_flow", q, v)
    _seed(db, cid, "operating_cash_flow", "FY", 751e6)
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    assert "qsum_mismatch" in (_row(db, cid, "operating_cash_flow", "FY").consistency_flags or "")


def test_eps_times_shares_vs_net_income_flagged(client, db):
    cid = _company(client, db, "c4@example.com")
    _seed(db, cid, "eps_diluted", "FY", 9.34)
    _seed(db, cid, "shares_outstanding", "SNAPSHOT", 178e6, year=None)
    _seed(db, cid, "net_income", "FY", 764e6)
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    assert "eps_ni_mismatch" in (_row(db, cid, "net_income", "FY").consistency_flags or "")


def test_derive_net_debt_overwrites_extracted_value(client, db):
    cid = _company(client, db, "c5@example.com")
    _seed(db, cid, "st_debt", "FY", 1248e6)
    _seed(db, cid, "lt_debt", "FY", 4306e6)
    _seed(db, cid, "cash_and_equivalents", "FY", 873e6)
    _seed(db, cid, "st_investments", "FY", 0)
    _seed(db, cid, "net_debt", "FY", 5475e6)
    derive_net_debt_from_components(db, cid, 2026)
    db.commit()
    row = _row(db, cid, "net_debt", "FY")
    assert row.numeric_value == Decimal("4681000000")
    assert row.primary_method == "calculated"
    assert "st_debt" in (row.source_name or "")


def test_derive_net_debt_respects_manual_and_pdf(client, db):
    cid = _company(client, db, "c6@example.com")
    _seed(db, cid, "st_debt", "FY", 1248e6)
    _seed(db, cid, "lt_debt", "FY", 4306e6)
    _seed(db, cid, "cash_and_equivalents", "FY", 873e6)
    _seed(db, cid, "st_investments", "FY", 0)
    _seed(db, cid, "net_debt", "FY", 5475e6, manually_overridden=True)
    derive_net_debt_from_components(db, cid, 2026)
    db.commit()
    assert _row(db, cid, "net_debt", "FY").numeric_value == Decimal("5475000000")


def test_derive_net_debt_skips_when_component_missing(client, db):
    cid = _company(client, db, "c7@example.com")
    _seed(db, cid, "st_debt", "FY", 1248e6)
    _seed(db, cid, "net_debt", "FY", 5475e6)
    derive_net_debt_from_components(db, cid, 2026)
    db.commit()
    assert _row(db, cid, "net_debt", "FY").numeric_value == Decimal("5475000000")


def test_derive_missing_ocf_from_fcf_and_capex(client, db):
    from app.values.consistency import derive_missing_ocf
    cid = _company(client, db, "c8@example.com")
    _seed(db, cid, "fcf", "FY", 2200e6)
    _seed(db, cid, "capex", "FY", 500e6)
    derive_missing_ocf(db, cid, 2026)
    db.commit()
    row = _row(db, cid, "operating_cash_flow", "FY")
    assert row.numeric_value == Decimal("2700000000")
    assert row.primary_method == "calculated"


def test_derive_missing_ocf_never_overwrites(client, db):
    from app.values.consistency import derive_missing_ocf
    cid = _company(client, db, "c9@example.com")
    _seed(db, cid, "fcf", "FY", 2200e6)
    _seed(db, cid, "capex", "FY", 500e6)
    _seed(db, cid, "operating_cash_flow", "FY", 2518e6)
    derive_missing_ocf(db, cid, 2026)
    db.commit()
    assert _row(db, cid, "operating_cash_flow", "FY").numeric_value == Decimal("2518000000")


def test_derive_sbc_quarters_splits_fy_evenly(client, db):
    from app.values.consistency import derive_sbc_quarters
    cid = _company(client, db, "c10@example.com")
    _seed(db, cid, "sbc", "FY", 94e6, is_forecast=False)
    derive_sbc_quarters(db, cid, 2026)
    db.commit()
    for q in ("Q1", "Q2", "Q3", "Q4"):
        row = _row(db, cid, "sbc", q)
        assert row.numeric_value == Decimal("23500000")
        assert row.primary_method == "calculated"


def test_derive_sbc_quarters_keeps_reported_quarters(client, db):
    from app.values.consistency import derive_sbc_quarters
    cid = _company(client, db, "c11@example.com")
    _seed(db, cid, "sbc", "FY", 100e6)
    _seed(db, cid, "sbc", "Q1", 40e6)
    derive_sbc_quarters(db, cid, 2026)
    db.commit()
    assert _row(db, cid, "sbc", "Q1").numeric_value == Decimal("40000000")
    assert _row(db, cid, "sbc", "Q2").numeric_value == Decimal("25000000")


def test_sbc_implausibly_low_flagged(client, db):
    """SBC-Teilkomponenten-Detektor: 4.1M bei 24 Mrd Umsatz (adidas-Fall)
    ist fast sicher eine uebersehene Teilsumme -> Flag."""
    cid = _company(client, db, "c12@example.com")
    _seed(db, cid, "revenue", "FY", 24000e6)
    _seed(db, cid, "sbc", "FY", 4.1e6)
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    assert "sbc_implausibly_low" in (_row(db, cid, "sbc", "FY").consistency_flags or "")


def test_sbc_zero_not_flagged(client, db):
    """Explizite 0 (Familien-Konzerne ohne SBC) ist legitim — kein Flag."""
    cid = _company(client, db, "c13@example.com")
    _seed(db, cid, "revenue", "FY", 24000e6)
    _seed(db, cid, "sbc", "FY", 0)
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    assert _row(db, cid, "sbc", "FY").consistency_flags is None


def test_sbc_plausible_not_flagged(client, db):
    cid = _company(client, db, "c14@example.com")
    _seed(db, cid, "revenue", "FY", 24000e6)
    _seed(db, cid, "sbc", "FY", 83.6e6)
    validate_cross_metrics(db, cid, 2026)
    db.commit()
    assert _row(db, cid, "sbc", "FY").consistency_flags is None
