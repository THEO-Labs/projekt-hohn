"""Tests fuer die Non-GAAP-Anreicherung aus 8-K-Earnings-Releases.

Hermetisch: EDGAR-Submissions/Exhibit-Antworten sind Fixtures
(_fetch_json/_fetch_text gepatcht), der Claude-Call ist ein Fake-Client
(app.llm.claude.get_client gepatcht — conftest blockt den echten).
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
from app.values.adjusted_enrichment import enrich_adjusted_from_earnings_releases
from app.values.models import CompanyValue

YEAR = date.today().year - 1

CIK = "0000789019"
SUB_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"


def _accn_urls(accn: str) -> tuple[str, str]:
    base = f"https://www.sec.gov/Archives/edgar/data/{int(CIK)}/{accn.replace('-', '')}"
    return f"{base}/index.json", f"{base}/ex991.htm"


ACCN_Q1 = "0000789019-90-000001"
ACCN_Q2 = "0000789019-90-000002"
INDEX_URL_Q1, EXHIBIT_URL_Q1 = _accn_urls(ACCN_Q1)
INDEX_URL_Q2, EXHIBIT_URL_Q2 = _accn_urls(ACCN_Q2)

# Q-Enden bei FY-Ende 31.12.: Q1 = 31.03., Q2 = 30.06. Die 8-K wird ~25
# Tage danach gefiled (im 75-Tage-Fenster).
Q1_FILING_DATE = date(YEAR, 4, 25).isoformat()
Q2_FILING_DATE = date(YEAR, 7, 25).isoformat()

INDEX_JSON = {"directory": {"item": [
    {"name": "tst-8k.htm"},
    {"name": "ex991.htm"},
]}}

EXHIBIT_HTML = """<html><body>
<p>TestCo reports quarterly results</p>
<table>
<tr><td>Reconciliation of GAAP to Non-GAAP</td></tr>
<tr><td>GAAP net income</td><td>$20,000</td></tr>
<tr><td>Non-GAAP net income</td><td>$24,000</td></tr>
<tr><td>Non-GAAP diluted EPS</td><td>$3.23</td></tr>
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
    user = User(email="adjenr@example.com", password_hash=hash_password("pw1234"))
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


def _seed_row(db, comp, key, quarter, value, **kw):
    row = CompanyValue(
        company_id=comp.id, value_key=key, period_type=quarter, period_year=YEAR,
        numeric_value=value, source_name="SEC EDGAR 10-Q",
        primary_method="provider", currency=kw.pop("currency", "USD"), **kw,
    )
    db.add(row)
    db.commit()
    return row


def test_enriches_ni_and_eps_with_one_call(db, company, monkeypatch):
    """Happy Path: ein Claude-Call pro Periode fuellt BEIDE Keys; GAAP-Wert,
    Methode und Locks bleiben unangetastet."""
    ni = _seed_row(db, company, "net_income", "Q2", Decimal("20000000000"))
    eps = _seed_row(db, company, "eps_diluted", "Q2", Decimal("2.70"))
    fake = _patch_all(
        monkeypatch,
        {SUB_URL: _submissions([("8-K", Q2_FILING_DATE, ACCN_Q2, "2.02,9.01")]),
         INDEX_URL_Q2: INDEX_JSON},
        {EXHIBIT_URL_Q2: EXHIBIT_HTML},
        {"non_gaap_net_income": 24000000000, "non_gaap_diluted_eps": 3.23,
         "gaap_net_income": 20000000000, "gaap_diluted_eps": 2.70,
         "source_kind": "table", "adjustment_items": "SBC, restructuring"},
    )

    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert enriched == 1
    assert len(fake.messages.calls) == 1
    call = fake.messages.calls[0]
    assert call["temperature"] == 0
    assert f"Q2 FY{YEAR}" in call["messages"][0]["content"]
    db.refresh(ni)
    db.refresh(eps)
    assert ni.numeric_value == Decimal("20000000000")
    assert ni.numeric_value_adjusted == Decimal("24000000000")
    assert ni.adjustments_note == "Non-GAAP (Reconciliation 8-K): SBC, restructuring"
    assert ni.adjustments_source == EXHIBIT_URL_Q2
    assert ni.primary_method == "provider"
    assert eps.numeric_value_adjusted == Decimal("3.23")
    assert eps.adjustments_source == EXHIBIT_URL_Q2


