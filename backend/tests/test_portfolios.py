from app.auth.models import User
from app.auth.security import hash_password


def _login(client, db, email="t@example.com"):
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})
    return user


def test_list_portfolios_requires_auth(client):
    response = client.get("/api/portfolios")
    assert response.status_code == 401


def test_create_and_list_portfolio(client, db):
    _login(client, db)
    create = client.post("/api/portfolios", json={"name": "Mandant A"})
    assert create.status_code == 201
    assert create.json()["name"] == "Mandant A"

    listed = client.get("/api/portfolios")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_only_own_portfolios_visible(client, db):
    _login(client, db, email="a@example.com")
    client.post("/api/portfolios", json={"name": "A"})
    client.post("/api/auth/logout")

    _login(client, db, email="b@example.com")
    listed = client.get("/api/portfolios")
    assert listed.status_code == 200
    assert listed.json() == []


def test_update_portfolio(client, db):
    _login(client, db)
    create = client.post("/api/portfolios", json={"name": "Old"})
    pid = create.json()["id"]
    update = client.patch(f"/api/portfolios/{pid}", json={"name": "New"})
    assert update.status_code == 200
    assert update.json()["name"] == "New"


def test_delete_portfolio(client, db):
    _login(client, db)
    create = client.post("/api/portfolios", json={"name": "X"})
    pid = create.json()["id"]
    delete = client.delete(f"/api/portfolios/{pid}")
    assert delete.status_code == 204
    assert client.get("/api/portfolios").json() == []
