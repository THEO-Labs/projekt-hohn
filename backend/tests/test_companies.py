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
                         json={"name": "Apple", "ticker": "AAPL", "currency": "USD"})
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
                      json={"name": "Old", "ticker": "OLD", "currency": "EUR"}).json()["id"]
    response = client.patch(f"/api/companies/{cid}", json={"name": "New"})
    assert response.status_code == 200
    assert response.json()["name"] == "New"


def test_delete_company(client, db):
    _user, pid = _login_with_portfolio(client, db)
    cid = client.post(f"/api/portfolios/{pid}/companies",
                      json={"name": "X", "ticker": "X", "currency": "EUR"}).json()["id"]
    response = client.delete(f"/api/companies/{cid}")
    assert response.status_code == 204
    assert client.get(f"/api/portfolios/{pid}/companies").json() == []


def test_create_company_with_invalid_isin_returns_422(client, db):
    _user, pid = _login_with_portfolio(client, db)
    response = client.post(f"/api/portfolios/{pid}/companies",
                           json={"name": "Apple", "ticker": "AAPL", "isin": "US0378331006", "currency": "USD"})
    assert response.status_code == 422
