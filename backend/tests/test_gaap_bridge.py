"""Tests fuer die GAAP-Bruecke aus 8-K-Earnings-Releases (gaap_bridge).

Hermetisch: EDGAR-Submissions/Exhibit-Antworten sind Fixtures
(adjusted_enrichment._fetch_json/_fetch_text gepatcht — die Bruecke nutzt
dieselben Fetch-Helfer), der Claude-Call ist ein Fake-Client.
"""
import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.values.adjusted_enrichment as adj
from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.values.gaap_bridge import (
    BRIDGE_SOURCE_NAME,
    bridge_gaap_from_earnings_releases,
)
from app.values.models import CompanyValue

YEAR = date.today().year - 1

CIK = "0000789019"
SUB_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"


def _accn_urls(accn: str) -> tuple[str, str]:
    base = f"https://www.sec.gov/Archives/edgar/data/{int(CIK)}/{accn.replace('-', '')}"
    return f"{base}/index.json", f"{base}/ex991.htm"


ACCN_Q2 = "0000789019-90-000002"
ACCN_Q3 = "0000789019-90-000003"
INDEX_URL_Q2, EXHIBIT_URL_Q2 = _accn_urls(ACCN_Q2)
INDEX_URL_Q3, EXHIBIT_URL_Q3 = _accn_urls(ACCN_Q3)

# FY-Ende 31.12.: Q2 endet 30.06., Q3 endet 30.09.; Release ~25 Tage danach.
Q2_FILING_DATE = date(YEAR, 7, 25).isoformat()
Q3_FILING_DATE = date(YEAR, 10, 25).isoformat()

INDEX_JSON = {"directory": {"item": [
    {"name": "tst-8k.htm"},
    {"name": "ex991.htm"},
]}}

EXHIBIT_HTML = """<html><body>
<p>TestCo reports quarterly results</p>
<table>
<tr><td>Condensed Consolidated Statements of Operations</td></tr>
<tr><td>Net revenues</td><td>$9,200</td></tr>
<tr><td>Net income</td><td>$5,200</td></tr>
</table>
</body></html>"""


def _submissions(entries):
    """entries: Liste (form, filing_date, accn, items)."""
    return {"filings": {"recent": {
        "form": [e[0] for e in entries],
        "filingDate": [e[1] for e in entries],
        "accessionNumber": [e[2] for e in entries],
        "primaryDocument": [f"doc{i}.htm" for i in range(len(entries))],
        "items": [e[3] for e in entries],
    }}}


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="bridge@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.flush()
    portfolio = Portfolio(name="P", owner_user_id=user.id)
    db.add(portfolio)
    db.flush()
    comp = Company(
        portfolio_id=portfolio.id, name="TestCo", ticker="TST",
        currency="USD", isin="US0001234567",
        fiscal_year_end_month=12, fiscal_year_end_day=31,
    )
    db.add(comp)
    db.commit()
    return comp


class _FakeMessages:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return SimpleNamespace(
            content=[SimpleNamespace(text=text)],
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )


class _FakeClient:
    def __init__(self, payload):
        self.messages = _FakeMessages(payload)


def _patch_all(monkeypatch, url_json, url_text, claude_payload):
    fake = _FakeClient(claude_payload)
    monkeypatch.setattr(adj, "_resolve_cik", lambda ticker: CIK)
    monkeypatch.setattr(adj, "_fetch_json", lambda url: url_json.get(url))
    monkeypatch.setattr(adj, "_fetch_text", lambda url: url_text.get(url))
    monkeypatch.setattr("app.llm.claude.get_client", lambda: fake)
    return fake


def _patch_q2(monkeypatch, payload, items="2.02,9.01"):
    return _patch_all(
        monkeypatch,
        {SUB_URL: _submissions([("8-K", Q2_FILING_DATE, ACCN_Q2, items)]),
         INDEX_URL_Q2: INDEX_JSON},
        {EXHIBIT_URL_Q2: EXHIBIT_HTML},
        payload,
    )


def _values(**kw):
    base = {k: None for k in (
        "revenue", "net_income", "eps_diluted", "operating_cash_flow",
        "sbc", "dividends", "buyback_volume",
    )}
    base.update(kw)
    return base


def _seed_row(db, comp, key, quarter, value, year=YEAR, **kw):
    row = CompanyValue(
        company_id=comp.id, value_key=key, period_type=quarter, period_year=year,
        numeric_value=value, source_name=kw.pop("source_name", "seed"),
        primary_method=kw.pop("primary_method", "provider"),
        currency=kw.pop("currency", "USD"), **kw,
    )
    db.add(row)
    db.commit()
    return row


