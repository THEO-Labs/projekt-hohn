# Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lauffähiges Skelett des Hohn-Rendite-Tools - Backend (FastAPI) + Frontend (React+Vite+Shadcn) im Single-Container-Setup, Postgres-Anbindung, Auth (Login fuer 2-5 User), CRUD für Portfolios und Firmen.

**Architecture:** FastAPI serviert `/api/*` und mountet das gebuilte React-`dist/` unter `/`. Postgres lokal in Docker (spaeter AWS RDS). JWT in HttpOnly-Cookie. SQLAlchemy 2.x + Alembic. Vite+TS+Tailwind+Shadcn. Alle Aenderungen via TDD wo moeglich.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, pydantic-settings, passlib[bcrypt], python-jose, httpx, pytest, respx, pytest-postgresql. Vite, React 18, TypeScript, Tailwind, Shadcn/ui, Vitest, MSW, React Testing Library. Docker, docker-compose.

**Spec:** `docs/superpowers/specs/2026-04-16-turning-point-hohn-rendite-design.md`

---

## File Structure (was dieser Plan anlegt)

```
projekt-hohn/
├── README.md
├── .gitignore
├── docker-compose.yml          # Postgres + (later) Backend-Container fuer Dev
├── Dockerfile                  # multi-stage: build frontend, then run backend
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── .env.example
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py             # FastAPI App-Factory + Static-Mount
│   │   ├── config.py           # Settings (pydantic-settings)
│   │   ├── db.py               # Engine, Session, Base
│   │   ├── auth/
│   │   │   ├── __init__.py
│   │   │   ├── models.py       # User
│   │   │   ├── schemas.py
│   │   │   ├── security.py     # password hashing + JWT helpers
│   │   │   ├── deps.py         # current_user dependency
│   │   │   └── routes.py       # POST /login, /logout, GET /me
│   │   ├── portfolios/
│   │   │   ├── __init__.py
│   │   │   ├── models.py
│   │   │   ├── schemas.py
│   │   │   └── routes.py
│   │   ├── companies/
│   │   │   ├── __init__.py
│   │   │   ├── models.py
│   │   │   ├── schemas.py
│   │   │   └── routes.py
│   │   └── alembic/
│   │       ├── env.py
│   │       └── versions/
│   ├── scripts/
│   │   └── create_user.py      # CLI: User anlegen
│   └── tests/
│       ├── conftest.py
│       ├── test_auth.py
│       ├── test_portfolios.py
│       └── test_companies.py
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── tsconfig.json
    ├── tailwind.config.ts
    ├── postcss.config.js
    ├── index.html
    ├── components.json         # Shadcn config
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── index.css
        ├── api/
        │   ├── client.ts
        │   ├── auth.ts
        │   ├── portfolios.ts
        │   └── companies.ts
        ├── hooks/
        │   └── useAuth.ts
        ├── pages/
        │   ├── LoginPage.tsx
        │   ├── PortfolioListPage.tsx
        │   └── PortfolioDetailPage.tsx
        ├── components/
        │   └── ui/             # Shadcn-Komponenten (button, input, dialog, etc.)
        ├── lib/
        │   ├── i18n.ts
        │   └── utils.ts        # Shadcn cn-helper
        └── tests/
            ├── setup.ts
            └── LoginPage.test.tsx
```

---

## Task 1: Repo-Init und Toplevel-Files

**Files:**
- Create: `.gitignore`
- Create: `README.md`
- Create: `docker-compose.yml`

- [ ] **Step 1: `.gitignore` schreiben**

```gitignore
# Python
__pycache__/
*.pyc
*.pyo
.venv/
venv/
.pytest_cache/
.coverage
*.egg-info/

# Node
node_modules/
dist/
.vite/

# Env
.env
.env.local
.env.*.local

# OS
.DS_Store
Thumbs.db

# IDE
.idea/
.vscode/
*.swp
```

- [ ] **Step 2: `README.md` minimal anlegen**

```markdown
# Turning Point Hohn-Rendite Tool

Internes Tool fuer Turning Point Investments zur Berechnung der erweiterten Hohn-Rendite.

## Lokal starten

Voraussetzungen: Docker, Python 3.12, Node 20+

```bash
docker compose up -d db
cd backend && uv sync && uv run alembic upgrade head
cd backend && uv run uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev
```

Backend: http://localhost:8000
Frontend: http://localhost:5173 (Dev), oder http://localhost:8000 (Prod-Build)

Siehe `docs/superpowers/specs/` fuer Architektur und `docs/superpowers/plans/` fuer Implementation Plans.
```

- [ ] **Step 3: `docker-compose.yml` (nur DB fuer Dev)**

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: hohn
      POSTGRES_PASSWORD: hohn_dev
      POSTGRES_DB: hohn
    ports:
      - "5432:5432"
    volumes:
      - hohn_db:/var/lib/postgresql/data

volumes:
  hohn_db:
```

- [ ] **Step 4: Commit**

```bash
git add .gitignore README.md docker-compose.yml
git commit -m "chore: repo-init, gitignore and dev docker-compose"
```

---

## Task 2: Backend-Skelett (FastAPI + Konfiguration)

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/.env.example`
- Create: `backend/app/__init__.py`
- Create: `backend/app/config.py`
- Create: `backend/app/main.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_main.py`

- [ ] **Step 1: `backend/pyproject.toml`**

```toml
[project]
name = "hohn-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy>=2.0",
    "alembic>=1.13",
    "psycopg[binary]>=3.2",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "passlib[bcrypt]>=1.7.4",
    "python-jose[cryptography]>=3.3",
    "httpx>=0.27",
]

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "respx>=0.21",
    "pytest-postgresql>=6",
    "ruff>=0.7",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
target-version = "py312"
```

