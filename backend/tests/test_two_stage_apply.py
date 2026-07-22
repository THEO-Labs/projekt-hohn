"""apply_to_db (Two-Stage-Pipeline) muss dieselben Schreibpfad-Invarianten
einhalten wie der FY-Refresh: Sign-Normalisierung fuer ALWAYS_POSITIVE_KEYS
und ein Currency-Label, das den geschriebenen Wert beschreibt."""

from decimal import Decimal
from uuid import UUID

from app.auth.models import User
from app.auth.security import hash_password
from app.values.models import CompanyValue
from scripts.two_stage_research import (
    ExtractResult,
    QuarterValue,
    TwoStageResult,
    VerifierVerdict,
    apply_to_db,
)


def _company(client, db, email="ts@example.com"):
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


def _result(value_key: str, fy_value: Decimal) -> TwoStageResult:
    extract = ExtractResult(
        ticker="TST", value_key=value_key, year=2025, currency="EUR",
        q1=None, q2=None, q3=None, q4=None,
        fy=QuarterValue(value=fy_value, source_quote="FY figure from AR", source_url=None,
                        is_estimate=False),
        quarter_only=None, is_adjusted_note=None,
    )
    verdict = VerifierVerdict(
        verdict="confirm", corrections={}, reason="reconciles", confidence=0.9, flags=[],
    )
    return TwoStageResult(extract=extract, verdict=verdict)


def _fy_row(db, cid: UUID, key: str) -> CompanyValue:
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == cid,
            CompanyValue.value_key == key,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == 2025,
        )
        .one()
    )


def test_apply_to_db_skip_stamps_refresh_attempt(client, db):
    """Hard-Skip (keine Evidenz) laesst den alten Wert stehen — muss aber
    last_refresh_attempt stempeln, sonst sieht stale Data ewig frisch aus."""
    cid = _company(client, db, email="ts3@example.com")
    stale = CompanyValue(
        company_id=cid, value_key="sbc", period_type="FY", period_year=2025,
        numeric_value=Decimal("95000000"), source_name="old contaminated row",
    )
    db.add(stale)
    db.commit()

    no_evidence = _result("sbc", Decimal("120000000"))
    no_evidence.extract.fy.source_quote = None
    apply_to_db(db, cid, "sbc", 2025, no_evidence, currency="EUR")
    db.commit()

    row = _fy_row(db, cid, "sbc")
    assert row.numeric_value == Decimal("95000000")
    assert row.last_refresh_attempt is not None


def test_apply_to_db_normalizes_sign(client, db):
    cid = _company(client, db)
    apply_to_db(db, cid, "buyback_volume", 2025, _result("buyback_volume", Decimal("-2000")),
                currency="EUR")
    db.commit()
    assert _fy_row(db, cid, "buyback_volume").numeric_value == Decimal("2000")


def test_apply_to_db_updates_currency_label_on_overwrite(client, db):
    # Der Extractor liefert explizit Firmenwaehrung — beim Ueberschreiben muss
    # das Currency-Label der Zeile dem neuen Wert folgen, nicht dem alten.
    cid = _company(client, db, email="ts2@example.com")
    stale = CompanyValue(
        company_id=cid, value_key="revenue", period_type="FY", period_year=2025,
        numeric_value=Decimal("999"), currency="USD", source_name="old provider row",
    )
    db.add(stale)
    db.commit()

    apply_to_db(db, cid, "revenue", 2025, _result("revenue", Decimal("50000")), currency="EUR")
    db.commit()

    row = _fy_row(db, cid, "revenue")
    assert row.numeric_value == Decimal("50000")
    assert row.currency == "EUR"
