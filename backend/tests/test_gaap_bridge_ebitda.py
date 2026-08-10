"""GAAP-Bruecke, EBITDA-Baustein-Ableitung: ebitda steht nicht im Release,
Operating income (GuV) und D&A (Cashflow) schon — die Bruecke verrechnet
beide intern (keine eigenen value_keys) zu ebitda = operating_income + D&A.
Nur wenn BEIDE Bausteine aus derselben Spaltenperiode extrahiert wurden;
YTD-Logik wie bei Statement-Keys. Hermetisch (EDGAR/Claude gemockt)."""
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
<tr><td>Operating income</td><td>$4,000</td></tr>
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
    user = User(email="ebitdabridge@example.com", password_hash=hash_password("pw1234"))
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


def _patch_q3(monkeypatch, payload):
    return _patch_all(
        monkeypatch,
        {SUB_URL: _submissions([("8-K", Q3_FILING_DATE, ACCN_Q3, "2.02,9.01")]),
         INDEX_URL_Q3: INDEX_JSON},
        {EXHIBIT_URL_Q3: EXHIBIT_HTML},
        payload,
    )


def _values(**kw):
    base = {k: None for k in (
        "revenue", "net_income", "eps_diluted", "operating_cash_flow",
        "sbc", "dividends", "buyback_volume",
        "operating_income", "depreciation_amortization",
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


def test_both_components_write_ebitda(db, company, monkeypatch):
    """Happy Path: beide Bausteine standalone extrahiert -> ebitda-Zelle
    als Bruecken-Wert (provider, Bridge-Source, Baustein-Note)."""
    fake = _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "values": _values(operating_income=4000000000,
                          depreciation_amortization=250000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 1
    assert len(fake.messages.calls) == 1
    content = fake.messages.calls[0]["messages"][0]["content"]
    assert "operating_income" in content
    assert "depreciation_amortization" in content
    (row,) = _cell(db, company, "ebitda", "Q2")
    assert row.numeric_value == Decimal("4250000000")
    assert row.primary_method == "provider"
    assert row.is_forecast is False
    assert row.source_name.startswith(BRIDGE_SOURCE_NAME)
    assert "EBITDA = Operating income + D&A" in row.source_name
    assert row.source_link == EXHIBIT_URL_Q2
    assert row.currency == "USD"


def test_single_component_no_write(db, company, monkeypatch):
    """Nur ein Baustein extrahiert: kein ebitda-Write."""
    _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "values": _values(operating_income=4000000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 0
    assert _cell(db, company, "ebitda", "Q2") == []


def test_mixed_columns_no_write(db, company, monkeypatch):
    """Spaltenkonsistenz: Operating income standalone, D&A nur YTD —
    verschiedene Spaltenperioden, kein Write."""
    _seed_row(db, company, "ebitda", "Q1", Decimal("100"))
    _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "values": _values(operating_income=4000000000),
        "ytd_values": _values(depreciation_amortization=500000000),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 0
    assert _cell(db, company, "ebitda", "Q2") == []


def test_ytd_pair_derives_standalone_quarter(db, company, monkeypatch):
    """Beide Bausteine YTD: ebitda-YTD = Summe, Standalone-Q3 = YTD minus
    berichtete ebitda-Vorquartale aus der DB."""
    _seed_row(db, company, "ebitda", "Q1", Decimal("100"))
    _seed_row(db, company, "ebitda", "Q2", Decimal("110"))
    _patch_q3(monkeypatch, {
        "period_end_date": f"{YEAR}-09-30",
        "values": _values(),
        "ytd_values": _values(operating_income=200,
                              depreciation_amortization=130),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 1
    (row,) = _cell(db, company, "ebitda", "Q3")
    assert row.numeric_value == Decimal("120")
    assert row.primary_method == "provider"
    assert "YTD 330" in row.source_name
    assert "EBITDA = Operating income + D&A" in row.source_name


def test_ytd_cross_check_rejects_inconsistent_columns(db, company, monkeypatch):
    """Standalone- UND YTD-Paar vorhanden, aber Vorquartale + Q-Wert treffen
    die YTD-Summe nicht: falsche Spalte, ebitda wird verworfen."""
    _seed_row(db, company, "ebitda", "Q1", Decimal("100"))
    _seed_row(db, company, "ebitda", "Q2", Decimal("110"))
    _patch_q3(monkeypatch, {
        "period_end_date": f"{YEAR}-09-30",
        # standalone 100 + 20 = 120; 210 + 120 = 330 != 400 -> verworfen.
        "values": _values(operating_income=100, depreciation_amortization=20),
        "ytd_values": _values(operating_income=300,
                              depreciation_amortization=100),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 0
    assert _cell(db, company, "ebitda", "Q3") == []


def test_calculated_ebitda_actual_not_overwritten(db, company, monkeypatch):
    """calculated-Restwerte (derive_ebitda_q4_from_fy) sind wie andere
    Nicht-two_stage-Actuals kein Bruecken-Kandidat — bleiben stehen."""
    calc = _seed_row(db, company, "ebitda", "Q2", Decimal("4100000000"),
                     primary_method="calculated",
                     source_name="Berechnet: FY minus Q1-Q3")
    _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "values": _values(revenue=9200000000, operating_income=4000000000,
                          depreciation_amortization=250000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    # revenue wird geschrieben, ebitda nicht.
    assert written == 1
    db.refresh(calc)
    assert calc.numeric_value == Decimal("4100000000")
    assert calc.primary_method == "calculated"
    (rev_row,) = _cell(db, company, "revenue", "Q2")
    assert rev_row.numeric_value == Decimal("9200000000")


def test_two_stage_ebitda_actual_replaced(db, company, monkeypatch):
    """Freitext-LLM-Actuals (two_stage_*) ersetzen wie bei den direkten
    Bruecken-Keys: der tabellenbasierte Baustein-Wert schlaegt sie."""
    llm = _seed_row(db, company, "ebitda", "Q2", Decimal("4000000000"),
                    primary_method="two_stage_confirmed")
    _patch_q2(monkeypatch, {
        "period_end_date": f"{YEAR}-06-30",
        "values": _values(operating_income=4000000000,
                          depreciation_amortization=250000000),
        "ytd_values": _values(),
    })

    written = bridge_gaap_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert written == 1
    db.refresh(llm)
    assert llm.numeric_value == Decimal("4250000000")
    assert llm.primary_method == "provider"
    assert llm.source_name.startswith(BRIDGE_SOURCE_NAME)
