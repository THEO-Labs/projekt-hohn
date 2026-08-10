"""Tests fuer den Provider-First-Flip: EDGAR-Anker VOR der Two-Stage-
Recherche (anchor_key_periods_with_provider + Skip in
_process_one_key_via_two_stage) und der Provider-Actual-Guard in
apply_to_db. Hermetisch — Provider via monkeypatch wie in
test_quarter_anchor.py, LLM-Pipeline via monkeypatch auf
scripts.two_stage_research.
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.providers.base import ProviderResult
from app.values.models import CompanyValue

CLOSED_YEAR = date.today().year - 1
RUNNING_YEAR = date.today().year


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


def _run_two_stage(db, monkeypatch, company, provider, key="net_income", year=CLOSED_YEAR):
    """_process_one_key_via_two_stage mit gemockter LLM-Pipeline aufrufen.
    Rueckgabe: (wrote, research_calls, updated)."""
    import app.values.provider_anchor as anchor_mod
    import scripts.two_stage_research as ts
    from app.values.routes import _process_one_key_via_two_stage

    monkeypatch.setattr(anchor_mod, "get_providers", lambda k: [provider])

    research_calls: list[dict] = []

    def fake_research(**kwargs):
        research_calls.append(kwargs)
        return object()

    monkeypatch.setattr(ts, "research_two_stage", fake_research)
    monkeypatch.setattr(ts, "apply_to_db", lambda *a, **kw: [])

    payload = SimpleNamespace(period_type="FY", period_year=year)
    updated: list = []
    wrote = _process_one_key_via_two_stage(
        db=db, key=key, company=company,
        company_id=company.id, payload=payload, updated=updated,
    )
    db.commit()
    return wrote, research_calls, updated


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


def test_full_provider_coverage_skips_two_stage(db, company, monkeypatch):
    """Abgeschlossenes Jahr, Provider liefert FY+Q1-Q4: keine LLM-Recherche,
    Key zaehlt als erfolgreich (wrote=True), alle 5 Zellen sind provider."""
    fy, quarters = _full_coverage("net_income", CLOSED_YEAR)
    provider = _Provider(fy=fy, quarters=quarters)

    wrote, research_calls, updated = _run_two_stage(db, monkeypatch, company, provider)

    assert research_calls == []
    assert wrote is True
    rows = _rows(db, company, "net_income", CLOSED_YEAR)
    assert {r.period_type for r in rows} == {"FY", "Q1", "Q2", "Q3", "Q4"}
    assert all(r.primary_method == "provider" for r in rows)
    assert all(r.numeric_value is not None for r in rows)
    assert all(r.is_forecast is False for r in rows)
    assert all(r.last_refresh_attempt is not None for r in rows)
    assert len(updated) == 5


def test_partial_coverage_runs_two_stage(db, company, monkeypatch):
    """Abgeschlossenes Jahr, Provider liefert nur Q1-Q3 (kein FY):
    Two-Stage laeuft, die gefundenen Quartale sind trotzdem verankert."""
    quarters = {
        ("net_income", CLOSED_YEAR, q): _res("100") for q in ("Q1", "Q2", "Q3")
    }
    provider = _Provider(quarters=quarters)

    _, research_calls, _ = _run_two_stage(db, monkeypatch, company, provider)

    assert len(research_calls) == 1
    assert research_calls[0]["value_key"] == "net_income"
    assert research_calls[0]["year"] == CLOSED_YEAR
    rows = _rows(db, company, "net_income", CLOSED_YEAR)
    assert {r.period_type for r in rows if r.primary_method == "provider"} == {
        "Q1", "Q2", "Q3",
    }


def test_running_year_always_runs_two_stage(db, company, monkeypatch):
    """Laufendes Jahr: Two-Stage laeuft immer — auch wenn Q1/Q2 schon vom
    Provider abgedeckt sind (Schaetzperioden braucht die Recherche)."""
    quarters = {
        ("net_income", RUNNING_YEAR, q): _res("100") for q in ("Q1", "Q2")
    }
    provider = _Provider(quarters=quarters)

    _, research_calls, _ = _run_two_stage(
        db, monkeypatch, company, provider, year=RUNNING_YEAR,
    )

    assert len(research_calls) == 1
    # FY-Anker fuer das laufende Jahr: kein FY-Fetch (Jahr nicht beendet).
    assert ("net_income", RUNNING_YEAR) not in provider.fy_calls


def test_non_us_company_no_pre_anchor(db, company, monkeypatch):
    """Non-US-Firma: Pre-Anchor wird nicht aufgerufen, Two-Stage laeuft
    unveraendert."""
    company.isin = "DE0001234567"
    db.commit()
    import app.values.provider_anchor as anchor_mod

    anchor_calls: list[tuple] = []

    def fake_anchor(db_, comp_, key_, year_):
        anchor_calls.append((key_, year_))
        return set()

    monkeypatch.setattr(anchor_mod, "anchor_key_periods_with_provider", fake_anchor)

    provider = _Provider()
    _, research_calls, _ = _run_two_stage(db, monkeypatch, company, provider)

    assert anchor_calls == []
    assert len(research_calls) == 1


def test_pre_anchor_error_falls_back_to_llm(db, company, monkeypatch):
    """Fehler im Pre-Anchor duerfen die Recherche nicht verhindern."""
    import app.values.provider_anchor as anchor_mod

    def boom(db_, comp_, key_, year_):
        raise RuntimeError("edgar down")

    monkeypatch.setattr(anchor_mod, "anchor_key_periods_with_provider", boom)

    provider = _Provider()
    _, research_calls, _ = _run_two_stage(db, monkeypatch, company, provider)

    assert len(research_calls) == 1


def _two_stage_result(value_key, fy_value, is_estimate=False):
    from scripts.two_stage_research import (
        ExtractResult,
        QuarterValue,
        TwoStageResult,
        VerifierVerdict,
    )
    extract = ExtractResult(
        ticker="TST", value_key=value_key, year=CLOSED_YEAR, currency="USD",
        q1=None, q2=None, q3=None, q4=None,
        fy=QuarterValue(
            value=fy_value, source_quote="FY figure from report",
            source_url=None, is_estimate=is_estimate,
        ),
        quarter_only=None, is_adjusted_note=None,
    )
    verdict = VerifierVerdict(
        verdict="confirm", corrections={}, reason="reconciles",
        confidence=0.9, flags=[],
    )
    return TwoStageResult(extract=extract, verdict=verdict)


def test_apply_to_db_skips_provider_actual(db, company):
    """Bestehende provider-Actual-Zeile bleibt bei Two-Stage-Write
    unangetastet — nur last_refresh_attempt wird gestempelt."""
    from scripts.two_stage_research import apply_to_db

    row = CompanyValue(
        company_id=company.id, value_key="net_income", period_type="FY",
        period_year=CLOSED_YEAR, numeric_value=Decimal("500"),
        primary_method="provider", source_name="SEC EDGAR 10-K",
        currency="USD", is_forecast=False,
    )
    db.add(row)
    db.commit()

    apply_to_db(
        db, company.id, "net_income", CLOSED_YEAR,
        _two_stage_result("net_income", Decimal("999")), currency="USD",
    )
    db.commit()

    db.refresh(row)
    assert row.numeric_value == Decimal("500")
    assert row.primary_method == "provider"
    assert row.source_name == "SEC EDGAR 10-K"
    assert row.last_refresh_attempt is not None


def test_apply_to_db_still_overwrites_forecast(db, company):
    """Forecast-Slots bleiben beschreibbar: Schaetzungen fuers laufende
    Jahr muessen weiter aktualisiert werden koennen."""
    from scripts.two_stage_research import apply_to_db

    row = CompanyValue(
        company_id=company.id, value_key="net_income", period_type="FY",
        period_year=CLOSED_YEAR, numeric_value=Decimal("500"),
        primary_method="provider", source_name="old forecast",
        currency="USD", is_forecast=True,
    )
    db.add(row)
    db.commit()

    apply_to_db(
        db, company.id, "net_income", CLOSED_YEAR,
        _two_stage_result("net_income", Decimal("999"), is_estimate=True),
        currency="USD",
    )
    db.commit()

    db.refresh(row)
    assert row.numeric_value == Decimal("999")
    assert row.primary_method.startswith("two_stage")


# --- _anchor_us_key_periods (US-Refresh ohne Two-Stage) ---------------------


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
    """US-Anker-Pfad (Ersatz fuer Two-Stage): FY+Q1-Q4 aus EDGAR werden
    geankert, alle Zellen provider, kein LLM involviert."""
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
