"""Portfolio-Sharing: Mitglieder sehen und bearbeiten das Portfolio,
Fremde weiterhin nicht."""

from app.auth.models import User
from app.auth.security import hash_password
from app.portfolios.models import PortfolioMember


def _user(db, email):
    u = User(email=email, password_hash=hash_password("pw1234"))
    db.add(u)
    db.commit()
    return u


def _login(client, email):
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})


def test_member_sees_shared_portfolio_and_companies(client, db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    owner = _user(db, "owner@example.com")
    member = _user(db, "member@example.com")
    _login(client, "owner@example.com")
    pid = client.post("/api/portfolios", json={"name": "DAX"}).json()["id"]
    client.post(f"/api/portfolios/{pid}/companies",
                json={"name": "TestCo", "ticker": "TST", "currency": "EUR"})

    db.add(PortfolioMember(portfolio_id=pid, user_id=member.id))
    db.commit()

    _login(client, "member@example.com")
    names = [p["name"] for p in client.get("/api/portfolios").json()]
    assert "DAX" in names
    companies = client.get(f"/api/portfolios/{pid}/companies").json()
    assert len(companies) == 1


def test_stranger_still_blocked(client, db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    _user(db, "owner2@example.com")
    _user(db, "stranger@example.com")
    _login(client, "owner2@example.com")
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]

    _login(client, "stranger@example.com")
    assert client.get("/api/portfolios").json() == []
    r = client.get(f"/api/portfolios/{pid}/companies")
    assert r.status_code == 404