def test_fill_only_null_keeps_existing_adjusted(db, company, monkeypatch):
    """Bestehender Adjusted-Wert (hier: manuell mit Quelle) wird nie
    ueberschrieben — nur die NULL-Zeile derselben Periode wird gefuellt."""
    ni = _seed_row(db, company, "net_income", "Q2", Decimal("20000000000"),
                   numeric_value_adjusted=Decimal("25000000000"),
                   adjustments_note="manuell", adjustments_source="https://ir.example/pr")
    eps = _seed_row(db, company, "eps_diluted", "Q2", Decimal("2.70"))
    fake = _patch_all(
        monkeypatch,
        {SUB_URL: _submissions([("8-K", Q2_FILING_DATE, ACCN_Q2, "2.02,9.01")]),
         INDEX_URL_Q2: INDEX_JSON},
        {EXHIBIT_URL_Q2: EXHIBIT_HTML},
        {"non_gaap_net_income": 24000000000, "non_gaap_diluted_eps": 3.23,
         "gaap_net_income": 20000000000, "gaap_diluted_eps": 2.70,
         "source_kind": "table", "adjustment_items": "SBC"},
    )

    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert enriched == 1
    assert len(fake.messages.calls) == 1
    db.refresh(ni)
    db.refresh(eps)
    assert ni.numeric_value_adjusted == Decimal("25000000000")
    assert ni.adjustments_note == "manuell"
    assert ni.adjustments_source == "https://ir.example/pr"
    assert eps.numeric_value_adjusted == Decimal("3.23")


def test_overwrites_unprotected_two_stage_adjusted(db, company, monkeypatch):
    """Adjusted-Wert aus der Two-Stage-Recherche (Quelle 'quote | url', per
    adjusted_is_protected unbelegt): der tabellenstrikte 8-K-Wert darf ihn
    ueberschreiben (Visa-Fall: gerundete 6.300 statt Tabelle 6.296)."""
    ni = _seed_row(db, company, "net_income", "Q2", Decimal("20000000000"),
                   numeric_value_adjusted=Decimal("23900000000"),
                   adjustments_note="Non-GAAP net income was $23.9 billion",
                   adjustments_source="Non-GAAP net income was $23.9 billion | https://ir.example/pr")
    fake = _patch_all(
        monkeypatch,
        {SUB_URL: _submissions([("8-K", Q2_FILING_DATE, ACCN_Q2, "2.02,9.01")]),
         INDEX_URL_Q2: INDEX_JSON},
        {EXHIBIT_URL_Q2: EXHIBIT_HTML},
        {"non_gaap_net_income": 24000000000, "non_gaap_diluted_eps": None,
         "gaap_net_income": 20000000000, "gaap_diluted_eps": None,
         "source_kind": "table", "adjustment_items": "SBC"},
    )

    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert enriched == 1
    assert len(fake.messages.calls) == 1
    db.refresh(ni)
    assert ni.numeric_value_adjusted == Decimal("24000000000")
    assert ni.adjustments_note == "Non-GAAP (Reconciliation 8-K): SBC"
    assert ni.adjustments_source == EXHIBIT_URL_Q2


def test_manual_adjusted_never_overwritten(db, company, monkeypatch):
    """adjustments_source='Manual' ist geschuetzt: die Zeile ist kein
    Kandidat — kein EDGAR-Zugriff, kein Claude-Call, Wert bleibt."""
    ni = _seed_row(db, company, "net_income", "Q2", Decimal("20000000000"),
                   numeric_value_adjusted=Decimal("25000000000"),
                   adjustments_note="Manuell ueberschrieben",
                   adjustments_source="Manual")

    def _boom(ticker):
        raise AssertionError("geschuetzte Adjusted-Zeile darf EDGAR nicht anfragen")

    monkeypatch.setattr(adj, "_resolve_cik", _boom)
    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])

    assert enriched == 0
    db.refresh(ni)
    assert ni.numeric_value_adjusted == Decimal("25000000000")
    assert ni.adjustments_source == "Manual"


def test_all_periods_filled_makes_no_llm_call(db, company, monkeypatch):
    """Idempotenz: sind alle Adjusted-Werte belegt, gibt es keine Kandidaten
    und damit weder EDGAR- noch Claude-Calls."""
    _seed_row(db, company, "net_income", "Q2", Decimal("20000000000"),
              numeric_value_adjusted=Decimal("24000000000"),
              adjustments_source="https://sec.gov/ex99.htm")

    def _boom(ticker):
        raise AssertionError("CIK-Aufloesung darf ohne Kandidaten nicht laufen")

    monkeypatch.setattr(adj, "_resolve_cik", _boom)
    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])

    assert enriched == 0


