from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.auth.models import User
from app.auth.security import hash_password
from app.providers.base import ProviderResult
from app.values.catalog import SEED_VALUES
from app.values.models import CompanyValue, ValueDefinition


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
    assert "hohn_return" in keys
    assert "sbc" in keys
    assert "fcf" in keys
    assert "ni_growth" in keys


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


def test_get_company_values_after_refresh(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)

    mock_result = ProviderResult(
        value=Decimal("3.14"),
        source_name="Yahoo Finance",
        source_link="https://finance.yahoo.com/quote/AAPL",
        currency="USD",
    )

    with patch("app.values.routes.get_providers") as mock_get_providers:
        mock_provider = MagicMock()
        mock_provider.fetch.return_value = mock_result
        mock_get_providers.return_value = [mock_provider]
        client.post(
            f"/api/companies/{cid}/values/refresh",
            json={"keys": ["net_income"], "period_type": "FY", "period_year": 2024},
        )

    response = client.get(f"/api/companies/{cid}/values?period_type=FY&period_year=2024")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["value_key"] == "net_income"


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


def test_manual_override_zero_persists(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)

    response = client.post(
        f"/api/companies/{cid}/values/capex/override?period_type=FY&period_year=2024",
        json={"numeric_value": 0, "source_name": "Manuell"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["manually_overridden"] is True
    assert Decimal(data["numeric_value"]) == Decimal("0")

    overwrite = client.post(
        f"/api/companies/{cid}/values/capex/override?period_type=FY&period_year=2024",
        json={"numeric_value": 0, "source_name": "Manuell"},
    )
    assert overwrite.status_code == 200
    assert Decimal(overwrite.json()["numeric_value"]) == Decimal("0")

    fetched = client.get(f"/api/companies/{cid}/values?period_type=FY&period_year=2024")
    assert fetched.status_code == 200
    rows = [r for r in fetched.json() if r["value_key"] == "capex"]
    assert len(rows) == 1
    assert Decimal(rows[0]["numeric_value"]) == Decimal("0")


def test_manual_override_prevents_refresh(client, db):
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
    assert data[0]["numeric_value"] == "999.990000"
    assert data[0]["manually_overridden"] is True


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