def _cell(db, comp, key, quarter, year=YEAR):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == quarter,
            CompanyValue.period_year == year,
        )
        .order_by(CompanyValue.is_forecast.asc())
        .all()
    )


def test_bridges_empty_and_not_found_cells(db, company, monkeypatch):
    """Happy Path: not_found-Zelle, leere Zelle und Estimate-Zeile werden
    aus dem Release gefuellt — provider-Actual mit Bridge-Source, ein
    Claude-Call fuer alle Keys der Periode."""
    nf = _seed_row(db, company, "net_income", "Q2", None,
                   primary_method="not_found", source_name="No source found")
    est = _seed_row(db, company, "eps_diluted", "Q2", Decimal("2.00"),
                    primary_method="web_guidance", is_forecast=True)
    fake = _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "values": _values(revenue=9200000000, net_income=5200000000,
                          eps_diluted=2.5),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 3
    assert len(fake.messages.calls) == 1
    content = fake.messages.calls[0]["messages"][0]["content"]
    assert f"Q2 FY{YEAR}" in content
    assert "revenue" in content
    db.refresh(nf)
    assert nf.numeric_value == Decimal("5200000000")
    assert nf.primary_method == "provider"
    assert nf.is_forecast is False
    assert nf.source_name == BRIDGE_SOURCE_NAME
    assert nf.source_link == EXHIBIT_URL_Q2
    db.refresh(est)
    assert est.numeric_value == Decimal("2.5")
    assert est.is_forecast is False
    assert est.primary_method == "provider"
    (rev_row,) = _cell(db, company, "revenue", "Q2")
    assert rev_row.numeric_value == Decimal("9200000000")
    assert rev_row.currency == "USD"


def test_reported_actual_and_manual_cells_skipped(db, company, monkeypatch):
    """Berichtete Actuals (provider) und Manual-Zeilen sind keine
    Kandidaten — der Bridge-Lauf schreibt sie nicht um."""
    anchored = _seed_row(db, company, "net_income", "Q2", Decimal("5100000000"))
    manual = _seed_row(db, company, "revenue", "Q2", Decimal("999"),
                       primary_method="manual", manually_overridden=True)
    fake = _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "values": _values(revenue=9200000000, net_income=5200000000),
        "ytd_values": _values(),
    })

    bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    db.refresh(anchored)
    db.refresh(manual)
    assert anchored.numeric_value == Decimal("5100000000")
    assert anchored.source_name == "seed"
    assert manual.numeric_value == Decimal("999")
    assert manual.manually_overridden is True


def test_non_us_company_skips(db, company, monkeypatch):
    company.isin = "DE0001234567"
    db.commit()

    def _boom(ticker):
        raise AssertionError("non-US darf EDGAR nicht anfragen")

    monkeypatch.setattr(adj, "_resolve_cik", _boom)
    assert bridge_gaap_from_earnings_releases(db, company, [YEAR]) == 0


