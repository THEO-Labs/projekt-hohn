"""Tests fuer den Nicht-US-Quartals-Anker aus den Yahoo-Quartalsstatements
(anchor_quarters_with_yahoo) und das Quartals-Mapping in
YahooFinanceProvider.fetch_quarterly.

Hermetisch: der Yahoo-Provider ist gemockt (DataFrames bzw. Fake-Provider
via get_providers), kein Netz, kein Claude.
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
    anchor_quarters_with_yahoo,
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
    # Nicht-US (DE-ISIN): der Yahoo-Anker gated auf Nicht-US-Filer.
    comp = Company(
        portfolio_id=portfolio.id, name="Deutsche TestCo", ticker="DTC",
        currency="EUR", isin="DE0001234567",
        fiscal_year_end_month=12, fiscal_year_end_day=31,
    )
    db.add(comp)
    db.commit()
    return comp


class _MarketQProvider:
    """Fake mit der fetch_quarterly-Signatur und dem provider_kind-Marker
    des Yahoo-Providers. results mappt (key, year, quarter) ->
    ProviderResult; fehlende Zellen liefern None."""
    name = "Bloomberg"
    provider_kind = "market"

    def __init__(self, results=None):
        self.results = results or {}
        self.calls: list[tuple] = []

    def fetch_quarterly(self, ticker, key, period_year, quarter,
                        fy_end_month=None, fy_end_day=None):
        self.calls.append((key, period_year, quarter))
        return self.results.get((key, period_year, quarter))


def _result(value, currency="EUR"):
    return ProviderResult(
        value=Decimal(str(value)), source_name="Bloomberg",
        source_link=None, currency=currency,
    )


def _seed(db, comp, key, pt, value, year=YEAR, **kw):
    row = CompanyValue(
        company_id=comp.id, value_key=key, period_type=pt, period_year=year,
        numeric_value=Decimal(str(value)) if value is not None else None,
        currency=kw.pop("currency", "EUR"), **kw,
    )
    db.add(row)
    db.commit()
    return row


def _rows(db, comp, key, pt, year=YEAR):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == pt,
            CompanyValue.period_year == year,
        )
        .all()
    )


def _run(db, comp, results, years=None):
    provider = _MarketQProvider(results)
    with patch("app.values.provider_anchor.get_providers", return_value=[provider]):
        written = anchor_quarters_with_yahoo(db, comp, years or [YEAR])
    db.commit()
    return written, provider


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


# --- Anker: Schreibrechte, Gates, Gating ------------------------------------


def test_statement_research_row_replaced_by_provider(db, company):
    """Provider ist die hoehere Quelle: statement_research-Zeile wird
    ueberschrieben (primary_method 'provider', Quelle wie FY-Anker)."""
    _seed(db, company, "revenue", "Q1", Decimal("9e9"),
          primary_method="statement_research",
          source_name="Zitat | https://ir.example.com")
    written, _ = _run(db, company, {("revenue", YEAR, "Q1"): _result("1e10")})

    assert written == 1
    (row,) = _rows(db, company, "revenue", "Q1")
    assert row.numeric_value == Decimal("10000000000")
    assert row.primary_method == "provider"
    assert row.source_name == "Bloomberg"
    assert row.is_forecast is False


def test_manual_actual_row_untouched(db, company):
    row = _seed(db, company, "revenue", "Q1", Decimal("9e9"),
                manually_overridden=True, primary_method="manual")
    written, _ = _run(db, company, {("revenue", YEAR, "Q1"): _result("1e10")})

    assert written == 0
    db.refresh(row)
    assert row.numeric_value == Decimal("9000000000")
    assert row.manually_overridden is True


def test_unreported_quarter_not_fetched_and_not_written(db, company, monkeypatch):
    """Karenz-Gate: unberichtete Quartale werden gar nicht erst gefetcht."""
    import app.values.provider_anchor as anchor_mod

    monkeypatch.setattr(
        anchor_mod, "_quarter_is_reported",
        lambda comp, year, q, today=None: q == "Q1",
    )
    results = {
        ("revenue", YEAR, "Q1"): _result("9e9"),
        ("revenue", YEAR, "Q2"): _result("1e10"),
    }
    written, provider = _run(db, company, results)

    assert written == 1
    fetched_quarters = {q for _, _, q in provider.calls}
    assert fetched_quarters == {"Q1"}
    assert _rows(db, company, "revenue", "Q2") == []


def test_future_year_nothing_reported_no_calls(db, company):
    written, provider = _run(
        db, company,
        {("revenue", YEAR + 2, "Q1"): _result("9e9")},
        years=[YEAR + 2],
    )
    assert written == 0
    assert provider.calls == []


def test_plausibility_gate_discards_outlier_keeps_good_quarter(db, company):
    """Faktor-Band 0.1-4 gegen FY/4: Ausreisser (9x) verworfen, plausibles
    Quartal geschrieben."""
    _seed(db, company, "revenue", "FY", Decimal("4e10"), primary_method="provider")
    results = {
        ("revenue", YEAR, "Q1"): _result("1e10"),
        ("revenue", YEAR, "Q2"): _result("9e10"),  # 9x FY/4 -> raus
    }
    written, _ = _run(db, company, results)

    assert written == 1
    (q1,) = _rows(db, company, "revenue", "Q1")
    assert q1.numeric_value == Decimal("10000000000")
    assert _rows(db, company, "revenue", "Q2") == []


def test_plausibility_gate_balance_key_compares_against_fy_level(db, company):
    """Bilanz-Stichtag: Vergleich gegen FY (nicht FY/4) — ein Quartalswert
    auf FY-Niveau ist plausibel und darf nicht verworfen werden."""
    _seed(db, company, "cash_and_equivalents", "FY", Decimal("1.2e10"),
          primary_method="provider")
    written, _ = _run(
        db, company,
        {("cash_and_equivalents", YEAR, "Q2"): _result("1.1e10")},
    )
    assert written == 1
    (row,) = _rows(db, company, "cash_and_equivalents", "Q2")
    assert row.numeric_value == Decimal("11000000000")


def test_qsum_gate_discards_all_quarters_on_mismatch(db, company):
    """Alle 4 Quartale einzeln im Band, Summe aber >5% neben dem FY-Anker
    (z.B. Halbjahreswerte in Q-Spalten) -> alle 4 verworfen."""
    _seed(db, company, "revenue", "FY", Decimal("4e10"), primary_method="provider")
    results = {
        ("revenue", YEAR, q): _result(v)
        for q, v in (("Q1", "1e10"), ("Q2", "1e10"), ("Q3", "1e10"), ("Q4", "3e10"))
    }
    written, _ = _run(db, company, results)

    assert written == 0
    for q in ("Q1", "Q2", "Q3", "Q4"):
        assert _rows(db, company, "revenue", q) == []


def test_qsum_gate_passes_consistent_quarters(db, company):
    _seed(db, company, "revenue", "FY", Decimal("4e10"), primary_method="provider")
    results = {
        ("revenue", YEAR, q): _result("1e10")
        for q in ("Q1", "Q2", "Q3", "Q4")
    }
    written, _ = _run(db, company, results)
    assert written == 4


def test_missing_fy_reference_skips_gate_and_writes(db, company):
    """Ohne FY-Referenz kein Urteil — der Wert wird geschrieben (die
    Provider-Sanity-Checks in yahoo.py bleiben die letzte Verteidigung)."""
    written, _ = _run(db, company, {("revenue", YEAR, "Q3"): _result("9e9")})
    assert written == 1
    (row,) = _rows(db, company, "revenue", "Q3")
    assert row.primary_method == "provider"


def test_sign_normalization_sbc(db, company):
    """normalize_sign im Schreibpfad: sbc (ALWAYS_POSITIVE) wird als
    Betrag gespeichert; capex bleibt konventionsgemaess roh."""
    written, _ = _run(db, company, {("sbc", YEAR, "Q1"): _result("-5e7")})
    assert written == 1
    (row,) = _rows(db, company, "sbc", "Q1")
    assert row.numeric_value == Decimal("50000000")


def test_us_company_skips_without_provider_call(db, company):
    """US-Filer laufen ueber EDGAR (anchor_quarters_with_provider) — der
    Yahoo-Anker fasst sie nicht an."""
    company.isin = "US0001234567"
    db.commit()
    written, provider = _run(db, company, {("revenue", YEAR, "Q1"): _result("9e9")})
    assert written == 0
    assert provider.calls == []


def test_currency_conflict_blocks_write(db, company):
    row = _seed(db, company, "revenue", "Q1", Decimal("9e9"), currency="CHF",
                primary_method="statement_research")
    written, _ = _run(db, company, {("revenue", YEAR, "Q1"): _result("1e10")})
    assert written == 0
    db.refresh(row)
    assert row.numeric_value == Decimal("9000000000")
    assert row.currency == "CHF"


# --- Wiring: Refresh-Flow ----------------------------------------------------


def test_refresh_invokes_yahoo_anchor_before_statement_research(client, db, monkeypatch):
    """Nicht-US-Refresh: Yahoo-Quartals-Anker laeuft mit den Konsistenz-
    Jahren VOR den Statement-Calls; ein Anker-Fehler bricht den Refresh
    nicht ab."""
    import app.values.provider_anchor as anchor_mod
    import app.values.routes as routes
    import app.values.statement_research as sr
    from tests.test_values import _seed_catalog

    _seed_catalog(db)
    user = User(email="yqwire@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": "yqwire@example.com", "password": "pw1234"})
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]
    c = client.post(
        f"/api/portfolios/{pid}/companies",
        json={"name": "Deutsche TestCo", "ticker": "DTC", "currency": "EUR"},
    ).json()
    cid = UUID(c["id"])

    monkeypatch.setattr(routes, "_prev_year_needs_backfill",
                        lambda db_, cid_, k, y: False)
    monkeypatch.setattr(routes, "_run_and_persist_calculations", lambda *a, **kw: [])
    monkeypatch.setattr(routes, "_ensure_previous_year_inputs", lambda *a, **kw: None)

    events: list = []

    def fake_yahoo_anchor(db_, company_, years_):
        events.append(("yahoo_anchor", list(years_)))
        raise RuntimeError("boom")  # darf den Refresh nicht abbrechen

    def fake_stmt(db_, company_, year_, cost_tracker=None, groups=None):
        events.append(("statement_research", year_))
        return 0

    monkeypatch.setattr(anchor_mod, "anchor_quarters_with_yahoo", fake_yahoo_anchor)
    monkeypatch.setattr(sr, "fetch_statement_research", fake_stmt)

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={"keys": ["net_income"], "period_type": "FY", "period_year": YEAR},
    )

    assert r.status_code == 200
    assert events[0] == ("yahoo_anchor", [YEAR])
    assert ("statement_research", YEAR) in events
