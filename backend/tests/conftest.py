import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://hohn:hohn_dev@localhost:5433/hohn_test")
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
    eng = create_engine(
        os.environ["DATABASE_URL"],
        future=True,
        connect_args={"prepare_threshold": 0},
    )
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


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    from app.auth.routes import limiter
    limiter.reset()
    yield


@pytest.fixture
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
