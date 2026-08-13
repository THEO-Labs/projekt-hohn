"""Bilanz-Keys in der GAAP-Bruecke (Condensed Consolidated Balance Sheet):
cash_and_equivalents, st_investments, st_debt, lt_debt als Instant-Werte.
Kein YTD-Pfad; eigenes Stichtags-Gate (±7d auf balance_sheet_date);
Q4-Stichtag == FY-Ende -> beide Slots. lt_debt strikt als Bilanzzeile
(Prompt-Warnung gegen 'total carrying value of debt').

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
    _SYSTEM_PROMPT,
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
ACCN_Q4 = "0000789019-90-000004"
INDEX_URL_Q2, EXHIBIT_URL_Q2 = _accn_urls(ACCN_Q2)
INDEX_URL_Q4, EXHIBIT_URL_Q4 = _accn_urls(ACCN_Q4)

Q2_FILING_DATE = date(YEAR, 7, 25).isoformat()
Q4_FILING_DATE = date(YEAR + 1, 1, 25).isoformat()

INDEX_JSON = {"directory": {"item": [
    {"name": "tst-8k.htm"},
    {"name": "ex991.htm"},
]}}

EXHIBIT_HTML = """<html><body>
<table>
<tr><td>Condensed Consolidated Balance Sheets</td></tr>
<tr><td>Cash and cash equivalents</td><td>8,000</td></tr>
<tr><td>Long-term debt</td><td>20,000</td></tr>
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
    user = User(email="balance@example.com", password_hash=hash_password("pw1234"))
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


def _patch_q4(monkeypatch, payload):
    return _patch_all(
        monkeypatch,
        {SUB_URL: _submissions([("8-K", Q4_FILING_DATE, ACCN_Q4, "2.02,9.01")]),
         INDEX_URL_Q4: INDEX_JSON},
        {EXHIBIT_URL_Q4: EXHIBIT_HTML},
        payload,
    )


def _values(**kw):
    base = {k: None for k in BRIDGE_KEYS}
    base.update(kw)
    return base


def _seed_row(db, comp, key, ptype, value, year=YEAR, **kw):
    row = CompanyValue(
        company_id=comp.id, value_key=key, period_type=ptype, period_year=year,
        numeric_value=value, source_name=kw.pop("source_name", "seed"),
        primary_method=kw.pop("primary_method", "provider"),
        currency=kw.pop("currency", "USD"), **kw,
    )
    db.add(row)
    db.commit()
    return row


def _cell(db, comp, key, ptype, year=YEAR):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == ptype,
            CompanyValue.period_year == year,
        )
        .order_by(CompanyValue.is_forecast.asc())
        .all()
    )


def test_balance_keys_happy_path(db, company, monkeypatch):
    """Alle vier Bilanz-Keys werden aus der Bilanzspalte extrahiert und als
    provider-Actuals geschrieben (Instant, kein YTD)."""
    _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "balance_sheet_date": f"{YEAR}-06-30",
        "values": _values(
            cash_and_equivalents=8000000000, st_investments=3000000000,
            st_debt=1500000000, lt_debt=20000000000,
        ),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 4
    for key, expected in (
        ("cash_and_equivalents", "8000000000"),
        ("st_investments", "3000000000"),
        ("st_debt", "1500000000"),
        ("lt_debt", "20000000000"),
    ):
        (row,) = _cell(db, company, key, "Q2")
        assert row.numeric_value == Decimal(expected)
        assert row.primary_method == "provider"
        assert row.is_forecast is False
        assert row.source_link == EXHIBIT_URL_Q2


def test_lt_debt_prompt_forbids_carrying_value(db, company, monkeypatch):
    """Die lt_debt-Prosa-Falle (total carrying value of debt) ist explizit
    im Prompt verboten; alle Bilanz-Keys sind angefordert."""
    assert "lt_debt" in BRIDGE_KEYS
    assert "carrying value of debt" in _SYSTEM_PROMPT
    assert "Long-term debt" in _SYSTEM_PROMPT
    assert "balance_sheet_date" in _SYSTEM_PROMPT
    fake = _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "balance_sheet_date": f"{YEAR}-06-30",
        "values": _values(lt_debt=20000000000),
        "ytd_values": _values(),
    })
    bridge_gaap_from_earnings_releases(db, company, [YEAR])
    assert "lt_debt" in fake.messages.calls[0]["messages"][0]["content"]


