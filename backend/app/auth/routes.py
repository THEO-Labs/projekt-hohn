import threading
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.models import User
from app.auth.schemas import LoginRequest, UserOut
from app.auth.security import create_access_token, verify_password
from app.config import settings
from app.db import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])
limiter = Limiter(key_func=get_remote_address)

COOKIE_NAME = "access_token"

# Account-Lockout: per-email failure tracking
_LOCK = threading.Lock()
_FAILED: dict[str, list[datetime]] = {}
LOCKOUT_THRESHOLD = 5
LOCKOUT_WINDOW = timedelta(minutes=15)
LOCKOUT_DURATION = timedelta(minutes=15)


def _is_locked(email: str) -> tuple[bool, int]:
    now = datetime.now(timezone.utc)
    with _LOCK:
        attempts = _FAILED.get(email, [])
        recent = [t for t in attempts if now - t < LOCKOUT_WINDOW]
        _FAILED[email] = recent
        if len(recent) >= LOCKOUT_THRESHOLD:
            unlock_at = recent[-1] + LOCKOUT_DURATION
            if unlock_at > now:
                return (True, int((unlock_at - now).total_seconds()))
        return (False, 0)


def _record_failure(email: str) -> None:
    with _LOCK:
        _FAILED.setdefault(email, []).append(datetime.now(timezone.utc))


def _clear_failures(email: str) -> None:
    with _LOCK:
        _FAILED.pop(email, None)


@router.post("/login", response_model=UserOut)
@limiter.limit("5/minute")
def login(request: Request, payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> User:
    locked, remaining = _is_locked(payload.email)
    if locked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Account temporarily locked after too many failed attempts. Try again in {remaining // 60 + 1} minute(s).",
        )
    user = db.query(User).filter(User.email == payload.email).one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        _record_failure(payload.email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    _clear_failures(payload.email)

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    token = create_access_token(subject=str(user.id))
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.jwt_ttl_minutes * 60,
    )
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user
