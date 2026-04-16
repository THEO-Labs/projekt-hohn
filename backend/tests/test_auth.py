from app.auth.models import User
from app.auth.security import hash_password


def _seed_user(db, email="t@example.com", password="pw1234"):
    user = User(email=email, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_login_sets_cookie_and_returns_user(client, db):
    _seed_user(db)
    response = client.post("/api/auth/login", json={"email": "t@example.com", "password": "pw1234"})
    assert response.status_code == 200
    assert response.json()["email"] == "t@example.com"
    assert "access_token" in response.cookies


def test_login_wrong_password_returns_401(client, db):
    _seed_user(db)
    response = client.post("/api/auth/login", json={"email": "t@example.com", "password": "wrong"})
    assert response.status_code == 401


def test_me_requires_auth(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 401


def test_me_returns_current_user(client, db):
    _seed_user(db)
    client.post("/api/auth/login", json={"email": "t@example.com", "password": "pw1234"})
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == "t@example.com"


def test_logout_clears_cookie(client, db):
    _seed_user(db)
    client.post("/api/auth/login", json={"email": "t@example.com", "password": "pw1234"})
    response = client.post("/api/auth/logout")
    assert response.status_code == 204
    me = client.get("/api/auth/me")
    assert me.status_code == 401
