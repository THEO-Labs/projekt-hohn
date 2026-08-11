"""Tests fuer die Nicht-US-Degradierung des Marktdaten-Feeds (Yahoo):
yahoo_reference_map ist ein reiner Fetch (keine DB-Writes), der FY-Anker
ueberspringt Markt-Provider fuer Nicht-US-Firmen (ESEF bleibt
Wertequelle, US-Pfad unveraendert), und der Refresh schreibt keine
Yahoo-Fundamentals mehr — Stammdaten bleiben auf dem Feed.

Dazu das Quartals-Mapping in YahooFinanceProvider.fetch_quarterly.
Hermetisch: Provider gemockt (DataFrames bzw. Fakes via get_providers),
kein Netz, kein Claude.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pandas as pd
import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.providers.base import ProviderResult
from app.providers.yahoo import YahooFinanceProvider
from app.values.models import CompanyValue
from app.values.provider_anchor import (
    _quarter_is_reported,
    anchor_fy_with_provider,
    yahoo_reference_map,
)

YEAR = date.today().year - 1


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="yq@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.flush()
    portfolio = Portfolio(name="P", owner_user_id=user.id)
    db.add(portfolio)
    db.flush()
    # Nicht-US (DE-ISIN): Markt-Provider ist hier nur noch Referenz.
    comp = Company(
        portfolio_id=portfolio.id, name="Deutsche TestCo", ticker="DTC",
        currency="EUR", isin="DE0001234567",
        fiscal_year_end_month=12, fiscal_year_end_day=31,
    )
    db.add(comp)
    db.commit()
    return comp


class _MarketProvider:
    """Fake mit fetch- und fetch_quarterly-Signatur und dem
    provider_kind-Marker des Yahoo-Providers. fy mappt (key, year),
    q mappt (key, year, quarter) -> ProviderResult; fehlend -> None."""
    name = "Bloomberg"
    provider_kind = "market"

    def __init__(self, fy=None, q=None):
        self.fy = fy or {}
        self.q = q or {}
        self.fetch_calls: list[tuple] = []
        self.q_calls: list[tuple] = []

    def fetch(self, ticker, key, period_type="FY", period_year=None):
        self.fetch_calls.append((key, period_type, period_year))
        return self.fy.get((key, period_year))

    def fetch_quarterly(self, ticker, key, period_year, quarter,
                        fy_end_month=None, fy_end_day=None):
        self.q_calls.append((key, period_year, quarter))
        return self.q.get((key, period_year, quarter))


class _EsefFake:
    """Berichts-Provider-Fake (kein provider_kind='market'): bleibt fuer
    Nicht-US-Firmen Wertequelle des FY-Ankers."""
    name = "ESEF (filings.xbrl.org)"

    def __init__(self, results=None):
        self.results = results or {}

    def fetch(self, ticker, key, period_type="FY", period_year=None,
              fy_end_month=None, fy_end_day=None, isin=None):
        return self.results.get((key, period_year))


def _result(value, currency="EUR", source_name="Bloomberg"):
    return ProviderResult(
        value=Decimal(str(value)), source_name=source_name,
        source_link=None, currency=currency,
    )


def _all_rows(db, comp):
    return (
        db.query(CompanyValue)
        .filter(CompanyValue.company_id == comp.id)
        .all()
    )


# --- Provider: Quartals-Mapping --------------------------------------------


def _q_provider(monkeypatch, kind, df, currency="EUR"):
    p = YahooFinanceProvider()
    monkeypatch.setattr(p, f"_get_quarterly_{kind}", lambda ticker: df)
    monkeypatch.setattr(p, "_get_info", lambda ticker: {"currency": currency})
    return p


def test_fetch_quarterly_calendar_fy_maps_quarter_to_column(monkeypatch):
    """Kalenderjahr-Firma: Q2 -> Spalte 30.06. desselben Jahres."""
    df = pd.DataFrame(
        [[Decimal("9e9"), Decimal("8e9")], [Decimal("1e9"), Decimal("0.9e9")]],
        index=["Total Revenue", "Net Income"],
        columns=[pd.Timestamp(f"{YEAR}-06-30"), pd.Timestamp(f"{YEAR}-03-31")],
    )
    p = _q_provider(monkeypatch, "financials", df)
    r = p.fetch_quarterly("DTC", "revenue", YEAR, "Q2",
                          fy_end_month=12, fy_end_day=31)
    assert r is not None
    assert r.value == Decimal("9000000000")
    assert r.currency == "EUR"
    assert r.source_name == "Bloomberg"
    r1 = p.fetch_quarterly("DTC", "revenue", YEAR, "Q1",
                           fy_end_month=12, fy_end_day=31)
    assert r1.value == Decimal("8000000000")


def test_fetch_quarterly_offset_fy_september_end(monkeypatch):
    """Abweichendes FY (Siemens, Ende 30.09.): Q1 FY N endet am 31.12.
    des Kalenderjahres N-1 — Day-Clamping-Toleranz (berechnet 30.12.)
    matcht die Yahoo-Spalte 31.12."""
    df = pd.DataFrame(
        [[Decimal("20e9"), Decimal("19e9")]],
        index=["Total Revenue"],
        columns=[pd.Timestamp(f"{YEAR - 1}-12-31"), pd.Timestamp(f"{YEAR - 1}-09-30")],
    )
    p = _q_provider(monkeypatch, "financials", df)
    r = p.fetch_quarterly("SIE.DE", "revenue", YEAR, "Q1",
                          fy_end_month=9, fy_end_day=30)
    assert r is not None
    assert r.value == Decimal("20000000000")
    # Q4 FY N-1 = FY-Ende 30.09. des Kalenderjahres N-1.
    r4 = p.fetch_quarterly("SIE.DE", "revenue", YEAR - 1, "Q4",
                           fy_end_month=9, fy_end_day=30)
    assert r4.value == Decimal("19000000000")


def test_fetch_quarterly_no_matching_column_returns_none(monkeypatch):
    """Yahoo liefert nur ~5 Quartale: aeltere Quartale ohne Spalte -> None."""
    df = pd.DataFrame(
        [[Decimal("9e9")]],
        index=["Total Revenue"],
        columns=[pd.Timestamp(f"{YEAR}-06-30")],
    )
    p = _q_provider(monkeypatch, "financials", df)
    assert p.fetch_quarterly("DTC", "revenue", YEAR - 3, "Q2",
                             fy_end_month=12, fy_end_day=31) is None


def test_fetch_quarterly_dividends_abs_and_unsupported_key(monkeypatch):
    """Cash-Outflows (dividends/buyback) kommen von Yahoo negativ und
    werden wie im FY-Pfad als Betrag geliefert; Keys ohne Quartalspfad
    (fcf ist berechnet) -> None."""
    df = pd.DataFrame(
        [[Decimal("-2e9")]],
        index=["Cash Dividends Paid"],
        columns=[pd.Timestamp(f"{YEAR}-03-31")],
    )
    p = _q_provider(monkeypatch, "cashflow", df)
    r = p.fetch_quarterly("DTC", "dividends", YEAR, "Q1",
                          fy_end_month=12, fy_end_day=31)
    assert r.value == Decimal("2000000000")
    assert p.fetch_quarterly("DTC", "fcf", YEAR, "Q1",
                             fy_end_month=12, fy_end_day=31) is None


def test_fetch_quarterly_balance_sheet_row(monkeypatch):
    df = pd.DataFrame(
        [[Decimal("11e9")]],
        index=["Cash And Cash Equivalents"],
        columns=[pd.Timestamp(f"{YEAR}-09-30")],
    )
    p = _q_provider(monkeypatch, "balance_sheet", df)
    r = p.fetch_quarterly("DTC", "cash_and_equivalents", YEAR, "Q3",
                          fy_end_month=12, fy_end_day=31)
    assert r.value == Decimal("11000000000")


# --- Karenz-Kriterium -------------------------------------------------------


def test_quarter_is_reported_respects_grace_and_fy_offset():
    class _C:
        fiscal_year_end_month = 9
        fiscal_year_end_day = 30

    c = _C()
    # Q1 FY2026 endet ~30.12.2025: 45 Tage Karenz.
    assert _quarter_is_reported(c, 2026, "Q1", today=date(2026, 3, 1)) is True
    assert _quarter_is_reported(c, 2026, "Q1", today=date(2026, 1, 15)) is False
    # Zukuenftiges Quartal nie berichtet.
    assert _quarter_is_reported(c, 2026, "Q4", today=date(2026, 3, 1)) is False


# --- yahoo_reference_map: reiner Fetch, keine Writes ------------------------


def test_reference_map_collects_fy_and_quarters_without_writes(db, company):
    provider = _MarketProvider(
        fy={("revenue", YEAR): _result("4e10")},
        q={
            ("revenue", YEAR, "Q1"): _result("1e10"),
            ("net_income", YEAR, "Q2"): _result("8e8"),
        },
    )
    with patch("app.values.provider_anchor.get_providers", return_value=[provider]):
        refs = yahoo_reference_map(company, [YEAR])

    assert refs[("revenue", "FY", YEAR)] == Decimal("40000000000")
    assert refs[("revenue", "Q1", YEAR)] == Decimal("10000000000")
    assert refs[("net_income", "Q2", YEAR)] == Decimal("800000000")
    assert ("revenue", "Q2", YEAR) not in refs
    # Reiner Fetch: es entsteht KEINE company_values-Zeile.
    assert _all_rows(db, company) == []


def test_reference_map_skips_unreported_quarters(db, company, monkeypatch):
    """Karenz-Gate: unberichtete Quartale werden gar nicht erst gefetcht."""
    import app.values.provider_anchor as anchor_mod

    monkeypatch.setattr(
        anchor_mod, "_quarter_is_reported",
        lambda comp, year, q, today=None: q == "Q1",
    )
    provider = _MarketProvider(q={
        ("revenue", YEAR, "Q1"): _result("9e9"),
        ("revenue", YEAR, "Q2"): _result("1e10"),
    })
    with patch("app.values.provider_anchor.get_providers", return_value=[provider]):
        refs = yahoo_reference_map(company, [YEAR])

    assert {q for _, _, q in provider.q_calls} == {"Q1"}
    assert ("revenue", "Q1", YEAR) in refs
    assert ("revenue", "Q2", YEAR) not in refs


def test_reference_map_fetch_errors_logged_and_skipped(db, company):
    class _Flaky(_MarketProvider):
        def fetch(self, ticker, key, period_type="FY", period_year=None):
            raise RuntimeError("rate limited")

    provider = _Flaky(q={("revenue", YEAR, "Q1"): _result("9e9")})
    with patch("app.values.provider_anchor.get_providers", return_value=[provider]):
        refs = yahoo_reference_map(company, [YEAR])

    # FY-Fehler frisst die Quartals-Referenzen nicht.
    assert refs == {("revenue", "Q1", YEAR): Decimal("9000000000")}


def test_reference_map_empty_without_market_provider(db, company):
    """conftest-Netzblock (get_providers -> []): leere Map, kein Fehler."""
    assert yahoo_reference_map(company, [YEAR]) == {}


# --- FY-Anker: Markt-Provider fuer Nicht-US abgeklemmt ----------------------


def _seed_fy(db, comp, key, year, value, **kw):
    row = CompanyValue(
        company_id=comp.id, value_key=key, period_type="FY", period_year=year,
        numeric_value=Decimal(str(value)),
        primary_method=kw.pop("primary_method", "two_stage_verified"),
        currency=kw.pop("currency", "EUR"), **kw,
    )
    db.add(row)
    db.commit()
    return row


def test_fy_anchor_skips_market_provider_for_non_us(db, company):
    """Kundenentscheid: der Markt-Feed schreibt fuer Nicht-US keine
    FY-Fundamentals mehr — kein Fetch, kein Write."""
    row = _seed_fy(db, company, "net_income", YEAR, "1e9")
    provider = _MarketProvider(fy={("net_income", YEAR): _result("2e9")})
    with patch("app.values.provider_anchor.get_providers", return_value=[provider]):
        wrote = anchor_fy_with_provider(db, company, "net_income", YEAR)
    db.commit()

    assert wrote is False
    assert provider.fetch_calls == []
    db.refresh(row)
    assert row.numeric_value == Decimal("1000000000")
    assert row.primary_method == "two_stage_verified"


def test_fy_anchor_esef_still_writes_for_non_us(db, company):
    """ESEF (offizielles XBRL) bleibt Wertequelle des FY-Ankers."""
    _seed_fy(db, company, "net_income", YEAR, "1e9")
    esef = _EsefFake({("net_income", YEAR): _result(
        "2e9", source_name=f"ESEF FY{YEAR}",
    )})
    market = _MarketProvider(fy={("net_income", YEAR): _result("9e9")})
    with patch("app.values.provider_anchor.get_providers",
               return_value=[esef, market]):
        wrote = anchor_fy_with_provider(db, company, "net_income", YEAR)
    db.commit()

    assert wrote is True
    row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company.id,
            CompanyValue.value_key == "net_income",
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == YEAR,
        )
        .one()
    )
    assert row.numeric_value == Decimal("2000000000")
    assert row.primary_method == "provider"
    assert row.source_name == f"ESEF FY{YEAR}"
    assert market.fetch_calls == []


def test_fy_anchor_market_provider_unchanged_for_us(db, company):
    """US-Pfad unveraendert: der Markt-Provider bleibt Fallback in der
    FY-Anker-Kette."""
    company.isin = "US0001234567"
    db.commit()
    _seed_fy(db, company, "net_income", YEAR, "1e9")
    provider = _MarketProvider(fy={("net_income", YEAR): _result("2e9")})
    with patch("app.values.provider_anchor.get_providers", return_value=[provider]):
        wrote = anchor_fy_with_provider(db, company, "net_income", YEAR)
    db.commit()

    assert wrote is True
    assert len(provider.fetch_calls) == 1


# --- Wiring: Refresh-Flow ----------------------------------------------------


def _setup_refresh(client, db, email="yqwire@example.com"):
    from tests.test_values import _seed_catalog

    _seed_catalog(db)
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]
    c = client.post(
        f"/api/portfolios/{pid}/companies",
        json={"name": "Deutsche TestCo", "ticker": "DTC", "currency": "EUR"},
    ).json()
    return UUID(c["id"])


def test_non_us_refresh_writes_no_yahoo_fundamentals(client, db, monkeypatch):
    """Nicht-US-FY-Refresh: obwohl der Markt-Provider Werte liefern
    koennte, entsteht keine Bloomberg-Fundamental-Zeile — und der
    Provider wird fuer Fundamentals gar nicht erst gefragt."""
    import app.values.provider_anchor as anchor_mod
    import app.values.statement_research as sr
    import app.values.routes as routes

    cid = _setup_refresh(client, db)

    monkeypatch.setattr(routes, "_prev_year_needs_backfill",
                        lambda db_, cid_, k, y: False)
    monkeypatch.setattr(routes, "_run_and_persist_calculations", lambda *a, **kw: [])
    monkeypatch.setattr(routes, "_ensure_previous_year_inputs", lambda *a, **kw: None)
    monkeypatch.setattr(sr, "fetch_statement_research",
                        lambda *a, **kw: 0)

    provider = _MarketProvider(fy={("net_income", YEAR): _result("2e9")})
    monkeypatch.setattr(anchor_mod, "get_providers", lambda key: [provider])

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={"keys": ["net_income"], "period_type": "FY", "period_year": YEAR},
    )

    assert r.status_code == 200
    assert provider.fetch_calls == []
    rows = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == cid,
            CompanyValue.value_key == "net_income",
        )
        .all()
    )
    assert all(not (row.source_name or "").startswith("Bloomberg") for row in rows)


def test_stammdaten_refresh_still_uses_market_provider(client, db, monkeypatch):
    """Stammdaten (stock_price/shares/market_cap) bleiben ausdruecklich
    auf dem Markt-Feed — der Daily-Refresh schreibt weiter Bloomberg."""
    import app.values.routes as routes

    cid = _setup_refresh(client, db, email="yqstamm@example.com")

    class _SnapshotProvider:
        name = "Bloomberg"
        provider_kind = "market"

        def fetch(self, ticker, key, period_type="FY", period_year=None):
            if key == "stock_price":
                return ProviderResult(
                    value=Decimal("123.45"), source_name="Bloomberg",
                    source_link=None, currency="EUR",
                )
            return None

    monkeypatch.setattr(routes, "get_providers", lambda key: [_SnapshotProvider()])

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={
            "keys": ["stock_price"], "period_type": "FY",
            "period_year": YEAR, "stammdaten_only": True,
        },
    )

    assert r.status_code == 200
    row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == cid,
            CompanyValue.value_key == "stock_price",
            CompanyValue.period_type == "SNAPSHOT",
        )
        .one()
    )
    assert row.numeric_value == Decimal("123.45")
    assert row.source_name == "Bloomberg"