- [ ] **Step 2: `backend/.env.example`**

```
DATABASE_URL=postgresql+psycopg://hohn:hohn_dev@localhost:5432/hohn
JWT_SECRET=change-me-in-prod
JWT_ALGORITHM=HS256
JWT_TTL_MINUTES=480
COOKIE_SECURE=false
ALLOWED_ORIGINS=http://localhost:5173
```

- [ ] **Step 3: `backend/app/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 480
    cookie_secure: bool = True
    allowed_origins: str = ""

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


settings = Settings()
```

- [ ] **Step 4: Test schreiben (failing test first)**

`backend/tests/test_main.py`:

```python
from fastapi.testclient import TestClient

from app.main import app


def test_healthcheck():
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 5: Test ausfuehren - sollte fehlschlagen**

Run: `cd backend && uv run pytest tests/test_main.py -v`
Expected: ImportError oder 404

- [ ] **Step 6: `backend/app/main.py` minimal implementieren**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings


def create_app() -> FastAPI:
    app = FastAPI(title="Hohn-Rendite Tool")

    if settings.origins_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.origins_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 7: Test laeuft gruen**

Run: `cd backend && uv run pytest tests/test_main.py -v`
Expected: PASS

- [ ] **Step 8: `backend/tests/conftest.py` (Test-Setup-Stub)**

```python
import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://hohn:hohn_dev@localhost:5432/hohn_test")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("COOKIE_SECURE", "false")
```

- [ ] **Step 9: Commit**

```bash
git add backend/
git commit -m "feat(backend): FastAPI scaffold with health endpoint and config"
```

---

## Task 3: Datenbank-Setup (SQLAlchemy + Alembic)

**Files:**
- Create: `backend/app/db.py`
- Create: `backend/alembic.ini`
- Create: `backend/app/alembic/env.py`
- Create: `backend/app/alembic/versions/.gitkeep`

- [ ] **Step 1: `backend/app/db.py`**

```python
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 2: `backend/alembic.ini`**

```ini
[alembic]
script_location = app/alembic
prepend_sys_path = .

[loggers]
keys = root

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

- [ ] **Step 3: `backend/app/alembic/env.py`**

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.db import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Versions-Verzeichnis sicherstellen**

`backend/app/alembic/versions/.gitkeep` (leere Datei)

- [ ] **Step 5: Postgres lokal starten + Verbindung pruefen**

Run:
```bash
docker compose up -d db
cd backend && uv run python -c "from app.db import engine; engine.connect().close(); print('OK')"
```
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add backend/
git commit -m "feat(backend): SQLAlchemy base, Alembic config and env"
```

---

## Task 4: User-Modell + erste Migration

**Files:**
- Create: `backend/app/auth/__init__.py`
- Create: `backend/app/auth/models.py`
- Generated: `backend/app/alembic/versions/0001_users.py` (alembic erzeugt)

- [ ] **Step 1: `backend/app/auth/models.py`**

```python
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 2: `backend/app/auth/__init__.py` - Modell registrieren damit Alembic es findet**

```python
from app.auth.models import User  # noqa: F401
```

- [ ] **Step 3: `backend/app/main.py` muss `app.auth` importieren - sonst kennt Alembic Base.metadata nicht**

Edit `backend/app/main.py`, ergaenze ganz oben (vor create_app):

```python
import app.auth  # noqa: F401  # Modelle registrieren
```

- [ ] **Step 4: Migration generieren**

Run:
```bash
cd backend && uv run alembic revision --autogenerate -m "users"
```
Expected: neue Datei in `backend/app/alembic/versions/`. Pruefen dass `users`-Tabelle erzeugt wird.

- [ ] **Step 5: Migration anwenden**

Run: `cd backend && uv run alembic upgrade head`
Expected: keine Fehler. Mit `psql` oder `\d users` pruefen.

- [ ] **Step 6: Commit**

```bash
git add backend/
git commit -m "feat(backend): User model and initial migration"
```

---

## Task 5: Password-Hashing + JWT-Helpers (TDD)

**Files:**
- Create: `backend/app/auth/security.py`
- Create: `backend/tests/test_security.py`

- [ ] **Step 1: Test schreiben**

`backend/tests/test_security.py`:

```python
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
```

- [ ] **Step 2: Test fehlschlagen lassen**

Run: `cd backend && uv run pytest tests/test_security.py -v`
Expected: ImportError

- [ ] **Step 3: `backend/app/auth/security.py` implementieren**

```python
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def create_access_token(subject: str, ttl: timedelta | None = None) -> str:
    expire = datetime.now(timezone.utc) + (ttl or timedelta(minutes=settings.jwt_ttl_minutes))
    payload: dict[str, Any] = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
```

- [ ] **Step 4: Test gruen**

Run: `cd backend && uv run pytest tests/test_security.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/auth/security.py backend/tests/test_security.py
git commit -m "feat(auth): password hashing and JWT helpers with tests"
```

---

## Task 6: Auth-Routen + current_user Dependency (TDD)

**Files:**
- Create: `backend/app/auth/schemas.py`
- Create: `backend/app/auth/deps.py`
- Create: `backend/app/auth/routes.py`
- Create: `backend/tests/test_auth.py`
- Modify: `backend/app/main.py` (Router registrieren)
- Modify: `backend/tests/conftest.py` (DB-Fixtures)

- [ ] **Step 1: DB-Fixtures in conftest.py erweitern**

Erweitere `backend/tests/conftest.py`:

```python
import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://hohn:hohn_dev@localhost:5432/hohn_test")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("COOKIE_SECURE", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(os.environ["DATABASE_URL"], future=True)
    yield eng


