from datetime import datetime, timezone

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


@router.post("/login", response_model=UserOut)
@limiter.limit("5/minute")
def login(request: Request, payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> User:
    user = db.query(User).filter(User.email == payload.email).one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

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