def test_non_us_company_skips(db, company, monkeypatch):
    company.isin = "DE0001234567"
    db.commit()
    _seed_row(db, company, "net_income", "Q2", Decimal("20000000000"))

    def _boom(ticker):
        raise AssertionError("non-US darf EDGAR nicht anfragen")

    monkeypatch.setattr(adj, "_resolve_cik", _boom)
    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])

    assert enriched == 0


def test_cross_check_rejects_wrong_column(db, company, monkeypatch):
    """Vorjahresspalten-Szenario: das mitgelieferte GAAP-Pendant weicht >2%
    vom DB-GAAP-Wert ab (falsche Spalte/Periode gelesen) — Werte werden
    verworfen, der Negativ-Marker verhindert Dauer-Retries."""
    ni = _seed_row(db, company, "net_income", "Q2", Decimal("20000000000"))
    eps = _seed_row(db, company, "eps_diluted", "Q2", Decimal("2.70"))
    fake = _patch_all(
        monkeypatch,
        {SUB_URL: _submissions([("8-K", Q2_FILING_DATE, ACCN_Q2, "2.02,9.01")]),
         INDEX_URL_Q2: INDEX_JSON},
        {EXHIBIT_URL_Q2: EXHIBIT_HTML},
        # gaap_net_income = Vorjahreswert, 10% daneben.
        {"non_gaap_net_income": 21000000000, "non_gaap_diluted_eps": 2.90,
         "gaap_net_income": 18000000000, "gaap_diluted_eps": 2.40,
         "source_kind": "table", "adjustment_items": "SBC"},
    )

    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert enriched == 0
    assert len(fake.messages.calls) == 1
    db.refresh(ni)
    db.refresh(eps)
    assert ni.numeric_value_adjusted is None
    assert ni.adjustments_source is None
    assert ni.adjustments_note == "no non-GAAP reconciliation found"
    assert eps.numeric_value_adjusted is None
    assert eps.adjustments_note == "no non-GAAP reconciliation found"

    # Zweiter Lauf: die markierten Zeilen sind keine Kandidaten mehr —
    # kein weiterer Claude-Call.
    enriched2 = enrich_adjusted_from_earnings_releases(db, company, [YEAR])
    assert enriched2 == 0
    assert len(fake.messages.calls) == 1


def test_loss_gaap_profit_non_gaap_accepted(db, company, monkeypatch):
    """GAAP-Verlust mit Non-GAAP-Gewinn (Impairment-Quartal): das alte
    0.5x-2x-Ratio-Gate haette geblockt — mit bestandenem GAAP-Cross-Check
    wird jetzt geschrieben."""
    ni = _seed_row(db, company, "net_income", "Q2", Decimal("-5000000000"))
    fake = _patch_all(
        monkeypatch,
        {SUB_URL: _submissions([("8-K", Q2_FILING_DATE, ACCN_Q2, "2.02,9.01")]),
         INDEX_URL_Q2: INDEX_JSON},
        {EXHIBIT_URL_Q2: EXHIBIT_HTML},
        {"non_gaap_net_income": 1000000000, "non_gaap_diluted_eps": None,
         "gaap_net_income": -5000000000, "gaap_diluted_eps": None,
         "source_kind": "table", "adjustment_items": "impairment, SBC"},
    )

    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert enriched == 1
    assert len(fake.messages.calls) == 1
    db.refresh(ni)
    assert ni.numeric_value == Decimal("-5000000000")
    assert ni.numeric_value_adjusted == Decimal("1000000000")
    assert ni.adjustments_source == EXHIBIT_URL_Q2


