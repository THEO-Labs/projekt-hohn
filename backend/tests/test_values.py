from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.auth.models import User
from app.auth.security import hash_password
from app.providers.base import ProviderResult
from app.values.catalog import SEED_VALUES
from app.values.models import CompanyValue


def _seed_catalog(db):
    from sqlalchemy import text
    for row in SEED_VALUES:
        db.execute(
            text(
                "INSERT INTO value_definitions "
                "(key, label_de, label_en, category, source_type, data_type, unit, sort_order) "
                "VALUES (:key, :label_de, :label_en, "
                "CAST(:category AS valuecategory), "
                "CAST(:source_type AS sourcetype), "
                "CAST(:data_type AS datatype), "
                ":unit, :sort_order) "
                "ON CONFLICT (key) DO NOTHING"
            ),
            {
                "key": row["key"],
                "label_de": row["label_de"],
                "label_en": row["label_en"],
                "category": row["category"],
                "source_type": row["source_type"],
                "data_type": row["data_type"],
                "unit": row.get("unit"),
                "sort_order": row["sort_order"],
            },
        )
    db.commit()


def _login_with_company(client, db, email="t@example.com", ticker="AAPL"):
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})
    p = client.post("/api/portfolios", json={"name": "P"}).json()
    pid = p["id"]
    c = client.post(
        f"/api/portfolios/{pid}/companies",
        json={"name": "Apple", "ticker": ticker, "currency": "USD"},
    ).json()
    return user, pid, c["id"]


def _login(client, db, email="catalog@example.com"):
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})


def test_get_value_definitions_returns_catalog(client, db):
    _seed_catalog(db)
    _login(client, db)
    response = client.get("/api/value-definitions")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == len(SEED_VALUES)
    keys = {item["key"] for item in data}
    assert "market_cap" in keys
    assert "hohn_return_simple" in keys
    assert "hohn_return_detailed" in keys
    assert "sbc" in keys
    assert "cash_and_equivalents" in keys
    assert "net_buyback_yield" in keys


def test_get_value_definitions_ordered(client, db):
    _seed_catalog(db)
    _login(client, db)
    response = client.get("/api/value-definitions")
    data = response.json()
    orders = [item["sort_order"] for item in data]
    assert orders == sorted(orders)


def test_get_company_values_empty(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)
    response = client.get(f"/api/companies/{cid}/values")
    assert response.status_code == 200
    assert response.json() == []


