"""Anlage-Sperre fuer Nicht-US-Firmen (Produktentscheid Aug 2026)."""
from uuid import uuid4

import pytest

from app.auth.models import User
from app.auth.security import hash_password


@pytest.fixture
def portfolio_id(client, db):
    email = f"u-{uuid4().hex[:6]}@example.com"
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})
    r = client.post("/api/portfolios", json={"name": f"P-{uuid4().hex[:6]}"})
    assert r.status_code == 201
    return r.json()["id"]


def _create(client, portfolio_id, **kw):
    payload = {"name": "Testfirma", "ticker": "TST", "currency": "USD"}
    payload.update(kw)
    return client.post(f"/api/portfolios/{portfolio_id}/companies", json=payload)


def test_us_company_creates(client, portfolio_id):
    r = _create(client, portfolio_id, isin="US0378331005", ticker="AAPL")
    assert r.status_code == 201


def test_non_us_isin_blocked(client, portfolio_id):
    r = _create(client, portfolio_id, isin="DE0007164600", ticker="SAP")
    assert r.status_code == 400
    assert "Nicht-US" in r.json()["detail"]


def test_non_us_ticker_suffix_blocked(client, portfolio_id):
    r = _create(client, portfolio_id, ticker="SAP.DE")
    assert r.status_code == 400


def test_currency_alone_is_not_blocked(client, portfolio_id):
    # Waehrung allein ist kein Kriterium (interne Fixtures/Randfaelle):
    # US-ISIN + EUR bleibt erlaubt, nur ISIN/Ticker-Suffix blocken.
    r = _create(client, portfolio_id, isin="US0378331005", currency="EUR")
    assert r.status_code == 201


def test_us_company_cannot_be_rededicated(client, portfolio_id):
    r = _create(client, portfolio_id, isin="US0378331005", ticker="AAPL")
    cid = r.json()["id"]
    r2 = client.patch(f"/api/companies/{cid}", json={"isin": "DE0007164600"})
    assert r2.status_code == 400


def test_existing_non_us_company_stays_editable(client, portfolio_id, db):
    from app.companies.models import Company
    from uuid import UUID
    company = Company(
        portfolio_id=UUID(portfolio_id), name="SAP SE", ticker="SAP.DE",
        isin="DE0007164600", currency="EUR",
    )
    db.add(company); db.commit()
    r = client.patch(f"/api/companies/{company.id}", json={"name": "SAP SE (Bestand)"})
    assert r.status_code == 200


def test_duplicate_ticker_blocked_across_portfolios(client, portfolio_id, db):
    r1 = _create(client, portfolio_id, isin="US78409V1044", ticker="SPGI")
    assert r1.status_code == 201
    r2 = client.post("/api/portfolios", json={"name": "Zweites Portfolio"})
    pid2 = r2.json()["id"]
    r3 = _create(client, pid2, isin="US78409V1044", ticker="SPGI")
    assert r3.status_code == 400
    assert "existiert bereits" in r3.json()["detail"]


def test_duplicate_ticker_case_insensitive(client, portfolio_id):
    r1 = _create(client, portfolio_id, isin="US78409V1044", ticker="SPGI")
    assert r1.status_code == 201
    r2 = _create(client, portfolio_id, isin="US78409V1044", ticker="spgi")
    assert r2.status_code == 400