def test_period_end_mismatch_rejects(db, company, monkeypatch):
    """Falsche Spalte (Q4-Datum statt Q2): kompletter Reject der Periode."""
    nf = _seed_row(db, company, "net_income", "Q2", None,
                   primary_method="not_found")
    fake = _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-12-31",
        "values": _values(net_income=5200000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 0
    assert len(fake.messages.calls) == 1
    db.refresh(nf)
    assert nf.numeric_value is None


def test_period_end_missing_rejects(db, company, monkeypatch):
    """Ohne DB-GAAP-Referenz ist das Header-Datum der primaere Falsche-
    Spalte-Schutz — fehlt es, wird NICHT geschrieben (strenger als das
    Adjusted-Enrichment)."""
    _patch_q2(monkeypatch, {
        "period_end_date": None,
        "values": _values(net_income=5200000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 0
    assert _cell(db, company, "net_income", "Q2") == []


def test_ytd_consistency_rejects_only_inconsistent_key(db, company, monkeypatch):
    """YTD-Cross-Check als Ersatz fuer den fehlenden DB-Referenzwert:
    prior-Q-Actuals + Q-Wert muessen die YTD-Angabe treffen. Der
    inkonsistente Key wird verworfen, konsistente Keys werden geschrieben."""
    _seed_row(db, company, "operating_cash_flow", "Q1", Decimal("100"))
    _seed_row(db, company, "operating_cash_flow", "Q2", Decimal("110"))
    fake = _patch_all(
        monkeypatch,
        {SUB_URL: _submissions([("8-K", Q3_FILING_DATE, ACCN_Q3, "2.02,9.01")]),
         INDEX_URL_Q3: INDEX_JSON},
        {EXHIBIT_URL_Q3: EXHIBIT_HTML},
        {
            "period_end_date": f"{YEAR}-09-30",
            # 100 + 110 + 120 = 330 != 400 -> ocf verworfen.
            "values": _values(operating_cash_flow=120, net_income=5200000000),
            "ytd_values": _values(operating_cash_flow=400),
        },
    )

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert len(fake.messages.calls) == 1
    assert _cell(db, company, "operating_cash_flow", "Q3") == []
    (ni_row,) = _cell(db, company, "net_income", "Q3")
    assert ni_row.numeric_value == Decimal("5200000000")
    assert written == 1


def test_ytd_derives_standalone_quarter(db, company, monkeypatch):
    """Cash-Flow-Statements der Releases sind meist YTD-only: Standalone-Q
    wird deterministisch als YTD minus berichtete Vorquartale abgeleitet."""
    _seed_row(db, company, "operating_cash_flow", "Q1", Decimal("100"))
    _seed_row(db, company, "operating_cash_flow", "Q2", Decimal("110"))
    _patch_all(
        monkeypatch,
        {SUB_URL: _submissions([("8-K", Q3_FILING_DATE, ACCN_Q3, "2.02,9.01")]),
         INDEX_URL_Q3: INDEX_JSON},
        {EXHIBIT_URL_Q3: EXHIBIT_HTML},
        {
            "period_end_date": f"{YEAR}-09-30",
            "values": _values(),
            "ytd_values": _values(operating_cash_flow=330),
        },
    )

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 1
    (row,) = _cell(db, company, "operating_cash_flow", "Q3")
    assert row.numeric_value == Decimal("120")
    assert row.primary_method == "provider"
    assert row.source_name.startswith(BRIDGE_SOURCE_NAME)
    assert "YTD 330" in row.source_name


def test_outflow_keys_normalized_positive(db, company, monkeypatch):
    """Release zeigt Buybacks/Dividends als negative Cash-Outflows — die
    DB-Konvention ist positiv (normalize_sign/ALWAYS_POSITIVE_KEYS)."""
    _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "values": _values(buyback_volume=-3000000000, dividends=-800000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 2
    (bb,) = _cell(db, company, "buyback_volume", "Q2")
    (dv,) = _cell(db, company, "dividends", "Q2")
    assert bb.numeric_value == Decimal("3000000000")
    assert dv.numeric_value == Decimal("800000000")


def test_within_grace_requires_item_202(db, company, monkeypatch):
    """Innerhalb der Reporting-Frist zaehlt eine Periode nur als berichtet,
    wenn ein Item-2.02-8-K nach Periodenende existiert. Ohne 2.02: kein
    Claude-Call; mit 2.02: Bridge laeuft."""
    monkeypatch.setattr("app.values.detail_page.REPORTING_GRACE_DAYS", 100000)

    fake = _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "values": _values(net_income=5200000000),
        "ytd_values": _values(),
    }, items="5.02")
    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    assert written == 0
    assert len(fake.messages.calls) == 0

    fake = _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "values": _values(net_income=5200000000),
        "ytd_values": _values(),
    }, items="2.02,9.01")
    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()
    assert written == 1
    assert len(fake.messages.calls) == 1


def test_max_llm_calls_cap(db, company, monkeypatch):
    """Der Deckel begrenzt die Claude-Calls pro Lauf."""
    _patch_all(
        monkeypatch,
        {SUB_URL: _submissions([
            ("8-K", Q2_FILING_DATE, ACCN_Q2, "2.02,9.01"),
            ("8-K", Q3_FILING_DATE, ACCN_Q3, "2.02,9.01"),
        ]),
         INDEX_URL_Q2: INDEX_JSON, INDEX_URL_Q3: INDEX_JSON},
        {EXHIBIT_URL_Q2: EXHIBIT_HTML, EXHIBIT_URL_Q3: EXHIBIT_HTML},
        {
            "period_end_date": f"{YEAR}-09-30",
            "values": _values(net_income=5200000000),
            "ytd_values": _values(),
        },
    )

    fake_client = None
    import app.llm.claude as claude_mod
    fake_client = claude_mod.get_client()
    bridge_gaap_from_earnings_releases(db, company, [YEAR], max_llm_calls=1)
    assert len(fake_client.messages.calls) == 1
