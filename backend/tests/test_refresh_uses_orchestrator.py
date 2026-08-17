"""Integrations-Test: refresh_company_values leitet die Werte-Beschaffung
komplett ueber den ValueOrchestrator (EDGAR-Anker + Perplexity-Luecken).
Kein Netz — die Adapter (yahoo_stammdaten/edgar_anchor) und PerplexityClient
werden gemockt.
"""
from decimal import Decimal

from app.auth.models import User
from app.llm.perplexity import PerplexityValue
from app.values.models import CompanyValue
from app.values.orchestrator import AnchorValue
from app.values.schemas import RefreshRequest


def _owner(db, company) -> User:
    from app.portfolios.models import Portfolio
    portfolio = db.query(Portfolio).filter(Portfolio.id == company.portfolio_id).one()
    return db.query(User).filter(User.id == portfolio.owner_user_id).one()


def test_refresh_persists_edgar_and_perplexity(db, us_company, monkeypatch):
    import app.values.adapters as adapters
    import app.values.orchestrator as orch_mod
    import app.llm.perplexity as perplexity_mod
    import app.values.routes as routes

    # Kein Netz: ISIN-Aufloesung + next-earnings laufen ueber get_providers.
    monkeypatch.setattr(routes, "get_providers", lambda key: [])
    # Client wird nur gebaut wenn ein Key gesetzt ist (Resilienz-Fix) — hier
    # soll der Perplexity-Pfad ausgeuebt werden, also Key setzen. routes.py
    # importiert `settings` lazy aus app.config, also dort patchen.
    import app.config as config_mod
    monkeypatch.setattr(config_mod.settings, "perplexity_api_key", "pk-test")

    running = orch_mod.running_fy_year(us_company)

    # Stammdaten (Feed) -> SNAPSHOT-Zeilen.
    monkeypatch.setattr(adapters, "yahoo_stammdaten", lambda company: {
        "market_cap": (Decimal("1000"), "USD"),
        "stock_price": (Decimal("10"), "USD"),
        "shares_outstanding": (Decimal("100"), "USD"),
    })

    # EDGAR-Anker: exakter net_income fuer das abgeschlossene Vorjahr.
    closed_year = running - 1
    monkeypatch.setattr(adapters, "edgar_anchor", lambda company, years: {
        ("net_income", closed_year): AnchorValue(
            Decimal("500"), "SEC EDGAR", "https://sec.gov/x", "USD"),
    })

    # Perplexity fuellt eine Luecke im abgeschlossenen Vorjahr.
    class FakePerplexity:
        def __init__(self, *a, **k):
            pass

        def fetch_period(self, **k):
            return {"revenue": PerplexityValue(
                value=2000.0, adjusted=None,
                source_url="https://pplx/x", source_title="t")}

        def fetch_consensus(self, **k):
            return {}

    monkeypatch.setattr(perplexity_mod, "PerplexityClient", FakePerplexity)

    user = _owner(db, us_company)
    payload = RefreshRequest(
        keys=["net_income", "revenue"], period_type="FY", period_year=running)
    refresh_company_values = __import__(
        "app.values.routes", fromlist=["refresh_company_values"]
    ).refresh_company_values
    refresh_company_values(company_id=us_company.id, payload=payload, user=user, db=db)

    # EDGAR-Wert persistiert (provider).
    ni = db.query(CompanyValue).filter_by(
        company_id=us_company.id, value_key="net_income",
        period_type="FY", period_year=closed_year).one()
    assert ni.numeric_value == Decimal("500")
    assert ni.source_name == "SEC EDGAR"
    assert ni.primary_method == "provider"

    # Perplexity-Wert persistiert (perplexity).
    rev = db.query(CompanyValue).filter_by(
        company_id=us_company.id, value_key="revenue",
        period_type="FY", period_year=closed_year).one()
    assert rev.numeric_value == Decimal("2000")
    assert rev.source_name == "Perplexity"
    assert rev.primary_method == "perplexity"

    # Stammdaten als SNAPSHOT (period_year=None).
    mc = db.query(CompanyValue).filter_by(
        company_id=us_company.id, value_key="market_cap",
        period_type="SNAPSHOT", period_year=None).one()
    assert mc.numeric_value == Decimal("1000")
    assert mc.source_name == "Market Data Feed"


def test_stammdaten_only_skips_fundamentals(db, us_company, monkeypatch):
    import app.values.adapters as adapters
    import app.llm.perplexity as perplexity_mod
    import app.values.routes as routes

    monkeypatch.setattr(routes, "get_providers", lambda key: [])

    monkeypatch.setattr(adapters, "yahoo_stammdaten", lambda company: {
        "market_cap": (Decimal("1234"), "USD"),
    })

    def _boom(company, years):
        raise AssertionError("edgar_anchor must not run in stammdaten_only mode")

    monkeypatch.setattr(adapters, "edgar_anchor", _boom)

    class BoomPerplexity:
        def __init__(self, *a, **k):
            pass

        def fetch_period(self, **k):
            raise AssertionError("perplexity must not run in stammdaten_only mode")

        def fetch_consensus(self, **k):
            raise AssertionError("perplexity must not run in stammdaten_only mode")

    monkeypatch.setattr(perplexity_mod, "PerplexityClient", BoomPerplexity)

    user = _owner(db, us_company)
    payload = RefreshRequest(
        keys=["market_cap"], period_type="SNAPSHOT", stammdaten_only=True)
    refresh_company_values = __import__(
        "app.values.routes", fromlist=["refresh_company_values"]
    ).refresh_company_values
    refresh_company_values(company_id=us_company.id, payload=payload, user=user, db=db)

    mc = db.query(CompanyValue).filter_by(
        company_id=us_company.id, value_key="market_cap",
        period_type="SNAPSHOT", period_year=None).one()
    assert mc.numeric_value == Decimal("1234")
    assert mc.source_name == "Market Data Feed"