def test_null_answer_persists_negative_marker(db, company, monkeypatch):
    """Bewusstes null (keine Reconciliation im Release): Negativ-Marker auf
    der Zeile, adjusted und source bleiben NULL, naechster Lauf macht
    keinen weiteren Claude-Call."""
    ni = _seed_row(db, company, "net_income", "Q2", Decimal("20000000000"))
    fake = _patch_all(
        monkeypatch,
        {SUB_URL: _submissions([("8-K", Q2_FILING_DATE, ACCN_Q2, "2.02,9.01")]),
         INDEX_URL_Q2: INDEX_JSON},
        {EXHIBIT_URL_Q2: EXHIBIT_HTML},
        {"non_gaap_net_income": None, "non_gaap_diluted_eps": None,
         "gaap_net_income": None, "gaap_diluted_eps": None,
         "adjustment_items": ""},
    )

    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert enriched == 0
    assert len(fake.messages.calls) == 1
    db.refresh(ni)
    assert ni.numeric_value_adjusted is None
    assert ni.adjustments_source is None
    assert ni.adjustments_note == "no non-GAAP reconciliation found"

    enriched2 = enrich_adjusted_from_earnings_releases(db, company, [YEAR])
    assert enriched2 == 0
    assert len(fake.messages.calls) == 1


def test_no_8k_in_window_skips_without_error(db, company, monkeypatch):
    """8-K ausserhalb des 75-Tage-Fensters: Periode wird uebersprungen,
    kein Claude-Call, kein Fehler."""
    ni = _seed_row(db, company, "net_income", "Q2", Decimal("20000000000"))
    late = date(YEAR + 1, 1, 15).isoformat()  # weit nach Q2-Ende + 75d
    fake = _patch_all(
        monkeypatch,
        {SUB_URL: _submissions([("8-K", late, ACCN_Q2, "2.02,9.01")])},
        {},
        {"non_gaap_net_income": 24000000000, "non_gaap_diluted_eps": None,
         "adjustment_items": ""},
    )

    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert enriched == 0
    assert fake.messages.calls == []
    db.refresh(ni)
    assert ni.numeric_value_adjusted is None


def test_max_llm_calls_caps_periods_newest_first(db, company, monkeypatch):
    """max_llm_calls deckelt: Kandidaten laufen absteigend (juengste Periode
    zuerst) — mit Cap 1 wird nur Q2 angereichert, Q1 bleibt NULL."""
    q1 = _seed_row(db, company, "net_income", "Q1", Decimal("20000000000"))
    q2 = _seed_row(db, company, "net_income", "Q2", Decimal("21000000000"))
    fake = _patch_all(
        monkeypatch,
        {SUB_URL: _submissions([
            ("8-K", Q1_FILING_DATE, ACCN_Q1, "2.02,9.01"),
            ("8-K", Q2_FILING_DATE, ACCN_Q2, "2.02,9.01"),
        ]),
         INDEX_URL_Q1: INDEX_JSON, INDEX_URL_Q2: INDEX_JSON},
        {EXHIBIT_URL_Q1: EXHIBIT_HTML, EXHIBIT_URL_Q2: EXHIBIT_HTML},
        {"non_gaap_net_income": 24000000000, "non_gaap_diluted_eps": None,
         "gaap_net_income": 21000000000, "gaap_diluted_eps": None,
         "source_kind": "table", "adjustment_items": "SBC"},
    )

    enriched = enrich_adjusted_from_earnings_releases(
        db, company, [YEAR], max_llm_calls=1,
    )
    db.commit()

    assert enriched == 1
    assert len(fake.messages.calls) == 1
    db.refresh(q1)
    db.refresh(q2)
    assert q2.numeric_value_adjusted == Decimal("24000000000")
    assert q1.numeric_value_adjusted is None


def test_submissions_json_fetched_once_per_run(db, company, monkeypatch):
    """Minor: das Submissions-JSON wird pro Aufruf EINMAL geholt, auch wenn
    mehrere Perioden angereichert werden."""
    q1 = _seed_row(db, company, "net_income", "Q1", Decimal("20000000000"))
    q2 = _seed_row(db, company, "net_income", "Q2", Decimal("20000000000"))
    url_json = {
        SUB_URL: _submissions([
            ("8-K", Q1_FILING_DATE, ACCN_Q1, "2.02,9.01"),
            ("8-K", Q2_FILING_DATE, ACCN_Q2, "2.02,9.01"),
        ]),
        INDEX_URL_Q1: INDEX_JSON, INDEX_URL_Q2: INDEX_JSON,
    }
    fake = _patch_all(
        monkeypatch,
        url_json,
        {EXHIBIT_URL_Q1: EXHIBIT_HTML, EXHIBIT_URL_Q2: EXHIBIT_HTML},
        {"non_gaap_net_income": 24000000000, "non_gaap_diluted_eps": None,
         "gaap_net_income": 20000000000, "gaap_diluted_eps": None,
         "source_kind": "table", "adjustment_items": "SBC"},
    )
    fetch_counts: dict[str, int] = {}

    def _counting_fetch(url):
        fetch_counts[url] = fetch_counts.get(url, 0) + 1
        return url_json.get(url)

    monkeypatch.setattr(adj, "_fetch_json", _counting_fetch)

    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert enriched == 2
    assert len(fake.messages.calls) == 2
    assert fetch_counts.get(SUB_URL) == 1
    db.refresh(q1)
    db.refresh(q2)
    assert q1.numeric_value_adjusted == Decimal("24000000000")
    assert q2.numeric_value_adjusted == Decimal("24000000000")


