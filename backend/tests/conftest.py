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

    # Der XBRL-Provider-Anker (FY-Refresh, provider_anchor.py) ruft die
    # Provider-Kette direkt — Default leer, sonst fetcht jeder FY-Refresh-
    # Test live gegen EDGAR/ESEF/Yahoo. Anker-Tests patchen
    # app.values.provider_anchor.get_providers explizit.
    import app.values.provider_anchor as anchor_mod

    monkeypatch.setattr(anchor_mod, "get_providers", lambda key: [])

    # Die 8-K-Adjusted-Anreicherung (adjusted_enrichment.py) ruft EDGAR
    # (Submissions/Exhibits) direkt — CIK-Aufloesung default None, sonst
    # fetcht jeder Refresh-Test mit US-ISIN live. Enrichment-Tests patchen
    # _resolve_cik explizit.
    import app.values.adjusted_enrichment as adj_mod

    monkeypatch.setattr(adj_mod, "_resolve_cik", lambda ticker: None)

    # Die Dokument-Stufe der Statement-Recherche (statement_research.py)
    # laedt Berichts-PDFs via httpx — Default None (kein Live-Netz).
    # Dokument-Tests patchen _download_document explizit.
    import app.values.statement_research as sr_mod

    monkeypatch.setattr(sr_mod, "_download_document", lambda url: None)

    # Kein Test darf real gegen die Anthropic-API laufen. Tests, die
    # LLM-Pfade brauchen, mocken get_client bzw. die _call_claude-Ebene.
    def _no_llm_in_tests():
        raise RuntimeError("Live-Anthropic-Call in Tests blockiert — mocke get_client")

    import app.llm.claude as claude_mod

    monkeypatch.setattr(claude_mod, "get_client", _no_llm_in_tests)
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


@pytest.fixture
def us_company(db):
    """Minimal US-Company fuer Orchestrator-/Provider-Tests (ISIN-Pflicht
    seit den ISIN-only-Company-Creation-Commits). Direkt per ORM angelegt
    (kein API-Client noetig) — Pattern wie tests/test_balance_carry_forward.py.
    """
    from app.auth.models import User
    from app.auth.security import hash_password
    from app.companies.models import Company
    from app.portfolios.models import Portfolio
    from tests.test_values import _seed_catalog

    _seed_catalog(db)
    user = User(email="orchestrator@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.flush()
    portfolio = Portfolio(name="P", owner_user_id=user.id)
    db.add(portfolio)
    db.flush()
    company = Company(
        portfolio_id=portfolio.id, name="TestCo US", ticker="TST",
        isin="US0001234567", currency="USD",
        fiscal_year_end_month=12, fiscal_year_end_day=31,
    )
    db.add(company)
    db.commit()
    return company
