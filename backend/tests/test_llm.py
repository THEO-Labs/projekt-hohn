from decimal import Decimal
from unittest.mock import patch

from app.auth.models import User
from app.auth.security import hash_password
from app.llm.claude import extract_score


def test_extract_score_valid():
    assert extract_score("SCORE: 1.20\nBEGRUENDUNG: ...") == Decimal("1.20")


def test_extract_score_comma():
    assert extract_score("SCORE: 0,85") == Decimal("0.85")


def test_extract_score_out_of_range():
    assert extract_score("SCORE: 2.50") is None


def test_extract_score_missing():
    assert extract_score("no score here") is None


def _login_with_company(client, db, email="t@example.com"):
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})
    p = client.post("/api/portfolios", json={"name": "P"}).json()
    pid = p["id"]
    c = client.post(
        f"/api/portfolios/{pid}/companies",
        json={"name": "Apple", "ticker": "AAPL", "currency": "USD"},
    ).json()
    return c["id"]


def test_analyze_requires_auth(client):
    response = client.post("/api/companies/00000000-0000-0000-0000-000000000000/analyze/judgement")
    assert response.status_code == 401


def test_chat_requires_auth(client):
    response = client.post(
        "/api/companies/00000000-0000-0000-0000-000000000000/chat/judgement",
        json={"message": "hello"},
    )
    assert response.status_code == 401


def test_chat_history_empty(client, db):
    cid = _login_with_company(client, db)
    response = client.get(f"/api/companies/{cid}/chat/judgement/history")
    assert response.status_code == 200
    data = response.json()
    assert data["messages"] == []
    assert "conversation_id" in data


def test_analyze_calls_claude(client, db):
    cid = _login_with_company(client, db)
    mock_response = ("SCORE: 1.10\nBEGRUENDUNG: Test", Decimal("1.10"))
    with patch("app.llm.routes.call_claude", return_value=mock_response):
        response = client.post(f"/api/companies/{cid}/analyze/judgement")
    assert response.status_code == 200
    data = response.json()
    assert data["message"]["role"] == "assistant"
    assert data["message"]["score_suggestion"] == "1.10"
    assert "conversation_id" in data


def test_chat_message_calls_claude(client, db):
    cid = _login_with_company(client, db)
    mock_response = ("SCORE: 1.30\nBEGRUENDUNG: Chat test", Decimal("1.30"))
    with patch("app.llm.routes.call_claude", return_value=mock_response):
        response = client.post(
            f"/api/companies/{cid}/chat/judgement",
            json={"message": "Was ist die Qualitaet?"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["message"]["role"] == "assistant"
    assert "conversation_id" in data


def test_chat_history_after_analyze(client, db):
    cid = _login_with_company(client, db)
    mock_response = ("SCORE: 1.10\nBEGRUENDUNG: Test", Decimal("1.10"))
    with patch("app.llm.routes.call_claude", return_value=mock_response):
        client.post(f"/api/companies/{cid}/analyze/judgement")

    response = client.get(f"/api/companies/{cid}/chat/judgement/history")
    assert response.status_code == 200
    data = response.json()
    assert len(data["messages"]) == 2
    roles = [m["role"] for m in data["messages"]]
    assert roles == ["user", "assistant"]


def test_analyze_unknown_company_returns_404(client, db):
    user = User(email="x@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": "x@example.com", "password": "pw1234"})
    response = client.post("/api/companies/00000000-0000-0000-0000-000000000001/analyze/judgement")
    assert response.status_code == 404