def test_focus_text_prioritizes_reconciliation_section():
    """Text ueber dem Cap wird um die Reconciliation-Sektion herum gekappt."""
    text = ("intro " * 20000) + "Reconciliation of GAAP to Non-GAAP net income" + (" tail" * 2000)
    focused = adj._focus_text(text, limit=40000)
    assert len(focused) <= 40000
    assert "Reconciliation of GAAP to Non-GAAP" in focused


# --- Tabellen-Prioritaet: source_kind steuert die Cross-Check-Toleranz ------


def _patch_period(monkeypatch, payload):
    return _patch_all(
        monkeypatch,
        {SUB_URL: _submissions([("8-K", Q2_FILING_DATE, ACCN_Q2, "2.02,9.01")]),
         INDEX_URL_Q2: INDEX_JSON},
        {EXHIBIT_URL_Q2: EXHIBIT_HTML},
        payload,
    )


def test_table_gate_rejects_gaap_beyond_half_percent(db, company, monkeypatch):
    """source_kind='table' verlangt 0.5%: GAAP-Pendant 0.9% neben der
    XBRL-Referenz (unter der Text-Toleranz von 2% noch akzeptabel) wird
    verworfen — Tabellenwerte muessen praktisch exakt passen."""
    ni = _seed_row(db, company, "net_income", "Q2", Decimal("5803000000"))
    fake = _patch_period(monkeypatch, {
        # 5.75e9 vs ref 5.803e9 = 0.91% daneben; bewusst NICHT glatt auf
        # 100 Mio, damit der Rundungs-Detektor nicht greift.
        "non_gaap_net_income": 6153000000, "non_gaap_diluted_eps": None,
        "gaap_net_income": 5750000000, "gaap_diluted_eps": None,
        "source_kind": "table", "adjustment_items": "SBC",
    })

    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert enriched == 0
    assert len(fake.messages.calls) == 1
    db.refresh(ni)
    assert ni.numeric_value_adjusted is None
    assert ni.adjustments_note == "no non-GAAP reconciliation found"


def test_text_gate_accepts_2pct_and_marks_note(db, company, monkeypatch):
    """source_kind='text': 2%-Toleranz plus Freitext-Kennzeichnung in der
    Note (gerundete '$5.9 billion'-Angaben aus dem Fliesstext)."""
    ni = _seed_row(db, company, "net_income", "Q2", Decimal("5803000000"))
    fake = _patch_period(monkeypatch, {
        # gaap 5.9e9 vs ref 5.803e9 = 1.67% — als text ok, als table nicht.
        "non_gaap_net_income": 6200000000, "non_gaap_diluted_eps": None,
        "gaap_net_income": 5900000000, "gaap_diluted_eps": None,
        "source_kind": "text", "adjustment_items": "SBC",
    })

    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert enriched == 1
    assert len(fake.messages.calls) == 1
    db.refresh(ni)
    assert ni.numeric_value_adjusted == Decimal("6200000000")
    assert ni.adjustments_note == \
        "Non-GAAP (Reconciliation 8-K): SBC (aus Freitext, ggf. gerundet)"


def test_rounding_detector_downgrades_table_claim_to_text(db, company, monkeypatch):
    """Rundungs-Detektor (Spec-Beispiel 5.8e9 vs 5.803e9): behaupteter
    Tabellenwert, aber Non-GAAP und GAAP glatt auf 100 Mio gerundet, waehrend
    die XBRL-Referenz ungerundet ist — wird als Freitext behandelt (Write mit
    Freitext-Note statt Vertrauen in exakte Tabellenwerte)."""
    ni = _seed_row(db, company, "net_income", "Q2", Decimal("5803000000"))
    fake = _patch_period(monkeypatch, {
        "non_gaap_net_income": 6200000000, "non_gaap_diluted_eps": None,
        "gaap_net_income": 5800000000, "gaap_diluted_eps": None,
        "source_kind": "table", "adjustment_items": "SBC",
    })

    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert enriched == 1
    db.refresh(ni)
    assert ni.numeric_value_adjusted == Decimal("6200000000")
    assert ni.adjustments_note.endswith(" (aus Freitext, ggf. gerundet)")


