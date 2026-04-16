from datetime import timedelta

from app.auth.security import (
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_hash_password_roundtrip():
    pw = "supersecret"
    hashed = hash_password(pw)
    assert hashed != pw
    assert verify_password(pw, hashed) is True
    assert verify_password("wrong", hashed) is False


def test_jwt_roundtrip():
    token = create_access_token(subject="user-123", ttl=timedelta(minutes=5))
    payload = decode_token(token)
    assert payload["sub"] == "user-123"


def test_jwt_invalid_returns_none():
    assert decode_token("not-a-jwt") is None