def test_refresh_with_mocked_provider(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)

    mock_result = ProviderResult(
        value=Decimal("189.50"),
        source_name="Yahoo Finance",
        source_link="https://finance.yahoo.com/quote/AAPL",
        currency="USD",
    )

    with patch("app.values.routes.get_providers") as mock_get_providers:
        mock_provider = MagicMock()
        mock_provider.fetch.return_value = mock_result
        mock_get_providers.return_value = [mock_provider]

        response = client.post(
            f"/api/companies/{cid}/values/refresh",
            json={"keys": ["market_cap"], "period_type": "SNAPSHOT"},
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["value_key"] == "market_cap"
    assert data[0]["numeric_value"] == "189.500000"
    assert data[0]["currency"] == "USD"
    assert data[0]["source_name"] == "Yahoo Finance"
    assert data[0]["manually_overridden"] is False


def test_refresh_skips_keys_without_provider(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)

    with patch("app.values.routes.get_providers") as mock_get_providers:
        mock_get_providers.return_value = []

        response = client.post(
            f"/api/companies/{cid}/values/refresh",
            json={"keys": ["fcf"], "period_type": "FY", "period_year": 2024},
        )

    assert response.status_code == 200
    # fcf is CALCULATED, so no provider fetch → no direct row, but calc runs with no inputs.
    assert response.json() == []


def test_refresh_updates_existing_value(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)

    def make_result(price):
        return ProviderResult(
            value=Decimal(str(price)),
            source_name="Yahoo Finance",
            source_link="https://finance.yahoo.com/quote/AAPL",
            currency="USD",
        )

    with patch("app.values.routes.get_providers") as mock_get_providers:
        mock_provider = MagicMock()
        mock_provider.fetch.return_value = make_result("100.00")
        mock_get_providers.return_value = [mock_provider]
        client.post(
            f"/api/companies/{cid}/values/refresh",
            json={"keys": ["market_cap"], "period_type": "SNAPSHOT"},
        )

    with patch("app.values.routes.get_providers") as mock_get_providers:
        mock_provider = MagicMock()
        mock_provider.fetch.return_value = make_result("200.00")
        mock_get_providers.return_value = [mock_provider]
        response = client.post(
            f"/api/companies/{cid}/values/refresh",
            json={"keys": ["market_cap"], "period_type": "SNAPSHOT"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data[0]["numeric_value"] == "200.000000"

    all_values = client.get(f"/api/companies/{cid}/values").json()
    assert len(all_values) == 1


def _fake_two_stage_result(value_key: str, year: int, fy_value: Decimal):
    from scripts.two_stage_research import (
        ExtractResult,
        QuarterValue,
        TwoStageResult,
        VerifierVerdict,
    )

    extract = ExtractResult(
        ticker="AAPL", value_key=value_key, year=year, currency="USD",
        q1=None, q2=None, q3=None, q4=None,
        fy=QuarterValue(value=fy_value, source_quote="10-K net income line",
                        source_url=None, is_estimate=False),
        quarter_only=None, is_adjusted_note=None,
    )
    verdict = VerifierVerdict(
        verdict="confirm", corrections={}, reason="reconciles", confidence=0.9, flags=[],
    )
    return TwoStageResult(extract=extract, verdict=verdict)


def test_get_company_values_after_refresh(client, db):
    """FY-Refresh fuer API-Keys laeuft transparent durch die Two-Stage-
    Pipeline (kein Provider-Fallback) — der Test mockt research_two_stage."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)

    def fake_research(*, ticker, company_name, value_key, year, **kwargs):
        return _fake_two_stage_result(value_key, year, Decimal("314000000"))

    with patch("scripts.two_stage_research.research_two_stage", side_effect=fake_research):
        client.post(
            f"/api/companies/{cid}/values/refresh",
            json={"keys": ["net_income"], "period_type": "FY", "period_year": 2024},
        )

    response = client.get(f"/api/companies/{cid}/values?period_type=FY&period_year=2024")
    assert response.status_code == 200
    data = response.json()
    rows = {item["value_key"]: item for item in data}
    assert "net_income" in rows
    assert Decimal(rows["net_income"]["numeric_value"]) == Decimal("314000000")
    assert rows["net_income"]["primary_method"] == "two_stage_confirmed"


def test_manual_override(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"numeric_value": "5.25", "source_name": "Manual"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["manually_overridden"] is True
    assert data["numeric_value"] == "5.250000"
    assert data["source_name"] == "Manual"


def test_manual_override_sign_normalized(client, db):
    """Negative Eingabe fuer einen ALWAYS_POSITIVE_KEY wird zentral auf abs()
    normalisiert — unabhaengig vom Schreibpfad (hier: Override-Endpoint)."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="sign@example.com")

    response = client.post(
        f"/api/companies/{cid}/values/buyback_volume/override?period_type=FY&period_year=2024",
        json={"numeric_value": "-3000", "source_name": "Manual"},
    )
    assert response.status_code == 200
    assert Decimal(response.json()["numeric_value"]) == Decimal("3000")


def test_manual_override_zero_persists(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)

    response = client.post(
        f"/api/companies/{cid}/values/buyback_volume/override?period_type=FY&period_year=2024",
        json={"numeric_value": 0, "source_name": "Manuell"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["manually_overridden"] is True
    assert Decimal(data["numeric_value"]) == Decimal("0")

    overwrite = client.post(
        f"/api/companies/{cid}/values/buyback_volume/override?period_type=FY&period_year=2024",
        json={"numeric_value": 0, "source_name": "Manuell"},
    )
    assert overwrite.status_code == 200
    assert Decimal(overwrite.json()["numeric_value"]) == Decimal("0")

    fetched = client.get(f"/api/companies/{cid}/values?period_type=FY&period_year=2024")
    assert fetched.status_code == 200
    rows = [r for r in fetched.json() if r["value_key"] == "buyback_volume"]
    assert len(rows) == 1
    assert Decimal(rows[0]["numeric_value"]) == Decimal("0")


def test_refresh_overwrites_manual_override_on_always_current_key(client, db):
    """Aktueller Vertrag: Manual-Overrides auf ALWAYS_CURRENT-Keys sind
    temporaer — ein Refresh holt den Live-Wert und setzt das Flag zurueck.
    (Hart gesperrt bleiben nur manuell ueberschriebene Forecasts.)"""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)

    client.post(
        f"/api/companies/{cid}/values/market_cap/override",
        json={"numeric_value": "999.99"},
    )

    with patch("app.values.routes.get_providers") as mock_get_providers:
        mock_provider = MagicMock()
        mock_provider.fetch.return_value = ProviderResult(
            value=Decimal("100.00"),
            source_name="Yahoo Finance",
            source_link="https://finance.yahoo.com/quote/AAPL",
            currency="USD",
        )
        mock_get_providers.return_value = [mock_provider]
        response = client.post(
            f"/api/companies/{cid}/values/refresh",
            json={"keys": ["market_cap"], "period_type": "SNAPSHOT"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data[0]["numeric_value"] == "100.000000"
    assert data[0]["manually_overridden"] is False


def test_refresh_one_failing_provider_doesnt_crash_others(client, db):
    """If one key's provider raises an exception, the other keys should still succeed."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="partial@example.com")

    good_result = ProviderResult(
        value=Decimal("189.50"),
        source_name="Yahoo Finance",
        source_link="https://finance.yahoo.com/quote/AAPL",
        currency="USD",
    )

    call_count = 0

    def side_effect(ticker, key, period_type, period_year):
        nonlocal call_count
        call_count += 1
        if key == "market_cap":
            raise RuntimeError("Simulated provider failure")
        return good_result

    with patch("app.values.routes.get_providers") as mock_get_providers:
        mock_provider = MagicMock()
        mock_provider.fetch.side_effect = side_effect
        mock_get_providers.return_value = [mock_provider]

        response = client.post(
            f"/api/companies/{cid}/values/refresh",
            json={"keys": ["market_cap", "sbc"], "period_type": "SNAPSHOT"},
        )

    assert response.status_code == 200
    data = response.json()
    returned_keys = {item["value_key"] for item in data}
    assert "sbc" in returned_keys
    assert "market_cap" not in returned_keys


def test_company_values_requires_auth(client, db):
    _seed_catalog(db)
    cid = str(uuid4())
    client.post("/api/auth/logout")
    response = client.get(f"/api/companies/{cid}/values")
    assert response.status_code == 401


def test_company_values_other_user_is_404(client, db):
    _seed_catalog(db)
    _u1, _p1, cid = _login_with_company(client, db, email="a@example.com")
    client.post("/api/auth/logout")

    user2 = User(email="b@example.com", password_hash=hash_password("pw1234"))
    db.add(user2)
    db.commit()
    client.post("/api/auth/login", json={"email": "b@example.com", "password": "pw1234"})

    response = client.get(f"/api/companies/{cid}/values")
    assert response.status_code == 404


def _seed_value(db, cid, key, period_type, period_year, value, manually_overridden=False):
    cv = CompanyValue(
        company_id=cid,
        value_key=key,
        period_type=period_type,
        period_year=period_year,
        numeric_value=Decimal(str(value)),
        source_name="Test",
        manually_overridden=manually_overridden,
    )
    db.add(cv)
    db.commit()


def _get_row(client, cid, key, period_type, period_year=None):
    params = f"period_type={period_type}"
    if period_year is not None:
        params += f"&period_year={period_year}"
    rows = client.get(f"/api/companies/{cid}/values?{params}").json()
    return next((r for r in rows if r["value_key"] == key), None)


def test_override_calculated_key_rejected(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="lock@example.com")

    # net_debt ist bewusst KEIN Calculated-Key mehr (kommt direkt aus der
    # Extraktion) und darf daher manuell ueberschrieben werden.
    for key in ("hohn_return_simple", "hohn_return_detailed", "ni_growth", "fcf_yield"):
        response = client.post(
            f"/api/companies/{cid}/values/{key}/override?period_type=FY&period_year=2024",
            json={"numeric_value": "1.0", "source_name": "Manual"},
        )
        assert response.status_code == 400, f"override of {key} should be rejected"


def test_override_primary_triggers_same_year_recalc(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="recalc@example.com")

    _seed_value(db, cid, "market_cap", "SNAPSHOT", None, "1000")
    _seed_value(db, cid, "net_income", "FY", 2023, "100")
    _seed_value(db, cid, "net_income", "FY", 2024, "150")

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"numeric_value": "200", "source_name": "Manuell"},
    )
    assert response.status_code == 200
    assert Decimal(response.json()["numeric_value"]) == Decimal("200")

    ni_growth = _get_row(client, cid, "ni_growth", "FY", 2024)
    assert ni_growth is not None
    assert Decimal(ni_growth["numeric_value"]) == Decimal("100")
    assert ni_growth["manually_overridden"] is False


def test_override_primary_cascades_to_next_year_when_data_exists(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="cross@example.com")

    _seed_value(db, cid, "market_cap", "SNAPSHOT", None, "1000")
    _seed_value(db, cid, "net_income", "FY", 2023, "100")
    _seed_value(db, cid, "net_income", "FY", 2024, "150")
    _seed_value(db, cid, "net_income", "FY", 2025, "180")

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"numeric_value": "200", "source_name": "Manuell"},
    )
    assert response.status_code == 200

    ng_2025 = _get_row(client, cid, "ni_growth", "FY", 2025)
    assert ng_2025 is not None
    assert Decimal(ng_2025["numeric_value"]) == Decimal("-10")


def test_override_primary_does_not_create_rows_in_empty_next_year(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="empty@example.com")

    _seed_value(db, cid, "market_cap", "SNAPSHOT", None, "1000")
    _seed_value(db, cid, "net_income", "FY", 2023, "100")
    _seed_value(db, cid, "net_income", "FY", 2024, "150")

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"numeric_value": "200", "source_name": "Manuell"},
    )
    assert response.status_code == 200

    rows_2025 = client.get(f"/api/companies/{cid}/values?period_type=FY&period_year=2025").json()
    assert rows_2025 == []


def test_override_market_cap_recalcs_all_existing_fy_years(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="mcap@example.com")

    _seed_value(db, cid, "market_cap", "SNAPSHOT", None, "1000")
    _seed_value(db, cid, "fcf", "FY", 2023, "100")
    _seed_value(db, cid, "fcf", "FY", 2024, "200")

    response = client.post(
        f"/api/companies/{cid}/values/market_cap/override?period_type=SNAPSHOT",
        json={"numeric_value": "2000", "source_name": "Manuell"},
    )
    assert response.status_code == 200

    fy_2023 = _get_row(client, cid, "fcf_yield", "FY", 2023)
    fy_2024 = _get_row(client, cid, "fcf_yield", "FY", 2024)
    assert fy_2023 is not None and Decimal(fy_2023["numeric_value"]) == Decimal("5")
    assert fy_2024 is not None and Decimal(fy_2024["numeric_value"]) == Decimal("10")


def test_old_calc_key_with_stale_lock_gets_overwritten(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="stale@example.com")

    _seed_value(db, cid, "market_cap", "SNAPSHOT", None, "1000")
    _seed_value(db, cid, "net_income", "FY", 2023, "100")
    _seed_value(db, cid, "net_income", "FY", 2024, "150")
    _seed_value(db, cid, "ni_growth", "FY", 2024, "999", manually_overridden=True)

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"numeric_value": "200", "source_name": "Manuell"},
    )
    assert response.status_code == 200

    ng = _get_row(client, cid, "ni_growth", "FY", 2024)
    assert ng is not None
    assert Decimal(ng["numeric_value"]) == Decimal("100")
    assert ng["manually_overridden"] is False