def test_missing_source_kind_rejects(db, company, monkeypatch):
    """source_kind fehlt: ohne Herkunftsangabe keine Toleranz zuordenbar —
    Werte verwerfen, Negativ-Marker gegen Dauer-Retries."""
    ni = _seed_row(db, company, "net_income", "Q2", Decimal("20000000000"))
    fake = _patch_period(monkeypatch, {
        "non_gaap_net_income": 24000000000, "non_gaap_diluted_eps": None,
        "gaap_net_income": 20000000000, "gaap_diluted_eps": None,
        "adjustment_items": "SBC",
    })

    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert enriched == 0
    assert len(fake.messages.calls) == 1
    db.refresh(ni)
    assert ni.numeric_value_adjusted is None
    assert ni.adjustments_note == "no non-GAAP reconciliation found"


def test_prompt_schema_requires_source_kind():
    """Das JSON-Schema im System-Prompt muss source_kind als Pflichtfeld
    ausweisen und die Tabellen-Prioritaet anweisen."""
    assert '"source_kind": "table"|"text"' in adj._SYSTEM_PROMPT
    assert "reconciliation TABLE" in adj._SYSTEM_PROMPT
    assert "source_kind='text'" in adj._SYSTEM_PROMPT


# --- period_end_date-Verifikation (Tabellenkopf) ---------------------------


def test_prompt_schema_requires_period_end_date():
    assert '"period_end_date": "YYYY-MM-DD"|null' in adj._SYSTEM_PROMPT


def test_period_end_mismatch_rejects(db, company, monkeypatch):
    """Falsche Spalte gelesen (Q4-Datum statt Q2): auch wenn der GAAP-
    Cross-Check zufaellig passen wuerde, wird die Periode verworfen —
    Negativ-Marker gegen Dauer-Retries."""
    ni = _seed_row(db, company, "net_income", "Q2", Decimal("20000000000"))
    fake = _patch_period(monkeypatch, {
        "non_gaap_net_income": 24000000000, "non_gaap_diluted_eps": None,
        "gaap_net_income": 20000000000, "gaap_diluted_eps": None,
        "source_kind": "table", "adjustment_items": "SBC",
        "period_end_date": f"{YEAR}-12-31",
    })

    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert enriched == 0
    assert len(fake.messages.calls) == 1
    db.refresh(ni)
    assert ni.numeric_value_adjusted is None
    assert ni.adjustments_note == "no non-GAAP reconciliation found"


def test_period_end_match_accepts_with_tolerance(db, company, monkeypatch):
    """Passendes Datum inkl. 52/53-Wochen-Toleranz (28.06. statt 30.06.)
    laesst den Write durch."""
    ni = _seed_row(db, company, "net_income", "Q2", Decimal("20000000000"))
    _patch_period(monkeypatch, {
        "non_gaap_net_income": 24000000000, "non_gaap_diluted_eps": None,
        "gaap_net_income": 20000000000, "gaap_diluted_eps": None,
        "source_kind": "table", "adjustment_items": "SBC",
        "period_end_date": f"{YEAR}-06-28",
    })

    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert enriched == 1
    db.refresh(ni)
    assert ni.numeric_value_adjusted == Decimal("24000000000")


def test_period_end_missing_stays_lenient(db, company, monkeypatch):
    """Fehlendes/unparsebares Header-Datum bleibt lenient (aeltere Releases
    ohne klares Spalten-Datum) — der GAAP-Cross-Check gilt weiterhin."""
    ni = _seed_row(db, company, "net_income", "Q2", Decimal("20000000000"))
    _patch_period(monkeypatch, {
        "non_gaap_net_income": 24000000000, "non_gaap_diluted_eps": None,
        "gaap_net_income": 20000000000, "gaap_diluted_eps": None,
        "source_kind": "table", "adjustment_items": "SBC",
        "period_end_date": None,
    })

    enriched = enrich_adjusted_from_earnings_releases(db, company, [YEAR])
    db.commit()

    assert enriched == 1
    db.refresh(ni)
    assert ni.numeric_value_adjusted == Decimal("24000000000")
