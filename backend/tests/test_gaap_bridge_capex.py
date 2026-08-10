"""capex in der GAAP-Bruecke (gaap_bridge): Extraktion aus der Investing-
Zeile des Condensed Cash Flow Statements, Vorzeichen-Normalisierung auf
die DB-Konvention (positiver Betrag, explizites abs — capex ist NICHT in
ALWAYS_POSITIVE_KEYS) und die bestehenden Gates (period_end, YTD).

Hermetisch: Fetch-Helfer und Claude-Client gepatcht wie in
tests/test_gaap_bridge.py.
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
    BRIDGE_KEYS,
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

Q2_FILING_DATE = date(YEAR, 7, 25).isoformat()
Q3_FILING_DATE = date(YEAR, 10, 25).isoformat()

INDEX_JSON = {"directory": {"item": [
    {"name": "tst-8k.htm"},
    {"name": "ex991.htm"},
]}}

EXHIBIT_HTML = """<html><body>
<table>
<tr><td>Condensed Consolidated Statements of Cash Flows</td></tr>
<tr><td>Purchases of property, equipment and technology</td><td>(2,000)</td></tr>
</table>
</body></html>"""


def _submissions(entries):
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
    user = User(email="capex@example.com", password_hash=hash_password("pw1234"))
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


def _patch_q2(monkeypatch, payload):
    return _patch_all(
        monkeypatch,
        {SUB_URL: _submissions([("8-K", Q2_FILING_DATE, ACCN_Q2, "2.02,9.01")]),
         INDEX_URL_Q2: INDEX_JSON},
        {EXHIBIT_URL_Q2: EXHIBIT_HTML},
        payload,
    )


def _patch_q3(monkeypatch, payload):
    return _patch_all(
        monkeypatch,
        {SUB_URL: _submissions([("8-K", Q3_FILING_DATE, ACCN_Q3, "2.02,9.01")]),
         INDEX_URL_Q3: INDEX_JSON},
        {EXHIBIT_URL_Q3: EXHIBIT_HTML},
        payload,
    )


def _values(**kw):
    base = {k: None for k in BRIDGE_KEYS}
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


def test_capex_in_bridge_keys_and_prompt(monkeypatch, db, company):
    """capex ist Bruecken-Key und steht in der Key-Anforderung des Prompts."""
    assert "capex" in BRIDGE_KEYS
    fake = _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "values": _values(capex=-2000000000),
        "ytd_values": _values(),
    })
    bridge_gaap_from_earnings_releases(db, company, [YEAR])
    content = fake.messages.calls[0]["messages"][0]["content"]
    assert "capex" in content
    assert "capex" in fake.messages.calls[0]["system"]


def test_capex_negative_statement_value_stored_positive(db, company, monkeypatch):
    """Cash-Flow-Statement zeigt capex negativ — die Bruecke speichert den
    Betrag positiv (explizites abs, capex nicht in ALWAYS_POSITIVE_KEYS)."""
    _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "values": _values(capex=-2000000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 1
    (row,) = _cell(db, company, "capex", "Q2")
    assert row.numeric_value == Decimal("2000000000")
    assert row.primary_method == "provider"
    assert row.is_forecast is False
    assert row.source_link == EXHIBIT_URL_Q2


def test_capex_period_end_gate_applies(db, company, monkeypatch):
    """period_end-Gate (±Toleranz) gilt auch fuer capex: falsches
    Tabellenkopf-Datum -> kompletter Reject."""
    _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-12-31",
        "values": _values(capex=-2000000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 0
    assert _cell(db, company, "capex", "Q2") == []


def test_capex_ytd_derivation_with_sign_normalisation(db, company, monkeypatch):
    """YTD-only-Cash-Flow-Statement: Standalone-Q3 = |YTD| minus positive
    DB-Vorquartale — das YTD-Vorzeichen wird vor der Ableitung
    normalisiert."""
    _seed_row(db, company, "capex", "Q1", Decimal("100"))
    _seed_row(db, company, "capex", "Q2", Decimal("110"))
    _patch_q3(monkeypatch, {
        "period_end_date": f"{YEAR}-09-30",
        "values": _values(),
        "ytd_values": _values(capex=-330),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 1
    (row,) = _cell(db, company, "capex", "Q3")
    assert row.numeric_value == Decimal("120")
    assert "YTD 330" in row.source_name


def test_capex_ytd_consistency_gate_applies(db, company, monkeypatch):
    """YTD-Konsistenz-Gate gilt auch fuer capex: Vorquartale + Q-Wert
    muessen die YTD-Angabe treffen, sonst wird der Key verworfen."""
    _seed_row(db, company, "capex", "Q1", Decimal("100"))
    _seed_row(db, company, "capex", "Q2", Decimal("110"))
    _patch_q3(monkeypatch, {
        "period_end_date": f"{YEAR}-09-30",
        # 100 + 110 + 120 = 330 != 400 -> capex verworfen.
        "values": _values(capex=-120),
        "ytd_values": _values(capex=-400),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 0
    assert _cell(db, company, "capex", "Q3") == []
