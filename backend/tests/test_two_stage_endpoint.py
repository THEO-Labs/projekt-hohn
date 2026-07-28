"""Two-Stage-Endpoint und -Helper: Robustheit gegen Actual+Forecast-
Zeilenpaare (Unique-Index erlaubt 2 Zeilen pro Zelle) und kein stiller
Zustand, wenn Pipeline UND XBRL-Anker nichts liefern."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.values.models import CompanyValue
from app.values.persistence import NOT_FOUND_SOURCE


def _setup(client, db, email):
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


def _fy_row(cid, key, year, value, is_forecast):
    return CompanyValue(
        company_id=cid, value_key=key, period_type="FY", period_year=year,
        numeric_value=value, is_forecast=is_forecast, source_name="seed",
    )


def test_two_stage_refresh_survives_prev_year_pair(client, db):
    """Punkt 10: der prev-FY-Hint-Lookup darf bei einem Actual+Forecast-
    Paar nicht crashen (one_or_none -> MultipleResultsFound); die
    Actual-Zeile liefert den Hint."""
    from tests.test_two_stage_apply import _result

    cid = _setup(client, db, "tse1@example.com")
    db.add(_fy_row(cid, "net_income", 2025, Decimal("50"), is_forecast=True))
    db.add(_fy_row(cid, "net_income", 2025, Decimal("77"), is_forecast=False))
    db.commit()

    seen = {}

    def fake_research(**kw):
        seen.update(kw)
        return _result("net_income", Decimal("100"))

    with patch("scripts.two_stage_research.research_two_stage",
               side_effect=fake_research):
        r = client.post(
            f"/api/companies/{cid}/values/two-stage-refresh",
            json={"keys": ["net_income"], "period_year": 2026},
        )

    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["verdict"] == "confirm"
    assert res.get("error") in (None, "")
    assert seen["prev_year_fy_hint"] == Decimal("77")


def test_helper_stamps_and_fills_not_found_when_pipeline_and_anchor_fail(client, db):
    """Punkt 11: research_two_stage wirft UND der Anker liefert nichts
    (Provider-Kette leer): bestehende Zeilen werden gestempelt, fehlende
    Perioden Q1-Q4+FY bekommen not_found-Platzhalter — kein stiller
    Zustand."""
    from app.values.routes import _process_one_key_via_two_stage

    cid = _setup(client, db, "tse2@example.com")
    existing = _fy_row(cid, "net_income", 2024, Decimal("100"), is_forecast=False)
    db.add(existing)
    db.commit()
    company = db.get(Company, cid)

    payload = SimpleNamespace(period_type="FY", period_year=2024)
    with patch("scripts.two_stage_research.research_two_stage",
               side_effect=RuntimeError("api down")):
        wrote = _process_one_key_via_two_stage(
            db=db, key="net_income", company=company,
            company_id=cid, payload=payload, updated=[],
        )
    db.commit()

    assert wrote is False
    rows = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == cid,
            CompanyValue.value_key == "net_income",
            CompanyValue.period_year == 2024,
        )
        .all()
    )
    by_pt = {r.period_type: r for r in rows}
    assert set(by_pt) == {"Q1", "Q2", "Q3", "Q4", "FY"}
    # Bestehende FY-Zeile: Wert bleibt, aber der Versuch ist gestempelt.
    assert by_pt["FY"].numeric_value == Decimal("100")
    assert by_pt["FY"].last_refresh_attempt is not None
    # Fehlende Quartale: rote not_found-Platzhalter.
    for pt in ("Q1", "Q2", "Q3", "Q4"):
        assert by_pt[pt].numeric_value is None
        assert by_pt[pt].primary_method == "not_found"
        assert by_pt[pt].source_name == NOT_FOUND_SOURCE
