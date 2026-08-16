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
                json={"name": "TestCo", "ticker": "TST",
                      "isin": "US0378331005", "currency": "EUR"})

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


def test_member_can_read_company_detail(client, db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    _user(db, "owner3@example.com")
    member = _user(db, "member3@example.com")
    _login(client, "owner3@example.com")
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]
    cid = client.post(f"/api/portfolios/{pid}/companies",
                      json={"name": "TestCo", "ticker": "TST",
                            "isin": "US0378331005", "currency": "EUR"}).json()["id"]
    db.add(PortfolioMember(portfolio_id=pid, user_id=member.id))
    db.commit()

    _login(client, "member3@example.com")
    r = client.get(f"/api/companies/{cid}/detail")
    assert r.status_code == 200


# --- Mitglieder-Verwaltung (GET/POST/DELETE /members) ---


def test_owner_adds_and_lists_members(client, db):
    owner = _user(db, "mgmt-owner@example.com")
    member = _user(db, "mgmt-member@example.com")
    _login(client, "mgmt-owner@example.com")
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]

    r = client.post(f"/api/portfolios/{pid}/members", json={"email": "mgmt-member@example.com"})
    assert r.status_code == 201
    assert r.json() == {"user_id": str(member.id), "email": "mgmt-member@example.com", "is_owner": False}

    members = client.get(f"/api/portfolios/{pid}/members").json()
    assert members == [
        {"user_id": str(owner.id), "email": "mgmt-owner@example.com", "is_owner": True},
        {"user_id": str(member.id), "email": "mgmt-member@example.com", "is_owner": False},
    ]


def test_add_member_email_case_insensitive(client, db):
    _user(db, "case-owner@example.com")
    _user(db, "case-member@example.com")
    _login(client, "case-owner@example.com")
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]

    r = client.post(f"/api/portfolios/{pid}/members", json={"email": "Case-Member@Example.COM"})
    assert r.status_code == 201
    assert r.json()["email"] == "case-member@example.com"


def test_add_unknown_email_404(client, db):
    _user(db, "unknown-owner@example.com")
    _login(client, "unknown-owner@example.com")
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]

    r = client.post(f"/api/portfolios/{pid}/members", json={"email": "nobody@example.com"})
    assert r.status_code == 404
    assert "User nicht gefunden" in r.json()["detail"]


def test_add_member_twice_409(client, db):
    _user(db, "dup-owner@example.com")
    _user(db, "dup-member@example.com")
    _login(client, "dup-owner@example.com")
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]

    assert client.post(f"/api/portfolios/{pid}/members", json={"email": "dup-member@example.com"}).status_code == 201
    r = client.post(f"/api/portfolios/{pid}/members", json={"email": "dup-member@example.com"})
    assert r.status_code == 409


def test_owner_cannot_add_self(client, db):
    _user(db, "self-owner@example.com")
    _login(client, "self-owner@example.com")
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]

    r = client.post(f"/api/portfolios/{pid}/members", json={"email": "self-owner@example.com"})
    assert r.status_code == 409


def test_member_sees_list_but_cannot_manage(client, db):
    owner = _user(db, "mm-owner@example.com")
    member = _user(db, "mm-member@example.com")
    _user(db, "mm-third@example.com")
    _login(client, "mm-owner@example.com")
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]
    db.add(PortfolioMember(portfolio_id=pid, user_id=member.id))
    db.commit()

    _login(client, "mm-member@example.com")
    members = client.get(f"/api/portfolios/{pid}/members").json()
    assert {m["email"] for m in members} == {"mm-owner@example.com", "mm-member@example.com"}

    # Mitglied darf weder hinzufuegen noch entfernen
    r = client.post(f"/api/portfolios/{pid}/members", json={"email": "mm-third@example.com"})
    assert r.status_code == 403
    r = client.delete(f"/api/portfolios/{pid}/members/{owner.id}")
    assert r.status_code == 403


def test_stranger_cannot_see_members(client, db):
    _user(db, "ms-owner@example.com")
    _user(db, "ms-stranger@example.com")
    _login(client, "ms-owner@example.com")
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]

    _login(client, "ms-stranger@example.com")
    assert client.get(f"/api/portfolios/{pid}/members").status_code == 404


def test_owner_removes_member(client, db):
    _user(db, "rm-owner@example.com")
    member = _user(db, "rm-member@example.com")
    _login(client, "rm-owner@example.com")
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]
    db.add(PortfolioMember(portfolio_id=pid, user_id=member.id))
    db.commit()

    r = client.delete(f"/api/portfolios/{pid}/members/{member.id}")
    assert r.status_code == 204
    members = client.get(f"/api/portfolios/{pid}/members").json()
    assert [m["email"] for m in members] == ["rm-owner@example.com"]

    # Ex-Mitglied hat keinen Zugriff mehr
    _login(client, "rm-member@example.com")
    assert client.get(f"/api/portfolios/{pid}/members").status_code == 404


def test_owner_cannot_remove_self(client, db):
    owner = _user(db, "rs-owner@example.com")
    _login(client, "rs-owner@example.com")
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]

    r = client.delete(f"/api/portfolios/{pid}/members/{owner.id}")
    assert r.status_code == 409


def test_remove_nonmember_404(client, db):
    _user(db, "rn-owner@example.com")
    other = _user(db, "rn-other@example.com")
    _login(client, "rn-owner@example.com")
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]

    r = client.delete(f"/api/portfolios/{pid}/members/{other.id}")
    assert r.status_code == 404
