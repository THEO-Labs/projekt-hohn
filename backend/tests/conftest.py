import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://hohn:hohn_dev@localhost:5433/hohn_test")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

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
def no_live_network(monkeypatch):
    """Tests muessen hermetisch sein: keine Live-Calls zu Yahoo/EDGAR.

    Ohne diese Patches holt die Company-Anlage das echte FY-Ende und die
    Recalc-Kaskade echte historische Market-Caps als FY+1-Anker — echte
    Weltdaten verschmutzen dann die geseedete Test-Welt (nichtdeterministisch).
    Provider-Fetches selbst mocken die Tests explizit via get_providers.
    """
    from app.providers.yahoo import YahooFinanceProvider

    monkeypatch.setattr(
        YahooFinanceProvider, "detect_fiscal_year_end", lambda self, ticker: None
    )
    import app.values.routes as values_routes

    monkeypatch.setattr(
        values_routes, "_fetch_and_store_historical_mcap",
        lambda db, ticker, company_id, year: None,
    )

    # Kein Test darf real gegen die Anthropic-API laufen. Tests, die die
    # Two-Stage-Pipeline brauchen, mocken research_two_stage direkt.
    def _no_llm_in_tests():
        raise RuntimeError("Live-Anthropic-Call in Tests blockiert — mocke research_two_stage/get_client")

    import app.llm.claude as claude_mod
    import scripts.two_stage_research as ts_mod

    monkeypatch.setattr(claude_mod, "get_client", _no_llm_in_tests)
    monkeypatch.setattr(ts_mod, "get_client", _no_llm_in_tests)
    yield


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    from app.auth.routes import limiter, _FAILED, _LOCK
    limiter.reset()
    with _LOCK:
        _FAILED.clear()
    yield


@pytest.fixture
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
