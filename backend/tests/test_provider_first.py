"""Tests fuer den US-Anker-Pfad im Refresh-Key-Loop
(_anchor_us_key_periods): EDGAR-XBRL statt LLM-Recherche. Hermetisch —
Provider via monkeypatch wie in test_quarter_anchor.py.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.providers.base import ProviderResult
from app.values.models import CompanyValue

CLOSED_YEAR = date.today().year - 1


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="pfirst@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.flush()
    portfolio = Portfolio(name="P", owner_user_id=user.id)
    db.add(portfolio)
    db.flush()
    # US-ISIN: der Pre-Anchor gated auf US-Filer (EDGAR-Quelle).
    comp = Company(
        portfolio_id=portfolio.id, name="TestCo", ticker="TST",
        currency="USD", isin="US0001234567",
        fiscal_year_end_month=12, fiscal_year_end_day=31,
    )
    db.add(comp)
    db.commit()
    return comp


class _Provider:
    """Mock mit fetch- und fetch_quarterly-Signatur des EdgarProviders."""
    name = "MockEdgar"

    def __init__(self, fy=None, quarters=None):
        self.fy = fy or {}              # (key, year) -> ProviderResult
        self.quarters = quarters or {}  # (key, year, quarter) -> ProviderResult
        self.fy_calls: list[tuple] = []
        self.q_calls: list[tuple] = []

    def fetch(self, ticker, key, period_type, period_year, **kwargs):
        self.fy_calls.append((key, period_year))
        return self.fy.get((key, period_year))

    def fetch_quarterly(self, ticker, key, period_year, quarter,
                        fy_end_month=None, fy_end_day=None):
        self.q_calls.append((key, period_year, quarter))
        return self.quarters.get((key, period_year, quarter))


def _res(value, source="SEC EDGAR"):
    return ProviderResult(
        value=Decimal(value), source_name=source,
        source_link="https://sec.gov/x", currency="USD",
    )


def _full_coverage(key, year):
    fy = {(key, year): _res("500", "SEC EDGAR 10-K")}
    quarters = {
        (key, year, q): _res(str(100 + i), f"SEC EDGAR 10-Q ({q})")
        for i, q in enumerate(("Q1", "Q2", "Q3", "Q4"))
    }
    return fy, quarters


def _rows(db, comp, key, year):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key == key,
            CompanyValue.period_year == year,
        )
        .all()
    )


# --- _anchor_us_key_periods (US-Refresh: EDGAR statt LLM) -------------------


def _run_anchor(db, monkeypatch, company, provider, key="net_income", year=CLOSED_YEAR):
    import app.values.provider_anchor as anchor_mod
    from app.values.routes import _anchor_us_key_periods

    monkeypatch.setattr(anchor_mod, "get_providers", lambda k: [provider])
    updated: list = []
    wrote = _anchor_us_key_periods(
        db=db, key=key, company=company,
        company_id=company.id, updated=updated, year=year,
    )
    db.commit()
    return wrote, updated


def test_anchor_us_key_periods_writes_provider_rows(db, company, monkeypatch):
    """US-Anker-Pfad: FY+Q1-Q4 aus EDGAR werden geankert, alle Zellen
    provider, kein LLM involviert."""
    fy, quarters = _full_coverage("net_income", CLOSED_YEAR)
    provider = _Provider(fy=fy, quarters=quarters)

    wrote, updated = _run_anchor(db, monkeypatch, company, provider)

    assert wrote is True
    rows = _rows(db, company, "net_income", CLOSED_YEAR)
    assert {r.period_type for r in rows} == {"FY", "Q1", "Q2", "Q3", "Q4"}
    assert all(r.primary_method == "provider" for r in rows)
    assert len(updated) == 5


def test_anchor_us_key_periods_non_edgar_key_noop(db, company, monkeypatch):
    """Key ohne EDGAR-Konzept (net_debt): No-op ohne Provider-Call — die
    Zelle bleibt leer bzw. wird per Ableitung gefuellt."""
    provider = _Provider()

    wrote, updated = _run_anchor(db, monkeypatch, company, provider, key="net_debt")

    assert wrote is False
    assert updated == []
    assert provider.fy_calls == []
    assert provider.q_calls == []


def test_anchor_us_key_periods_error_returns_false(db, company, monkeypatch):
    """Anker-Fehler crasht den Refresh nicht — Key gilt als nicht
    geschrieben."""
    import app.values.provider_anchor as anchor_mod

    def boom(db_, comp_, key_, year_):
        raise RuntimeError("edgar down")

    monkeypatch.setattr(anchor_mod, "anchor_key_periods_with_provider", boom)
    from app.values.routes import _anchor_us_key_periods

    wrote = _anchor_us_key_periods(
        db=db, key="net_income", company=company,
        company_id=company.id, updated=[], year=CLOSED_YEAR,
    )
    assert wrote is False
