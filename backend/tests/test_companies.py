from app.auth.models import User
from app.auth.security import hash_password


def _login_with_portfolio(client, db, email="t@example.com"):
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})
    p = client.post("/api/portfolios", json={"name": "P"}).json()
    return user, p["id"]


def test_create_and_list_company(client, db):
    _user, pid = _login_with_portfolio(client, db)
    create = client.post(f"/api/portfolios/{pid}/companies",
                         json={"name": "Apple", "ticker": "AAPL",
                               "isin": "US0378331005", "currency": "USD"})
    assert create.status_code == 201
    assert create.json()["ticker"] == "AAPL"

    listed = client.get(f"/api/portfolios/{pid}/companies")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_company_in_other_users_portfolio_is_404(client, db):
    _u1, pid_a = _login_with_portfolio(client, db, email="a@example.com")
    client.post("/api/auth/logout")
    _u2, _pid_b = _login_with_portfolio(client, db, email="b@example.com")
    response = client.post(f"/api/portfolios/{pid_a}/companies",
                           json={"name": "X", "ticker": "X", "currency": "EUR"})
    assert response.status_code == 404


def test_update_company(client, db):
    _user, pid = _login_with_portfolio(client, db)
    cid = client.post(f"/api/portfolios/{pid}/companies",
                      json={"name": "Old", "ticker": "OLD",
                            "isin": "US5949181045", "currency": "EUR"}).json()["id"]
    response = client.patch(f"/api/companies/{cid}", json={"name": "New"})
    assert response.status_code == 200
    assert response.json()["name"] == "New"


def test_delete_company(client, db):
    _user, pid = _login_with_portfolio(client, db)
    cid = client.post(f"/api/portfolios/{pid}/companies",
                      json={"name": "X", "ticker": "X",
                            "isin": "US02079K3059", "currency": "EUR"}).json()["id"]
    response = client.delete(f"/api/companies/{cid}")
    assert response.status_code == 204
    assert client.get(f"/api/portfolios/{pid}/companies").json() == []


def test_accounting_standard_us_gaap_for_us_company(client, db):
    # US-Filer (ISIN-Praefix "US") -> US-GAAP, in CompanyOut und im Detail-Endpoint
    _user, pid = _login_with_portfolio(client, db)
    created = client.post(f"/api/portfolios/{pid}/companies",
                          json={"name": "Apple", "ticker": "AAPL",
                                "isin": "US0378331005", "currency": "USD"})
    assert created.status_code == 201
    assert created.json()["accounting_standard"] == "US-GAAP"
    cid = created.json()["id"]

    listed = client.get(f"/api/portfolios/{pid}/companies").json()
    assert listed[0]["accounting_standard"] == "US-GAAP"

    detail = client.get(f"/api/companies/{cid}/detail")
    assert detail.status_code == 200
    assert detail.json()["company"]["accounting_standard"] == "US-GAAP"


def test_accounting_standard_ifrs_for_german_company(client, db):
    # Neuanlage deutscher Firmen ist gesperrt (Produktentscheid Aug 2026);
    # BESTEHENDE Nicht-US-Firmen liefern weiter IFRS als Standard.
    from uuid import UUID
    from app.companies.models import Company
    _user, pid = _login_with_portfolio(client, db)
    blocked = client.post(f"/api/portfolios/{pid}/companies",
                          json={"name": "BASF", "ticker": "BAS.DE",
                                "isin": "DE000BASF111", "currency": "EUR"})
    assert blocked.status_code == 400

    company = Company(portfolio_id=UUID(pid), name="BASF", ticker="BAS.DE",
                      isin="DE000BASF111", currency="EUR")
    db.add(company)
    db.commit()
    detail = client.get(f"/api/companies/{company.id}/detail")
    assert detail.status_code == 200
    assert detail.json()["company"]["accounting_standard"] == "IFRS"


def test_create_without_isin_returns_400(client, db):
    # ISIN-Pflicht (Produktentscheid Aug 2026): Anlage ohne ISIN wird
    # abgelehnt (frueher lief das als IFRS-Default durch).
    _user, pid = _login_with_portfolio(client, db)
    created = client.post(f"/api/portfolios/{pid}/companies",
                          json={"name": "X", "ticker": "X", "currency": "EUR"})
    assert created.status_code == 400
    assert "ISIN" in created.json()["detail"]


def test_create_company_with_invalid_isin_returns_422(client, db):
    _user, pid = _login_with_portfolio(client, db)
    response = client.post(f"/api/portfolios/{pid}/companies",
                           json={"name": "Apple", "ticker": "AAPL", "isin": "US0378331006", "currency": "USD"})
    assert response.status_code == 422