def test_balance_date_gate_rejects_wrong_column(db, company, monkeypatch):
    """Bilanzstichtag = Vorjahres-FY-Ende (Vergleichsspalte): Bilanz-Keys
    werden verworfen (±7d-Gate), Statement-Keys mit korrektem
    period_end_date werden weiter geschrieben."""
    _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "balance_sheet_date": f"{YEAR - 1}-12-31",
        "values": _values(lt_debt=20000000000, net_income=5200000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 1
    assert _cell(db, company, "lt_debt", "Q2") == []
    (ni,) = _cell(db, company, "net_income", "Q2")
    assert ni.numeric_value == Decimal("5200000000")


def test_balance_date_missing_rejects_balance_keys(db, company, monkeypatch):
    _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "balance_sheet_date": None,
        "values": _values(cash_and_equivalents=8000000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 0
    assert _cell(db, company, "cash_and_equivalents", "Q2") == []


def test_balance_date_within_tolerance_accepted(db, company, monkeypatch):
    """52/53-Wochen-Stichtage nahe dem Kalenderquartalsende (±7d) passieren
    das Gate."""
    _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "balance_sheet_date": f"{YEAR}-07-02",
        "values": _values(cash_and_equivalents=8000000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 1
    (row,) = _cell(db, company, "cash_and_equivalents", "Q2")
    assert row.numeric_value == Decimal("8000000000")


def test_missing_balance_line_stays_null(db, company, monkeypatch):
    """Nicht ausgewiesene Bilanzzeile (st_investments null): kein Write,
    kein Raten — andere Bilanz-Keys werden normal geschrieben."""
    _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "balance_sheet_date": f"{YEAR}-06-30",
        "values": _values(cash_and_equivalents=8000000000, st_investments=None),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 1
    assert _cell(db, company, "st_investments", "Q2") == []


def test_q4_release_writes_q4_and_fy_slot(db, company, monkeypatch):
    """Q4/FY-Release: der Bilanzstichtag ist zugleich das FY-Ende — der
    Instant-Wert landet in BEIDEN Slots (Verteilung wie beim
    EDGAR-Instant-Pfad: 10-K-Instant fuellt Q4 und FY)."""
    fake = _patch_q4(monkeypatch, {
        "period_end_date": f"{YEAR}-12-31",
        "balance_sheet_date": f"{YEAR}-12-31",
        "values": _values(lt_debt=20000000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(
        db, company, [YEAR], max_llm_calls=1,
    )
    db.commit()

    assert written == 2
    assert len(fake.messages.calls) == 1
    (fy_row,) = _cell(db, company, "lt_debt", "FY")
    (q4_row,) = _cell(db, company, "lt_debt", "Q4")
    assert fy_row.numeric_value == Decimal("20000000000")
    assert q4_row.numeric_value == Decimal("20000000000")
    assert fy_row.primary_method == "provider"
    assert q4_row.primary_method == "provider"


def test_cross_slot_write_respects_locks(db, company, monkeypatch):
    """Der Doppel-Write in den zweiten Slot respektiert die Lock-Guards:
    ein manueller FY-Actual bleibt, der Q4-Slot wird gefuellt (Abweichung
    <1% — innerhalb der Q4/FY-Widerspruchs-Toleranz)."""
    manual_fy = _seed_row(db, company, "lt_debt", "FY", Decimal("20100000000"),
                          primary_method="manual", manually_overridden=True)
    _patch_q4(monkeypatch, {
        "period_end_date": f"{YEAR}-12-31",
        "balance_sheet_date": f"{YEAR}-12-31",
        "values": _values(lt_debt=20000000000),
        "ytd_values": _values(),
    })

    bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    db.refresh(manual_fy)
    assert manual_fy.numeric_value == Decimal("20100000000")
    assert manual_fy.manually_overridden is True
    (q4_row,) = _cell(db, company, "lt_debt", "Q4")
    assert q4_row.numeric_value == Decimal("20000000000")


def test_st_debt_prompt_requires_sum(db, company, monkeypatch):
    """st_debt ist als Summe ALLER kurzfristigen Fremdkapital-Zeilen
    beschrieben (Apple: 'Commercial paper' + 'Term debt, current'), mit
    st_debt_components fuer die Summanden im Antwort-Schema."""
    assert "Commercial paper" in _SYSTEM_PROMPT
    assert "Current maturities of long-term debt" in _SYSTEM_PROMPT
    assert "Short-term borrowings" in _SYSTEM_PROMPT
    assert "st_debt_components" in _SYSTEM_PROMPT


def test_st_debt_sums_cp_and_term_debt(db, company, monkeypatch):
    """Apple-Klasse: 'Commercial paper' und 'Term debt, current' sind
    separate Bilanzzeilen — die Bruecke schreibt die Summe, die Quelle
    nennt die Summanden."""
    _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "balance_sheet_date": f"{YEAR}-06-30",
        "st_debt_components": "Commercial paper 7,979 + Term debt, current 12,350",
        "values": _values(st_debt=20329000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 1
    (row,) = _cell(db, company, "st_debt", "Q2")
    assert row.numeric_value == Decimal("20329000000")
    assert "Commercial paper 7,979" in row.source_name
    assert "Term debt, current 12,350" in row.source_name


def test_st_debt_single_line_still_ok(db, company, monkeypatch):
    """Einzelne kurzfristige Fremdkapital-Zeile (keine Summanden): Wert
    wird unveraendert geschrieben, Quelle ohne Summanden-Note."""
    _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "balance_sheet_date": f"{YEAR}-06-30",
        "st_debt_components": None,
        "values": _values(st_debt=1500000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 1
    (row,) = _cell(db, company, "st_debt", "Q2")
    assert row.numeric_value == Decimal("1500000000")
    assert "st_debt =" not in row.source_name


def test_q4_fy_contradiction_blocks_q4_write(db, company, monkeypatch):
    """Autoritative FY-Zeile (XBRL-Anker, CP+Term-Debt-Summe) vs Bruecken-
    Q4-Wert mit >1% Abweichung bei identischem Stichtag: der Q4-Write wird
    geblockt (Apple st_debt Q4=12.350 vs FY=20.329)."""
    fy = _seed_row(db, company, "st_debt", "FY", Decimal("20329000000"))
    _patch_q4(monkeypatch, {
        "period_end_date": f"{YEAR}-12-31",
        "balance_sheet_date": f"{YEAR}-12-31",
        "values": _values(st_debt=12350000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 0
    assert _cell(db, company, "st_debt", "Q4") == []
    db.refresh(fy)
    assert fy.numeric_value == Decimal("20329000000")


def test_q4_fy_contradiction_blocks_fy_write(db, company, monkeypatch):
    """Gegenrichtung: autoritative Q4-Zeile vorhanden, die Bruecke will den
    FY-Slot mit >1% Abweichung fuellen — geblockt."""
    q4 = _seed_row(db, company, "st_debt", "Q4", Decimal("12350000000"))
    _patch_q4(monkeypatch, {
        "period_end_date": f"{YEAR}-12-31",
        "balance_sheet_date": f"{YEAR}-12-31",
        "values": _values(st_debt=20329000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 0
    assert _cell(db, company, "st_debt", "FY") == []
    db.refresh(q4)
    assert q4.numeric_value == Decimal("12350000000")


def test_q4_fy_gate_ignores_two_stage_counterpart(db, company, monkeypatch):
    """Nicht-autoritative Gegen-Zeile (two_stage-Freitext-LLM) blockt
    nicht: beide Slots werden mit dem Bruecken-Wert ueberschrieben."""
    _seed_row(db, company, "st_debt", "FY", Decimal("99"),
              primary_method="two_stage_web")
    _patch_q4(monkeypatch, {
        "period_end_date": f"{YEAR}-12-31",
        "balance_sheet_date": f"{YEAR}-12-31",
        "values": _values(st_debt=20329000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(
        db, company, [YEAR], max_llm_calls=1,
    )
    db.commit()

    assert written == 2
    (q4_row,) = _cell(db, company, "st_debt", "Q4")
    (fy_row,) = _cell(db, company, "st_debt", "FY")
    assert q4_row.numeric_value == Decimal("20329000000")
    assert fy_row.numeric_value == Decimal("20329000000")