@pytest.fixture
def db(engine):
    # Jedes Test-Verfahren bekommt eine frische Schema-Reinstellung -
    # Tests commiten bewusst (Transaction/Cookie-Flow), also rollback reicht nicht.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, future=True)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
```

Hinweis: `hohn_test`-DB muss in Postgres existieren. Vorab:
```bash
docker compose exec db createdb -U hohn hohn_test
```

- [ ] **Step 2: Tests schreiben (failing)**

`backend/tests/test_auth.py`:

```python
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
```

- [ ] **Step 3: Tests fehlschlagen lassen**

Run: `cd backend && uv run pytest tests/test_auth.py -v`
Expected: ImportError oder 404

- [ ] **Step 4: `backend/app/auth/schemas.py`**

```python
from uuid import UUID

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: UUID
    email: EmailStr

    class Config:
        from_attributes = True
```

- [ ] **Step 5: `backend/app/auth/deps.py`**

```python
from uuid import UUID

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.security import decode_token
from app.db import get_db


def current_user(
    access_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_token(access_token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    try:
        user_id = UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user
```

- [ ] **Step 6: `backend/app/auth/routes.py`**

```python
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.models import User
from app.auth.schemas import LoginRequest, UserOut
from app.auth.security import create_access_token, verify_password
from app.config import settings
from app.db import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "access_token"


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> User:
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
```

- [ ] **Step 7: Router in `main.py` registrieren**

Edit `backend/app/main.py`, in `create_app()` vor `return app`:

```python
    from app.auth.routes import router as auth_router
    app.include_router(auth_router)
```

- [ ] **Step 8: Tests gruen**

Run: `cd backend && uv run pytest tests/test_auth.py -v`
Expected: 5 PASS

- [ ] **Step 9: Commit**

```bash
git add backend/
git commit -m "feat(auth): login/logout/me endpoints with JWT cookie auth"
```

---

## Task 7: CLI-Skript zum Anlegen eines Users

**Files:**
- Create: `backend/scripts/__init__.py`
- Create: `backend/scripts/create_user.py`

- [ ] **Step 1: `backend/scripts/__init__.py` (leer)**

- [ ] **Step 2: `backend/scripts/create_user.py`**

```python
import argparse
from getpass import getpass

from app.auth.models import User
from app.auth.security import hash_password
from app.db import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a new user")
    parser.add_argument("--email", required=True)
    args = parser.parse_args()

    pw = getpass("Password: ")
    pw2 = getpass("Repeat: ")
    if pw != pw2:
        raise SystemExit("Passwords do not match")

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == args.email).one_or_none()
        if existing:
            raise SystemExit(f"User {args.email} already exists")
        user = User(email=args.email, password_hash=hash_password(pw))
        db.add(user)
        db.commit()
        print(f"Created user {args.email} (id={user.id})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Smoke-Test (manuell)**

Run:
```bash
cd backend && uv run python -m scripts.create_user --email test@example.com
```
(Passwoerter eingeben.) Erwartung: `Created user test@example.com (id=...)`.

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/
git commit -m "feat(auth): CLI script to create users"
```

---

## Task 8: Portfolio-Modell + Routen (TDD)

**Files:**
- Create: `backend/app/portfolios/__init__.py`
- Create: `backend/app/portfolios/models.py`
- Create: `backend/app/portfolios/schemas.py`
- Create: `backend/app/portfolios/routes.py`
- Create: `backend/tests/test_portfolios.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: `backend/app/portfolios/models.py`**

```python
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db import Base


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 2: `backend/app/portfolios/__init__.py`**

```python
from app.portfolios.models import Portfolio  # noqa: F401
```

- [ ] **Step 3: In `backend/app/main.py` ergaenzen**

Edit oben:

```python
import app.portfolios  # noqa: F401
```

- [ ] **Step 4: Migration generieren und anwenden**

Run:
```bash
cd backend && uv run alembic revision --autogenerate -m "portfolios"
cd backend && uv run alembic upgrade head
```

- [ ] **Step 5: `backend/app/portfolios/schemas.py`**

```python
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class PortfolioUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class PortfolioOut(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
```

- [ ] **Step 6: Tests schreiben**

`backend/tests/test_portfolios.py`:

```python
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
```

- [ ] **Step 7: Tests fehlschlagen lassen**

Run: `cd backend && uv run pytest tests/test_portfolios.py -v`
Expected: 404 / ImportError

- [ ] **Step 8: `backend/app/portfolios/routes.py`**

```python
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.models import User
from app.db import get_db
from app.portfolios.models import Portfolio
from app.portfolios.schemas import PortfolioCreate, PortfolioOut, PortfolioUpdate

router = APIRouter(prefix="/api/portfolios", tags=["portfolios"])


@router.get("", response_model=list[PortfolioOut])
def list_portfolios(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[Portfolio]:
    return db.query(Portfolio).filter(Portfolio.owner_user_id == user.id).order_by(Portfolio.created_at).all()


@router.post("", response_model=PortfolioOut, status_code=status.HTTP_201_CREATED)
def create_portfolio(
    payload: PortfolioCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Portfolio:
    portfolio = Portfolio(name=payload.name, owner_user_id=user.id)
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return portfolio


def _get_owned(db: Session, user: User, portfolio_id: UUID) -> Portfolio:
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).one_or_none()
    if not portfolio or portfolio.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found")
    return portfolio


@router.patch("/{portfolio_id}", response_model=PortfolioOut)
def update_portfolio(
    portfolio_id: UUID,
    payload: PortfolioUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Portfolio:
    portfolio = _get_owned(db, user, portfolio_id)
    portfolio.name = payload.name
    db.commit()
    db.refresh(portfolio)
    return portfolio


@router.delete("/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_portfolio(
    portfolio_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    portfolio = _get_owned(db, user, portfolio_id)
    db.delete(portfolio)
    db.commit()
```

- [ ] **Step 9: Router in `main.py` registrieren**

```python
    from app.portfolios.routes import router as portfolios_router
    app.include_router(portfolios_router)
```

- [ ] **Step 10: Tests gruen**

Run: `cd backend && uv run pytest tests/test_portfolios.py -v`
Expected: 5 PASS

- [ ] **Step 11: Commit**

```bash
git add backend/
git commit -m "feat(portfolios): CRUD endpoints scoped to owner"
```

---

## Task 9: Company-Modell + Routen (TDD)

**Files:**
- Create: `backend/app/companies/__init__.py`
- Create: `backend/app/companies/models.py`
- Create: `backend/app/companies/schemas.py`
- Create: `backend/app/companies/routes.py`
- Create: `backend/tests/test_companies.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: `backend/app/companies/models.py`**

```python
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    portfolio_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 2: `backend/app/companies/__init__.py`**

```python
from app.companies.models import Company  # noqa: F401
```

- [ ] **Step 3: In `main.py` ergaenzen**

```python
import app.companies  # noqa: F401
```

- [ ] **Step 4: Migration generieren + anwenden**

Run:
```bash
cd backend && uv run alembic revision --autogenerate -m "companies"
cd backend && uv run alembic upgrade head
```

- [ ] **Step 5: `backend/app/companies/schemas.py`**

```python
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    ticker: str = Field(min_length=1, max_length=32)
    isin: str | None = Field(default=None, max_length=12)
    currency: str = Field(min_length=3, max_length=3)


class CompanyUpdate(BaseModel):
    name: str | None = None
    ticker: str | None = None
    isin: str | None = None
    currency: str | None = None


class CompanyOut(BaseModel):
    id: UUID
    portfolio_id: UUID
    name: str
    ticker: str
    isin: str | None
    currency: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
```

- [ ] **Step 6: Tests schreiben**

`backend/tests/test_companies.py`:

```python
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
```

- [ ] **Step 7: Tests fehlschlagen lassen**

Run: `cd backend && uv run pytest tests/test_companies.py -v`

- [ ] **Step 8: `backend/app/companies/routes.py`**

```python
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.models import User
from app.companies.models import Company
from app.companies.schemas import CompanyCreate, CompanyOut, CompanyUpdate
from app.db import get_db
from app.portfolios.models import Portfolio

portfolio_scoped = APIRouter(prefix="/api/portfolios/{portfolio_id}/companies", tags=["companies"])
company_router = APIRouter(prefix="/api/companies", tags=["companies"])


def _get_owned_portfolio(db: Session, user: User, portfolio_id: UUID) -> Portfolio:
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).one_or_none()
    if not portfolio or portfolio.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found")
    return portfolio


def _get_owned_company(db: Session, user: User, company_id: UUID) -> Company:
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    _get_owned_portfolio(db, user, company.portfolio_id)
    return company


@portfolio_scoped.get("", response_model=list[CompanyOut])
def list_companies(
    portfolio_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[Company]:
    _get_owned_portfolio(db, user, portfolio_id)
    return (
        db.query(Company)
        .filter(Company.portfolio_id == portfolio_id)
        .order_by(Company.created_at)
        .all()
    )


@portfolio_scoped.post("", response_model=CompanyOut, status_code=status.HTTP_201_CREATED)
def create_company(
    portfolio_id: UUID,
    payload: CompanyCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Company:
    _get_owned_portfolio(db, user, portfolio_id)
    company = Company(portfolio_id=portfolio_id, **payload.model_dump())
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


@company_router.patch("/{company_id}", response_model=CompanyOut)
def update_company(
    company_id: UUID,
    payload: CompanyUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Company:
    company = _get_owned_company(db, user, company_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(company, key, value)
    db.commit()
    db.refresh(company)
    return company


@company_router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_company(
    company_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    company = _get_owned_company(db, user, company_id)
    db.delete(company)
    db.commit()
```

- [ ] **Step 9: Beide Router in `main.py` registrieren**

```python
    from app.companies.routes import portfolio_scoped, company_router
    app.include_router(portfolio_scoped)
    app.include_router(company_router)
```

- [ ] **Step 10: Tests gruen**

Run: `cd backend && uv run pytest tests/test_companies.py -v`
Expected: 4 PASS

- [ ] **Step 11: Commit**

```bash
git add backend/
git commit -m "feat(companies): CRUD endpoints scoped to portfolio owner"
```

---

## Task 10: Frontend-Skelett (Vite + React + TS + Tailwind)

**Files:**
- Create: `frontend/package.json` (via `npm create vite@latest`)
- Create: alle restlichen Vite-Dateien
- Create: Tailwind-Setup

- [ ] **Step 1: Vite + React + TS scaffolden**

Run:
```bash
cd /Users/till-olelohse/projekt-hohn
npm create vite@latest frontend -- --template react-ts
cd frontend && npm install
```

- [ ] **Step 2: Tailwind v3 installieren (v4 hat anderes API - bewusst pinnen)**

Run:
```bash
cd frontend
npm install -D "tailwindcss@^3" postcss autoprefixer
npx tailwindcss init -p
```

- [ ] **Step 3: `frontend/tailwind.config.ts` schreiben**

```ts
import type { Config } from "tailwindcss";

export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: { extend: {} },
  plugins: [],
} satisfies Config;
```

- [ ] **Step 4: `frontend/src/index.css` ersetzen**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 5: `frontend/vite.config.ts` ergaenzen (Proxy auf Backend)**

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
```

- [ ] **Step 6: `frontend/tsconfig.json` Pfad-Alias ergaenzen**

In `compilerOptions` ergaenzen:
```json
    "baseUrl": ".",
    "paths": { "@/*": ["src/*"] }
```

- [ ] **Step 7: Smoke-Test**

Run:
```bash
cd frontend && npm run dev
```
Expected: Vite startet auf 5173, Default-React-Page laedt im Browser.

Stoppe wieder mit Ctrl+C.

- [ ] **Step 8: Commit**

```bash
git add frontend/
git commit -m "feat(frontend): Vite + React + TS + Tailwind scaffold with Vite proxy"
```

---

## Task 11: Shadcn-Setup + lib/utils

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/components.json`
- Create: `frontend/src/lib/utils.ts`

- [ ] **Step 1: Shadcn CLI ausfuehren (initialisiert ohne Interaktion)**

Run:
```bash
cd frontend
npx shadcn@latest init -y -d
```

(Default: TypeScript, Tailwind, src-Pfad, alias `@/`).

- [ ] **Step 2: Erste Komponenten installieren**

```bash
npx shadcn@latest add button input label dialog card sonner
```

Erwartung: `frontend/src/components/ui/` enthaelt jetzt `button.tsx`, `input.tsx`, etc.

- [ ] **Step 3: Smoke-Test**

Edit `frontend/src/App.tsx` testweise:

```tsx
import { Button } from "@/components/ui/button";

export default function App() {
  return <div className="p-8"><Button>Test</Button></div>;
}
```

Run `npm run dev` -> Button erscheint mit Shadcn-Styles. Wieder revertieren oder im naechsten Step ueberschreiben.

- [ ] **Step 4: Commit**

```bash
git add frontend/
git commit -m "feat(frontend): Shadcn/ui setup with base components"
```

---

## Task 12: API-Client + i18n + useAuth-Hook

**Files:**
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/auth.ts`
- Create: `frontend/src/api/portfolios.ts`
- Create: `frontend/src/api/companies.ts`
- Create: `frontend/src/lib/i18n.ts`
- Create: `frontend/src/hooks/useAuth.ts`

- [ ] **Step 1: `frontend/src/api/client.ts`**

```ts
export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : "API error");
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new ApiError(response.status, detail?.detail ?? response.statusText);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
```

- [ ] **Step 2: `frontend/src/api/auth.ts`**

```ts
import { api } from "./client";

export type User = { id: string; email: string };

export const login = (email: string, password: string) =>
  api<User>("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });

export const logout = () => api<void>("/api/auth/logout", { method: "POST" });

export const me = () => api<User>("/api/auth/me");
```

- [ ] **Step 3: `frontend/src/api/portfolios.ts`**

```ts
import { api } from "./client";

export type Portfolio = {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
};

export const listPortfolios = () => api<Portfolio[]>("/api/portfolios");
export const createPortfolio = (name: string) =>
  api<Portfolio>("/api/portfolios", { method: "POST", body: JSON.stringify({ name }) });
export const updatePortfolio = (id: string, name: string) =>
  api<Portfolio>(`/api/portfolios/${id}`, { method: "PATCH", body: JSON.stringify({ name }) });
export const deletePortfolio = (id: string) =>
  api<void>(`/api/portfolios/${id}`, { method: "DELETE" });
```

- [ ] **Step 4: `frontend/src/api/companies.ts`**

```ts
import { api } from "./client";

export type Company = {
  id: string;
  portfolio_id: string;
  name: string;
  ticker: string;
  isin: string | null;
  currency: string;
  created_at: string;
  updated_at: string;
};

export type CompanyCreate = {
  name: string;
  ticker: string;
  isin?: string;
  currency: string;
};

export const listCompanies = (portfolioId: string) =>
  api<Company[]>(`/api/portfolios/${portfolioId}/companies`);

export const createCompany = (portfolioId: string, payload: CompanyCreate) =>
  api<Company>(`/api/portfolios/${portfolioId}/companies`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateCompany = (id: string, payload: Partial<CompanyCreate>) =>
  api<Company>(`/api/companies/${id}`, { method: "PATCH", body: JSON.stringify(payload) });

export const deleteCompany = (id: string) =>
  api<void>(`/api/companies/${id}`, { method: "DELETE" });
```

- [ ] **Step 5: `frontend/src/lib/i18n.ts`**

```ts
export const t = {
  appTitle: "Hohn-Rendite Tool",
  login: "Anmelden",
  logout: "Abmelden",
  email: "E-Mail",
  password: "Passwort",
  loginFailed: "Anmeldung fehlgeschlagen",
  portfolios: "Portfolios",
  newPortfolio: "Neues Portfolio",
  portfolioName: "Portfolio-Name",
  companies: "Firmen",
  newCompany: "Neue Firma",
  companyName: "Firmenname",
  ticker: "Ticker",
  isin: "ISIN",
  currency: "Currency",
  save: "Speichern",
  cancel: "Abbrechen",
  delete: "Loeschen",
  edit: "Bearbeiten",
  back: "Zurueck",
};

export const terms = {
  marketCap: "Market Cap",
  ebitda: "EBITDA",
  fcfYield: "FCF Yield",
  hohnRendite: "Hohn-Rendite",
};
```

- [ ] **Step 6: `frontend/src/hooks/useAuth.ts`**

```ts
import { useEffect, useState } from "react";
import { ApiError } from "@/api/client";
import { login as apiLogin, logout as apiLogout, me, type User } from "@/api/auth";

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    me()
      .then(setUser)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) setUser(null);
        else throw err;
      })
      .finally(() => setLoading(false));
  }, []);

  const login = async (email: string, password: string) => {
    const u = await apiLogin(email, password);
    setUser(u);
    return u;
  };

  const logout = async () => {
    await apiLogout();
    setUser(null);
  };

  return { user, loading, login, logout };
}
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/
git commit -m "feat(frontend): API client, i18n, useAuth hook"
```

---

## Task 13: LoginPage + App-Router (TDD mit Vitest)

**Files:**
- Modify: `frontend/package.json` (Dev-Deps fuer Vitest + RTL + MSW)
- Create: `frontend/src/tests/setup.ts`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/src/pages/LoginPage.tsx`
- Create: `frontend/src/tests/LoginPage.test.tsx`
- Create: `frontend/src/pages/PortfolioListPage.tsx` (Stub)
- Create: `frontend/src/pages/PortfolioDetailPage.tsx` (Stub)
- Modify: `frontend/src/App.tsx` (Router)

- [ ] **Step 1: Test-Deps installieren**

Run:
```bash
cd frontend
npm install -D vitest @vitest/ui jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event msw react-router-dom
```

- [ ] **Step 2: `frontend/vitest.config.ts`**

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/tests/setup.ts"],
    globals: true,
  },
});
```

- [ ] **Step 3: `frontend/src/tests/setup.ts`**

```ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 4: `package.json` scripts ergaenzen**

In `frontend/package.json` `scripts`:
```json
    "test": "vitest run",
    "test:watch": "vitest"
```

- [ ] **Step 5: Test schreiben (failing)**

`frontend/src/tests/LoginPage.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LoginPage } from "@/pages/LoginPage";

describe("LoginPage", () => {
  it("ruft onLogin mit eingegebenen Credentials", async () => {
    const onLogin = vi.fn().mockResolvedValue(undefined);
    render(<LoginPage onLogin={onLogin} />);

    await userEvent.type(screen.getByLabelText(/E-Mail/i), "t@example.com");
    await userEvent.type(screen.getByLabelText(/Passwort/i), "pw1234");
    await userEvent.click(screen.getByRole("button", { name: /Anmelden/i }));

    expect(onLogin).toHaveBeenCalledWith("t@example.com", "pw1234");
  });

  it("zeigt Fehler an wenn Login fehlschlaegt", async () => {
    const onLogin = vi.fn().mockRejectedValue(new Error("nope"));
    render(<LoginPage onLogin={onLogin} />);

    await userEvent.type(screen.getByLabelText(/E-Mail/i), "t@example.com");
    await userEvent.type(screen.getByLabelText(/Passwort/i), "pw1234");
    await userEvent.click(screen.getByRole("button", { name: /Anmelden/i }));

    expect(await screen.findByText(/Anmeldung fehlgeschlagen/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 6: Test fehlschlagen lassen**

Run: `cd frontend && npm run test`
Expected: ImportError fuer LoginPage

- [ ] **Step 7: `frontend/src/pages/LoginPage.tsx` implementieren**

```tsx
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { t } from "@/lib/i18n";

type Props = { onLogin: (email: string, password: string) => Promise<void> };

export function LoginPage({ onLogin }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await onLogin(email, password);
    } catch {
      setError(t.loginFailed);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-lg border bg-white p-8 shadow-sm">
        <h1 className="text-xl font-semibold">{t.appTitle}</h1>
        <div className="space-y-2">
          <Label htmlFor="email">{t.email}</Label>
          <Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div className="space-y-2">
          <Label htmlFor="password">{t.password}</Label>
          <Input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <Button type="submit" disabled={submitting} className="w-full">
          {t.login}
        </Button>
      </form>
    </div>
  );
}
```

- [ ] **Step 8: Tests gruen**

Run: `cd frontend && npm run test`
Expected: 2 PASS

- [ ] **Step 9: Stub-Pages anlegen**

`frontend/src/pages/PortfolioListPage.tsx`:

```tsx
export function PortfolioListPage() {
  return <div className="p-8">Portfolios - kommt in Task 14</div>;
}
```

`frontend/src/pages/PortfolioDetailPage.tsx`:

```tsx
export function PortfolioDetailPage() {
  return <div className="p-8">Portfolio-Detail - kommt in Task 15</div>;
}
```

- [ ] **Step 10: `frontend/src/App.tsx` Router**

```tsx
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { LoginPage } from "@/pages/LoginPage";
import { PortfolioListPage } from "@/pages/PortfolioListPage";
import { PortfolioDetailPage } from "@/pages/PortfolioDetailPage";
import { useAuth } from "@/hooks/useAuth";

export default function App() {
  const { user, loading, login } = useAuth();

  if (loading) return <div className="p-8">Lade...</div>;

  return (
    <BrowserRouter>
      <Routes>
        {!user ? (
          <>
            <Route path="/login" element={<LoginPage onLogin={async (e, p) => { await login(e, p); }} />} />
            <Route path="*" element={<Navigate to="/login" replace />} />
          </>
        ) : (
          <>
            <Route path="/" element={<PortfolioListPage />} />
            <Route path="/portfolios/:id" element={<PortfolioDetailPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </>
        )}
      </Routes>
    </BrowserRouter>
  );
}
```

- [ ] **Step 11: Smoke-Test im Browser**

```bash
# in einem Terminal
cd backend && uv run uvicorn app.main:app --reload --port 8000
# in einem anderen
cd frontend && npm run dev
```

Browser: http://localhost:5173 -> Login-Seite. Mit dem in Task 7 angelegten User einloggen. Erwartet: Redirect auf `/` mit Stub-Page "Portfolios - kommt in Task 14".

- [ ] **Step 12: Commit**

```bash
git add frontend/
git commit -m "feat(frontend): LoginPage, router, useAuth integration"
```

---

## Task 14: PortfolioListPage (Liste, Anlegen, Loeschen)

**Files:**
- Modify: `frontend/src/pages/PortfolioListPage.tsx`
- Create: `frontend/src/components/AppHeader.tsx`

- [ ] **Step 1: `frontend/src/components/AppHeader.tsx`**

```tsx
import { Button } from "@/components/ui/button";
import { t } from "@/lib/i18n";

type Props = { email: string; onLogout: () => void; backHref?: string; title?: string };

export function AppHeader({ email, onLogout, title }: Props) {
  return (
    <header className="flex items-center justify-between border-b bg-white px-6 py-3">
      <h1 className="text-lg font-semibold">{title ?? t.appTitle}</h1>
      <div className="flex items-center gap-3 text-sm">
        <span className="text-slate-600">{email}</span>
        <Button variant="outline" size="sm" onClick={onLogout}>{t.logout}</Button>
      </div>
    </header>
  );
}
```

- [ ] **Step 2: `frontend/src/pages/PortfolioListPage.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { AppHeader } from "@/components/AppHeader";
import { useAuth } from "@/hooks/useAuth";
import {
  createPortfolio,
  deletePortfolio,
  listPortfolios,
  type Portfolio,
} from "@/api/portfolios";
import { t } from "@/lib/i18n";

export function PortfolioListPage() {
  const { user, logout } = useAuth();
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");

  const refresh = () => listPortfolios().then(setPortfolios);

  useEffect(() => {
    refresh();
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    await createPortfolio(name);
    setName("");
    setOpen(false);
    refresh();
  };

  const remove = async (id: string) => {
    await deletePortfolio(id);
    refresh();
  };

  if (!user) return null;

  return (
    <div className="min-h-screen bg-slate-50">
      <AppHeader email={user.email} onLogout={logout} />
      <main className="mx-auto max-w-4xl p-6">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-2xl font-semibold">{t.portfolios}</h2>
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button>{t.newPortfolio}</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{t.newPortfolio}</DialogTitle>
              </DialogHeader>
              <form onSubmit={submit} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="pname">{t.portfolioName}</Label>
                  <Input id="pname" value={name} onChange={(e) => setName(e.target.value)} required />
                </div>
                <div className="flex justify-end gap-2">
                  <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                    {t.cancel}
                  </Button>
                  <Button type="submit">{t.save}</Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>

        <ul className="divide-y rounded-lg border bg-white">
          {portfolios.map((p) => (
            <li key={p.id} className="flex items-center justify-between px-4 py-3">
              <Link to={`/portfolios/${p.id}`} className="font-medium hover:underline">
                {p.name}
              </Link>
              <Button variant="ghost" size="sm" onClick={() => remove(p.id)}>
                {t.delete}
              </Button>
            </li>
          ))}
          {portfolios.length === 0 && (
            <li className="px-4 py-8 text-center text-sm text-slate-500">Noch keine Portfolios</li>
          )}
        </ul>
      </main>
    </div>
  );
}
```

- [ ] **Step 3: Smoke-Test im Browser**

Backend + Frontend laufen lassen, einloggen, Portfolio anlegen und loeschen. Erwartet: funktioniert end-to-end.

- [ ] **Step 4: Commit**

```bash
git add frontend/
git commit -m "feat(frontend): PortfolioListPage with create and delete"
```

---

## Task 15: PortfolioDetailPage (Firmen-Liste, Anlegen, Loeschen)

**Files:**
- Modify: `frontend/src/pages/PortfolioDetailPage.tsx`

- [ ] **Step 1: `frontend/src/pages/PortfolioDetailPage.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { AppHeader } from "@/components/AppHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { useAuth } from "@/hooks/useAuth";
import {
  createCompany,
  deleteCompany,
  listCompanies,
  type Company,
} from "@/api/companies";
import { t } from "@/lib/i18n";

export function PortfolioDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const [companies, setCompanies] = useState<Company[]>([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", ticker: "", isin: "", currency: "EUR" });

  const refresh = () => {
    if (id) listCompanies(id).then(setCompanies);
  };

  useEffect(() => {
    refresh();
  }, [id]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!id) return;
    await createCompany(id, {
      name: form.name,
      ticker: form.ticker,
      currency: form.currency,
      isin: form.isin || undefined,
    });
    setForm({ name: "", ticker: "", isin: "", currency: "EUR" });
    setOpen(false);
    refresh();
  };

  const remove = async (cid: string) => {
    await deleteCompany(cid);
    refresh();
  };

  if (!user) return null;

  return (
    <div className="min-h-screen bg-slate-50">
      <AppHeader email={user.email} onLogout={logout} />
      <main className="mx-auto max-w-4xl p-6">
        <div className="mb-4">
          <Link to="/" className="text-sm text-slate-600 hover:underline">{t.back}</Link>
        </div>
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-2xl font-semibold">{t.companies}</h2>
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button>{t.newCompany}</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{t.newCompany}</DialogTitle>
              </DialogHeader>
              <form onSubmit={submit} className="space-y-4">
                <Field label={t.companyName} value={form.name}
                       onChange={(v) => setForm({ ...form, name: v })} required />
                <Field label={t.ticker} value={form.ticker}
                       onChange={(v) => setForm({ ...form, ticker: v })} required />
                <Field label={t.isin} value={form.isin}
                       onChange={(v) => setForm({ ...form, isin: v })} />
                <Field label={t.currency} value={form.currency}
                       onChange={(v) => setForm({ ...form, currency: v.toUpperCase() })} required />
                <div className="flex justify-end gap-2">
                  <Button type="button" variant="ghost" onClick={() => setOpen(false)}>{t.cancel}</Button>
                  <Button type="submit">{t.save}</Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>

        <ul className="divide-y rounded-lg border bg-white">
          {companies.map((c) => (
            <li key={c.id} className="flex items-center justify-between px-4 py-3">
              <div>
                <div className="font-medium">{c.name}</div>
                <div className="text-sm text-slate-600">{c.ticker} - {c.currency}</div>
              </div>
              <Button variant="ghost" size="sm" onClick={() => remove(c.id)}>{t.delete}</Button>
            </li>
          ))}
          {companies.length === 0 && (
            <li className="px-4 py-8 text-center text-sm text-slate-500">Noch keine Firmen</li>
          )}
        </ul>
      </main>
    </div>
  );
}

function Field({
  label, value, onChange, required,
}: { label: string; value: string; onChange: (v: string) => void; required?: boolean }) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <Input value={value} onChange={(e) => onChange(e.target.value)} required={required} />
    </div>
  );
}
```

- [ ] **Step 2: Smoke-Test im Browser**

Einloggen -> Portfolio waehlen -> Firma anlegen + loeschen.

- [ ] **Step 3: Commit**

```bash
git add frontend/
git commit -m "feat(frontend): PortfolioDetailPage with company CRUD"
```

---

## Task 16: Single-Container Build (Dockerfile + Static-Mount)

**Files:**
- Create: `Dockerfile`
- Modify: `backend/app/main.py` (Static-Mount fuer Prod)
- Create: `.dockerignore`

- [ ] **Step 1: `.dockerignore`**

```
.git
node_modules
.venv
__pycache__
.pytest_cache
*.pyc
backend/.env
frontend/dist
docs
.idea
.vscode
```

- [ ] **Step 2: Multi-stage `Dockerfile`**

```dockerfile
# ---- Stage 1: Frontend Build ----
FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Backend Runtime ----
FROM python:3.12-slim AS backend
WORKDIR /app

RUN pip install --no-cache-dir uv

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev

COPY backend/ ./
COPY --from=frontend-build /frontend/dist ./static

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Hinweis: `uv.lock` ist nach erstem `uv sync` da. Falls noch nicht: vorher `cd backend && uv lock` ausfuehren.

- [ ] **Step 3: `backend/app/main.py` um Static-Mount ergaenzen**

In `create_app()` nach den Routern:

```python
    from pathlib import Path
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                from fastapi import HTTPException
                raise HTTPException(status_code=404)
            return FileResponse(static_dir / "index.html")
```

- [ ] **Step 4: Lockfile sicherstellen**

Run: `cd backend && uv lock`

- [ ] **Step 5: Build + Smoke-Test**

```bash
docker build -t hohn:dev .
docker run --rm -p 8000:8000 \
  -e DATABASE_URL=postgresql+psycopg://hohn:hohn_dev@host.docker.internal:5432/hohn \
  -e JWT_SECRET=dev-secret \
  -e COOKIE_SECURE=false \
  -e ALLOWED_ORIGINS=http://localhost:8000 \
  hohn:dev
```

Browser: http://localhost:8000 -> sollte die React-App laden, Login funktionieren, /api/health 200 liefern.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile .dockerignore backend/
git commit -m "feat(deploy): multi-stage Dockerfile with frontend bundled into backend"
```

---

## Task 17: Tests final, README aktualisieren, Tag

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Komplette Backend-Tests laufen lassen**

Run:
```bash
cd backend && uv run pytest -v
```
Expected: alle Tests gruen.

- [ ] **Step 2: Frontend-Tests laufen lassen**

Run:
```bash
cd frontend && npm run test
```
Expected: alle gruen.

- [ ] **Step 3: README ergaenzen mit "User anlegen"**

Vor "Backend / Frontend"-Block in `README.md` einfuegen (achte auf 4-Backtick-Outer-Fences damit die nested code-blocks korrekt rendern):

````markdown
## Ersten User anlegen

Nach `alembic upgrade head`:

```bash
cd backend && uv run python -m scripts.create_user --email du@example.com
```
````

- [ ] **Step 4: Final-Commit + Tag**

```bash
git add README.md
git commit -m "docs: README - user creation step"
git tag v0.1.0-foundation -m "Foundation: auth + portfolios + companies CRUD"
```

---

## Done-Kriterien Plan 1

- [ ] Alle Backend-Tests gruen (`cd backend && uv run pytest`)
- [ ] Alle Frontend-Tests gruen (`cd frontend && npm run test`)
- [ ] Docker-Build erfolgreich, Container startet
- [ ] Manuell: Login -> Portfolio anlegen -> Firma anlegen -> Loeschen funktioniert
- [ ] Tag `v0.1.0-foundation` gesetzt
- [ ] Bereit fuer Plan 2 (Values & Calculation)
