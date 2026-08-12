"""Tests fuer die Dokument-Bruecke (Stufe 2) der Statement-Recherche:
Websearch findet die Berichts-PDFs (url auch bei value null), kann sie
aber nicht lesen — Stufe 2 laedt das Dokument und gibt es Claude als
document-Block. Hermetisch: Stufe-1-Call via _call_claude gemockt,
Download via _download_document (conftest-Default: None), Dokument-Call
via _call_claude_document bzw. Fake-Client."""
import json
from datetime import date
from decimal import Decimal

import app.values.statement_research as sr
from app.values.models import CompanyValue
from tests.test_statement_research import (  # noqa: F401 (Fixture-Import)
    _entry,
    _rows,
    _seed,
    company,
)

YEAR = date.today().year - 2  # abgeschlossenes Jahr

DOC_URL = "https://www.sap.com/docs/investors/q1-statement.pdf"
PDF_BYTES = b"%PDF-1.4 fake quarterly statement"

# Kalender-FY der Fixture-Firma: Periodenenden fuer das strikte
# period_end_date-Gate der Dokument-Stufe.
PERIOD_END = {
    "FY": f"{YEAR}-12-31", "Q1": f"{YEAR}-03-31", "Q2": f"{YEAR}-06-30",
    "Q3": f"{YEAR}-09-30", "Q4": f"{YEAR}-12-31",
}


def _doc_entry(pt, value, **kw):
    """Dokument-Stufen-Entry: period_end_date gesetzt (strikte Stufe 2
    verwirft Werte ohne Stichtag)."""
    kw.setdefault("period_end", PERIOD_END[pt])
    return _entry(value, **kw)

# Original vor dem conftest-Patch sichern (Guard-Tests ohne Live-Netz:
# die URL faellt vor jedem Request durch _url_allowed).
_REAL_DOWNLOAD = sr._download_document


def _null_entry(url=DOC_URL):
    """SAP-Muster: Websuche liefert die Berichts-URL, aber keinen Wert."""
    return {"value": None, "quote": None, "url": url, "derived_from": None}


def _stage1_income(doc_url=DOC_URL):
    """Stufe 1: revenue aus Snippets lesbar, net_income nur als URL.
    Alle Eintraege tragen dieselbe Dokument-URL — genau EIN Kandidat,
    deterministische Call-Reihenfolge."""
    return {
        "revenue": {
            "FY": _entry(40_000_000_000, url=doc_url),
            "Q1": _entry(9_000_000_000, url=doc_url),
            "Q2": _entry(10_000_000_000, url=doc_url),
            "Q3": _entry(10_500_000_000, url=doc_url),
            "Q4": _entry(10_500_000_000, url=doc_url),
        },
        "net_income": {pt: _null_entry(doc_url) for pt in ("FY", "Q1", "Q2", "Q3", "Q4")},
    }


def _full_income():
    """Stufe 1 deckt alle Basis-Keys der Gruppe — kein Restbedarf."""
    payload = {}
    for key in ("revenue", "net_income", "ebitda"):
        payload[key] = {
            "FY": _entry(4_000_000_000),
            "Q1": _entry(1_000_000_000),
            "Q2": _entry(1_000_000_000),
            "Q3": _entry(1_000_000_000),
            "Q4": _entry(1_000_000_000),
        }
    payload["eps_diluted"] = {
        pt: _entry("1.00") for pt in ("FY", "Q1", "Q2", "Q3", "Q4")
    }
    return payload


def _mock_stage1(monkeypatch, payloads: dict):
    def fake(company_, year, group, cost_tracker=None):
        return payloads.get(group, {})

    monkeypatch.setattr(sr, "_call_claude", fake)


def _mock_download(monkeypatch, result=(PDF_BYTES, "pdf")):
    calls: list[str] = []

    def fake(url):
        calls.append(url)
        return result

    monkeypatch.setattr(sr, "_download_document", fake)
    return calls


def _mock_doc_call(monkeypatch, payload: dict):
    calls: list[tuple] = []

    def fake(company_, year, group, needed, doc_bytes, kind, doc_url,
             cost_tracker=None):
        calls.append((group, dict(needed), doc_bytes, kind, doc_url))
        return payload

    monkeypatch.setattr(sr, "_call_claude_document", fake)
    return calls


class _FakeBlock:
    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


def _fake_client(monkeypatch, answer: dict):
    """Echter _call_claude_document-Pfad mit Fake-Anthropic-Client —
    captured die messages.create-Payload."""
    import app.llm.claude as claude_mod
    from app.llm.rate_limiter import claude_limiter

    monkeypatch.setattr(claude_limiter, "min_interval", 0)
    captured: list[dict] = []

    class _FakeMessages:
        def create(self, **kwargs):
            captured.append(kwargs)
            return _FakeResponse(json.dumps(answer))

    class _FakeClient:
        messages = _FakeMessages()

    monkeypatch.setattr(claude_mod, "get_client", lambda: _FakeClient())
    return captured


# --- Trigger nur bei Restbedarf ---------------------------------------------


def test_no_residual_need_no_download_no_doc_call(db, company, monkeypatch):
    """Stufe 1 deckt alle Zellen der Gruppe -> Stufe 2 laedt nichts und
    ruft Claude nicht."""
    _mock_stage1(monkeypatch, {"income": _full_income()})
    downloads = _mock_download(monkeypatch)
    doc_calls = _mock_doc_call(monkeypatch, {})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])

    assert downloads == []
    assert doc_calls == []


def test_no_urls_from_stage1_no_download(db, company, monkeypatch):
    """Restbedarf, aber Stufe 1 lieferte keine URLs -> kein Download,
    kein Dokument-Call."""
    payload = _stage1_income()
    for key in payload:
        for pt in payload[key]:
            payload[key][pt]["url"] = None
    _mock_stage1(monkeypatch, {"income": payload})
    downloads = _mock_download(monkeypatch)
    doc_calls = _mock_doc_call(monkeypatch, {})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])

    assert downloads == []
    assert doc_calls == []


def test_provider_covered_cells_not_needy(db, company, monkeypatch):
    """Zellen mit Provider-Wert sind nicht beduerftig — Stufe-2-Trigger
    nur fuer wertlose Zellen."""
    for pt in ("FY", "Q1", "Q2", "Q3", "Q4"):
        _seed(db, company, "net_income", pt, 1_000_000_000,
              primary_method="provider")
    needed = sr._needy_cells(db, company, YEAR, "income",
                             ("FY", "Q1", "Q2", "Q3", "Q4"))
    assert "net_income" not in needed
    # revenue/eps/ebitda ohne Zeilen bleiben beduerftig.
    assert "revenue" in needed


# --- Dokument-Call schreibt fehlende Werte ----------------------------------


def test_document_values_fill_needy_cells(db, company, monkeypatch):
    """PDF-Werte fuellen die not_found-Zellen der Stufe 1; Persistenz
    wie Stufe 1 (statement_research, is_forecast=False)."""
    _mock_stage1(monkeypatch, {"income": _stage1_income()})
    _mock_download(monkeypatch)
    doc_payload = {
        "net_income": {
            "FY": _doc_entry("FY", 3_000_000_000, quote="Profit after tax 3,000", url=None),
            "Q1": _doc_entry("Q1", 700_000_000, quote="Profit after tax 700", url=None),
        },
    }
    doc_calls = _mock_doc_call(monkeypatch, doc_payload)

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    assert len(doc_calls) == 1
    group, needed, doc_bytes, kind, doc_url = doc_calls[0]
    assert group == "income"
    assert kind == "pdf" and doc_bytes == PDF_BYTES and doc_url == DOC_URL
    assert set(needed["net_income"]) == {"FY", "Q1", "Q2", "Q3", "Q4"}

    fy = _rows(db, company, "net_income", "FY")[0]
    assert fy.numeric_value == Decimal("3000000000")
    assert fy.primary_method == "statement_research"
    assert fy.is_forecast is False
    # url-Fallback: Zelle ohne eigene Quelle traegt die Dokument-URL.
    assert fy.source_link == DOC_URL
    assert DOC_URL in fy.source_name
    # Stufe-1-Werte bleiben unberuehrt.
    assert _rows(db, company, "revenue", "FY")[0].numeric_value == Decimal("40000000000")


def test_stage1_values_not_overwritten_by_stage2(db, company, monkeypatch):
    """needed_map beschraenkt Stufe-2-Writes auf beduerftige Zellen —
    auch wenn das Dokument abweichende Werte fuer gedeckte Zellen liefert."""
    _mock_stage1(monkeypatch, {"income": _stage1_income()})
    _mock_download(monkeypatch)
    doc_payload = {
        "revenue": {"FY": _doc_entry("FY", 99_000_000_000)},  # gedeckt -> ignorieren
        "net_income": {"FY": _doc_entry("FY", 3_000_000_000)},
    }
    _mock_doc_call(monkeypatch, doc_payload)

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    assert _rows(db, company, "revenue", "FY")[0].numeric_value == Decimal("40000000000")
    assert _rows(db, company, "net_income", "FY")[0].numeric_value == Decimal("3000000000")


# --- API-Payload: document-Block (PDF) und HTML-Fallback ---------------------


def test_pdf_document_block_in_api_payload(db, company, monkeypatch):
    """PDF geht als base64-document-Block VOR dem Text-Block an die
    Messages API (Anthropic-Format)."""
    import base64

    _mock_stage1(monkeypatch, {"income": _stage1_income()})
    _mock_download(monkeypatch)
    captured = _fake_client(monkeypatch, {"net_income": {"FY": _doc_entry("FY", 3_000_000_000)}})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])

    assert len(captured) == 1
    kwargs = captured[0]
    assert kwargs["model"] == sr.EXTRACT_MODEL
    content = kwargs["messages"][0]["content"]
    assert content[0]["type"] == "document"
    src = content[0]["source"]
    assert src["type"] == "base64"
    assert src["media_type"] == "application/pdf"
    assert base64.b64decode(src["data"]) == PDF_BYTES
    assert content[1]["type"] == "text"
    assert "net_income" in content[1]["text"]
    # Kein web_search-Tool im Dokument-Call.
    assert "tools" not in kwargs


def test_html_fallback_extracts_text_no_document_block(db, company, monkeypatch):
    """HTML-Dokumente: Text extrahieren (wie gaap_bridge._clean_html)
    statt document-Block."""
    html = (b"<html><head><script>x()</script></head><body>"
            b"<table><tr><td>Profit after tax</td><td>3,000</td></tr></table>"
            b"</body></html>")
    _mock_stage1(monkeypatch, {"income": _stage1_income()})
    _mock_download(monkeypatch, result=(html, "html"))
    captured = _fake_client(monkeypatch, {"net_income": {"FY": _doc_entry("FY", 3_000_000_000)}})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    content = captured[0]["messages"][0]["content"]
    assert len(content) == 1 and content[0]["type"] == "text"
    text = content[0]["text"]
    assert "Berichtstext" in text
    assert "Profit after tax" in text
    assert "<script>" not in text  # Tags gestrippt
    assert _rows(db, company, "net_income", "FY")[0].numeric_value == Decimal("3000000000")


# --- Groessen-/Seiten-Limits und SSRF-Guards --------------------------------


def test_pdf_over_page_limit_skipped(db, company, monkeypatch):
    """PDF > MAX_PDF_PAGES, aber die Seiten-Extraktion scheitert
    (Fake-Bytes unparsbar, kein Teil-PDF) -> Skip wie frueher, kein
    Claude-Call."""
    _mock_stage1(monkeypatch, {"income": _stage1_income()})
    _mock_download(monkeypatch)
    monkeypatch.setattr(sr, "_pdf_page_count", lambda data: 320)
    doc_calls = _mock_doc_call(monkeypatch, {"net_income": {"FY": _entry(3_000_000_000)}})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    assert doc_calls == []
    assert _rows(db, company, "net_income", "FY")[0].primary_method == "not_found"


def test_pdf_page_count_heuristic_on_unparsable_pdf():
    """pypdf scheitert an Fake-Bytes -> Groessen-Heuristik ~50 KB/Seite."""
    assert sr._pdf_page_count(b"%PDF- junk") == 1
    assert sr._pdf_page_count(b"x" * (sr._PDF_PAGE_BYTES_HEURISTIC * 150)) == 150


def test_url_guard_rejects_ips_localhost_and_non_http():
    assert sr._url_allowed("https://www.sap.com/investors/q1.pdf") is True
    assert sr._url_allowed("http://ir.example.com/report.html") is True
    assert sr._url_allowed("ftp://ir.example.com/report.pdf") is False
    assert sr._url_allowed("file:///etc/passwd") is False
    assert sr._url_allowed("https://127.0.0.1/report.pdf") is False
    assert sr._url_allowed("http://10.0.0.5/report.pdf") is False
    assert sr._url_allowed("http://192.168.1.1/x.pdf") is False
    assert sr._url_allowed("http://[::1]/x.pdf") is False
    assert sr._url_allowed("https://localhost/x.pdf") is False
    assert sr._url_allowed("https://foo.internal/x.pdf") is False
    assert sr._url_allowed("https://evil@127.0.0.1/x.pdf") is False


def test_real_download_rejects_private_url_without_request():
    """Der echte Download verwirft Guard-Verletzungen VOR jedem Request
    (hermetisch: es wird nie eine Verbindung aufgebaut)."""
    assert _REAL_DOWNLOAD("http://192.168.1.1/report.pdf") is None
    assert _REAL_DOWNLOAD("ftp://ir.example.com/report.pdf") is None


def test_real_download_size_limit_and_redirect_guard(monkeypatch):
    """Groessen-Limit bricht den Stream ab; Redirect auf private IP wird
    am Hop-Guard verworfen (httpx via MockTransport, kein Netz)."""
    import httpx

    monkeypatch.setattr(sr, "MAX_DOC_BYTES", 1000)

    def handler(request):
        if request.url.host == "big.example.com":
            return httpx.Response(
                200, headers={"content-type": "application/pdf"},
                content=b"%PDF-" + b"0" * 2000,
            )
        if request.url.host == "redir.example.com":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/x.pdf"})
        return httpx.Response(
            200, headers={"content-type": "application/pdf"},
            content=b"%PDF-ok",
        )

    real_client = httpx.Client

    def fake_client(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(**kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)

    assert _REAL_DOWNLOAD("https://big.example.com/huge.pdf") is None
    assert _REAL_DOWNLOAD("https://redir.example.com/doc.pdf") is None
    data, kind = _REAL_DOWNLOAD("https://ok.example.com/doc.pdf")
    assert kind == "pdf" and data == b"%PDF-ok"


# --- Seiten-Extraktion fuer grosse PDFs --------------------------------------


def _make_text_pdf(pages_text: list[str]) -> bytes:
    """Minimal-PDF mit einer Textzeile pro Seite (Helvetica, Tj-Operator)
    — hermetisch erzeugt, von pypdf les- und extrahierbar."""
    n = len(pages_text)
    objs: list[bytes] = []
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, text in enumerate(pages_text):
        content_num = 4 + 2 * i + 1
        objs.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                "/Resources << /Font << /F1 3 0 R >> >> "
                f"/Contents {content_num} 0 R >>"
            ).encode()
        )
        safe = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode()
        objs.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
            + stream + b"\nendstream"
        )
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for idx, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{idx} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    count = len(objs) + 1
    out += f"xref\n0 {count}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {count} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF"
    ).encode()
    return bytes(out)


def test_extract_relevant_pages_keyword_and_neighbors():
    """Keyword-Seite + je 1 Nachbarseite landen im Teil-PDF (SAP-Muster:
    Finanzverbindlichkeiten-Note im Geschaeftsbericht)."""
    import io

    from pypdf import PdfReader

    texts = [f"Vorwort Seite {i}" for i in range(10)]
    texts[5] = "Note 12: Financial liabilities and borrowings"
    result = sr._extract_relevant_pages(_make_text_pdf(texts), "balance")
    assert result is not None
    part, n_extracted, n_total = result
    assert (n_extracted, n_total) == (3, 10)
    reader = PdfReader(io.BytesIO(part))
    assert len(reader.pages) == 3
    joined = " ".join((p.extract_text() or "") for p in reader.pages)
    assert "Financial liabilities" in joined
    assert "Seite 4" in joined and "Seite 6" in joined  # Nachbarn
    assert "Seite 0" not in joined


def test_extract_relevant_pages_cap():
    """Viele Treffer-Seiten -> Deckel MAX_EXTRACT_PAGES greift."""
    texts = [f"Kapitalflussrechnung Seite {i}" for i in range(40)]
    part, n_extracted, n_total = sr._extract_relevant_pages(
        _make_text_pdf(texts), "cashflow",
    )
    assert n_total == 40
    assert n_extracted == sr.MAX_EXTRACT_PAGES == 30
    assert part[:5] == b"%PDF-"


def test_extract_relevant_pages_no_hit_returns_none():
    """Kein Keyword-Treffer -> None (Caller skippt das Dokument)."""
    pdf = _make_text_pdf(["Vorwort des Vorstands"] * 5)
    assert sr._extract_relevant_pages(pdf, "balance") is None


def test_extract_relevant_pages_unparsable_returns_none():
    assert sr._extract_relevant_pages(b"%PDF- junk", "balance") is None


def test_large_pdf_partial_extraction_attached(db, company, monkeypatch):
    """PDF > MAX_PDF_PAGES wird nicht mehr uebersprungen: das Teil-PDF
    der Keyword-Seiten geht an den Dokument-Call und zaehlt normal
    gegen das Budget."""
    _mock_stage1(monkeypatch, {"income": _stage1_income()})
    _mock_download(monkeypatch)
    monkeypatch.setattr(sr, "_pdf_page_count", lambda data: 320)
    part = b"%PDF-partial"
    monkeypatch.setattr(
        sr, "_extract_relevant_pages", lambda data, group: (part, 12, 320),
    )
    doc_calls = _mock_doc_call(
        monkeypatch, {"net_income": {"FY": _doc_entry("FY", 3_000_000_000)}},
    )

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    assert len(doc_calls) == 1
    assert doc_calls[0][2] == part  # Teil-PDF, nicht das Original
    assert _rows(db, company, "net_income", "FY")[0].numeric_value == Decimal("3000000000")


def test_large_pdf_no_hit_skipped_logged_no_budget(db, company, monkeypatch, caplog):
    """Grosses PDF ohne Keyword-Treffer: Skip + Log, verbraucht KEIN
    Call-Budget — der naechste Kandidat kommt dran."""
    import logging

    big = "https://ir.example.com/big-report.pdf"
    payload = _stage1_income(doc_url=big)  # big deckt am meisten -> zuerst
    for pt in ("Q1", "Q2", "Q3", "Q4"):
        payload["net_income"][pt] = _null_entry(DOC_URL)
    _mock_stage1(monkeypatch, {"income": payload})

    def fake_download(url):
        if "big" in url:
            return (b"%PDF-big annual report", "pdf")
        return (PDF_BYTES, "pdf")

    monkeypatch.setattr(sr, "_download_document", fake_download)
    monkeypatch.setattr(
        sr, "_pdf_page_count", lambda data: 320 if b"big" in data else 3,
    )
    monkeypatch.setattr(sr, "_extract_relevant_pages", lambda data, group: None)
    doc_calls = _mock_doc_call(
        monkeypatch, {"net_income": {"FY": _doc_entry("FY", 3_000_000_000)}},
    )

    with caplog.at_level(logging.INFO, logger="app.values.statement_research"):
        sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    assert "keine Keyword-Treffer" in caplog.text
    assert len(doc_calls) == 1
    assert doc_calls[0][4] == DOC_URL  # Budget blieb fuer den Kandidaten
    assert _rows(db, company, "net_income", "FY")[0].numeric_value == Decimal("3000000000")


# --- Kandidaten-Priorisierung ------------------------------------------------


def test_balance_candidates_prefer_half_year_and_annual_reports():
    """Bilanz-Gruppe: Halbjahres-/Geschaeftsberichte (Notes mit dem
    Schulden-Split) vor Quartals-Statements — trotz geringerer
    Perioden-Abdeckung."""
    q_url = "https://ir.example.com/q1-statement.pdf"
    hy_url = "https://ir.example.com/half-year-report.pdf"
    ar_url = "https://ir.example.com/annual-report.pdf"
    period_urls = {
        "FY": [q_url, ar_url],
        "Q1": [q_url],
        "Q2": [q_url, hy_url],
        "Q3": [q_url],
    }
    order = sr._candidate_docs(period_urls, ["FY", "Q1", "Q2", "Q3"], "balance")
    assert order == [ar_url, hy_url, q_url] or order == [hy_url, ar_url, q_url]
    # Innerhalb der Prioritaets-Stufe entscheidet weiter die Abdeckung.
    assert order[-1] == q_url


def test_candidate_docs_fy_gap_beats_pure_quarter_coverage():
    """FY-first-Budget: das Dokument, das die beduerftige FY-Zelle deckt,
    kommt vor dem Quartals-Kandidaten mit hoeherer Perioden-Abdeckung —
    das Call-Budget fliesst zuerst in die FY-Reihe (H-Rendite-Basis)."""
    fy_url = "https://ir.example.com/q4-statement.pdf"
    q_url = "https://ir.example.com/q2-statement.pdf"
    period_urls = {
        "FY": [fy_url], "Q1": [q_url], "Q2": [q_url], "Q3": [q_url],
    }
    order = sr._candidate_docs(period_urls, ["FY", "Q1", "Q2", "Q3"], "income")
    assert order == [fy_url, q_url]


def test_candidate_docs_without_fy_need_keep_coverage_order():
    """Ohne FY-Bedarf bleibt die reine Abdeckungs-Reihenfolge."""
    q_url = "https://ir.example.com/q2-statement.pdf"
    one_url = "https://ir.example.com/q1-statement.pdf"
    period_urls = {"Q1": [q_url, one_url], "Q2": [q_url], "Q3": [q_url]}
    order = sr._candidate_docs(period_urls, ["Q1", "Q2", "Q3"], "income")
    assert order == [q_url, one_url]


def test_candidate_docs_balance_priority_dominates_fy_first():
    """Die Bilanz-Priorisierung (Schulden-Split-Note) bleibt dominant:
    Halbjahresbericht ohne FY-Deckung vor dem FY-deckenden
    Quartals-Statement; FY-first ordnet nur innerhalb der Stufen."""
    q_url = "https://ir.example.com/q4-statement.pdf"
    hy_url = "https://ir.example.com/half-year-report.pdf"
    order = sr._candidate_docs(
        {"FY": [q_url], "Q2": [hy_url]}, ["FY", "Q2"], "balance",
    )
    assert order == [hy_url, q_url]


def test_needy_cells_list_fy_first(db, company):
    """Beduerftige Perioden stehen in _PERIODS-Ordnung — FY zuerst im
    Dokument-Prompt, unabhaengig von der Reihenfolge des Inputs."""
    needed = sr._needy_cells(db, company, YEAR, "income", ("Q1", "FY"))
    assert needed["revenue"] == ("FY", "Q1")


def test_income_candidates_annual_report_last_but_allowed():
    """income/cashflow: Statements zuerst, Geschaeftsbericht als LETZTER
    Kandidat zugelassen (statt nie)."""
    q_url = "https://ir.example.com/q4-statement.pdf"
    ar_url = "https://ir.example.com/annual-report.pdf"
    order = sr._candidate_docs({"FY": [ar_url, q_url]}, ["FY"], "income")
    assert order == [q_url, ar_url]
    # Halbjahresberichte sind fuer income/cashflow NICHT zurueckgestuft.
    hy_url = "https://ir.example.com/half-year-report.pdf"
    order = sr._candidate_docs({"Q2": [ar_url, hy_url]}, ["Q2"], "cashflow")
    assert order == [hy_url, ar_url]


def test_balance_stage2_downloads_priority_doc_first(db, company, monkeypatch):
    """End-to-end: die Bilanz-Stufe-2 laedt den Halbjahresbericht vor dem
    hoeher abdeckenden Quartals-Statement."""
    q_url = "https://ir.example.com/q1-statement.pdf"
    hy_url = "https://ir.example.com/half-year-report.pdf"
    payload = {
        "st_debt": {
            "FY": _null_entry(q_url), "Q1": _null_entry(q_url),
            "Q2": _null_entry(hy_url), "Q3": _null_entry(q_url),
            "Q4": _null_entry(q_url),
        },
    }
    _mock_stage1(monkeypatch, {"balance": payload})
    downloads = _mock_download(monkeypatch)
    _mock_doc_call(monkeypatch, {})

    sr.fetch_statement_research(db, company, YEAR, groups=["balance"])

    assert downloads[0] == hy_url


# --- non-IFRS-Sidecars -------------------------------------------------------


def test_non_ifrs_sidecar_written_with_doc_note(db, company, monkeypatch):
    """SAP-Muster: Basis (IFRS) kommt aus Stufe 1, non-IFRS-Spalte nur
    aus dem PDF — Sidecar mit Note 'Non-IFRS (Berichts-PDF)'."""
    payload = _stage1_income()
    # Basis via Websuche gelesen; ebitda nur als URL -> Restbedarf.
    payload["net_income"]["FY"] = _entry(3_000_000_000, url=DOC_URL)
    payload["ebitda"] = {"FY": _null_entry()}
    _mock_stage1(monkeypatch, {"income": payload})
    _mock_download(monkeypatch)
    doc_payload = {
        "ebitda": {"FY": _doc_entry("FY", 5_000_000_000, url=None)},
        "net_income_adjusted": {
            "FY": _doc_entry("FY", 3_500_000_000,
                             quote="Profit after tax (non-IFRS) 3,500", url=None),
        },
    }
    _mock_doc_call(monkeypatch, doc_payload)

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    ni = _rows(db, company, "net_income", "FY")[0]
    assert ni.numeric_value == Decimal("3000000000")
    assert ni.numeric_value_adjusted == Decimal("3500000000")
    assert ni.adjustments_note == "Non-IFRS (Berichts-PDF)"
    assert ni.adjustments_source.startswith("Profit after tax (non-IFRS)")
    assert DOC_URL in ni.adjustments_source
    assert _rows(db, company, "ebitda", "FY")[0].numeric_value == Decimal("5000000000")


def test_sidecar_gate_track_mixup_skipped(db, company, monkeypatch):
    """Sidecar weicht >60% vom DB-Basiswert ab (Spur-Verwechslung):
    verworfen. Unter-reported allein ist dagegen erlaubt (Non-IFRS darf
    unter reported liegen)."""
    payload = _stage1_income()
    payload["net_income"]["FY"] = _entry(4_000_000_000, url=DOC_URL)
    payload["ebitda"] = {"FY": _null_entry()}
    _mock_stage1(monkeypatch, {"income": payload})
    _mock_download(monkeypatch)
    _mock_doc_call(monkeypatch, {
        "ebitda": {"FY": _doc_entry("FY", 5_000_000_000)},
        "net_income_adjusted": {"FY": _doc_entry("FY", 12_000_000_000)},  # >150% aufwaerts daneben
    })

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    ni = _rows(db, company, "net_income", "FY")[0]
    assert ni.numeric_value == Decimal("4000000000")
    assert ni.numeric_value_adjusted is None


# --- Spalten-Gates der Dokument-Stufe ----------------------------------------


def test_stage2_missing_period_end_date_discarded(db, company, monkeypatch):
    """Stufe 2 ist strikt: das Dokument liegt vor, der Stichtag steht in
    der Tabelle — Werte ohne period_end_date werden verworfen; mit
    passendem Stichtag geschrieben."""
    _mock_stage1(monkeypatch, {"income": _stage1_income()})
    _mock_download(monkeypatch)
    _mock_doc_call(monkeypatch, {
        "net_income": {
            "FY": _doc_entry("FY", 3_000_000_000),
            "Q1": _entry(700_000_000),  # ohne period_end_date -> skip
        },
    })

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    assert _rows(db, company, "net_income", "FY")[0].numeric_value == Decimal("3000000000")
    q1 = _rows(db, company, "net_income", "Q1")[0]
    assert q1.numeric_value is None
    assert q1.primary_method == "not_found"


def test_stage2_prev_year_column_and_sidecar_discarded(db, company, monkeypatch):
    """Vorjahresspalten-Gate auch in Stufe 2 — inkl. Sidecar (SAP: die
    FY2024-adjusted-Serie kam aus der non-IFRS-Tabelle des PDFs)."""
    payload = _stage1_income()
    payload["net_income"]["FY"] = _entry(3_000_000_000, url=DOC_URL)
    _mock_stage1(monkeypatch, {"income": payload})
    _mock_download(monkeypatch)
    _mock_doc_call(monkeypatch, {
        "net_income": {
            "Q1": _doc_entry("Q1", 700_000_000, column_label=str(YEAR - 1)),
        },
        "net_income_adjusted": {
            "FY": _doc_entry("FY", 3_200_000_000, column_label=str(YEAR - 1)),
        },
    })

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    assert _rows(db, company, "net_income", "Q1")[0].primary_method == "not_found"
    ni = _rows(db, company, "net_income", "FY")[0]
    assert ni.numeric_value == Decimal("3000000000")
    assert ni.numeric_value_adjusted is None


def test_portal_urls_not_document_candidates(db, company, monkeypatch):
    """Portal-URLs aus Stufe 1 (GuruFocus & Co) sind keine Stufe-2-
    Dokumentkandidaten — kein Download, kein Dokument-Call."""
    payload = _stage1_income()
    for pt in payload["net_income"]:
        payload["net_income"][pt] = _null_entry(
            "https://www.gurufocus.com/stock/SAP/summary"
        )
    for pt in payload["revenue"]:
        payload["revenue"][pt]["url"] = "https://www.gurufocus.com/stock/SAP/summary"
    _mock_stage1(monkeypatch, {"income": payload})
    downloads = _mock_download(monkeypatch)
    doc_calls = _mock_doc_call(monkeypatch, {})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])

    assert downloads == []
    assert doc_calls == []


# --- not_found nach gelesenem Dokument --------------------------------------


def test_not_found_stamp_after_document_read(db, company, monkeypatch):
    """Dokument gelesen, Wert nachweislich nicht enthalten -> not_found-
    Platzhalter bleibt (rote Zelle), nur berichtete Perioden."""
    _mock_stage1(monkeypatch, {"income": _stage1_income()})
    _mock_download(monkeypatch)
    doc_calls = _mock_doc_call(monkeypatch, {"net_income": {"FY": _doc_entry("FY", 3_000_000_000)}})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    assert len(doc_calls) >= 1
    assert _rows(db, company, "net_income", "FY")[0].numeric_value == Decimal("3000000000")
    for pt in ("Q1", "Q2", "Q3", "Q4"):
        (row,) = _rows(db, company, "net_income", pt)
        assert row.numeric_value is None
        assert row.primary_method == "not_found"
        assert row.last_refresh_attempt is not None


# --- Kosten-Deckel -----------------------------------------------------------


def test_cost_cap_max_two_document_calls(db, company, monkeypatch):
    """Mehr als 2 Kandidaten-Dokumente, Bedarf bleibt -> genau 2
    Dokument-Calls (MAX_DOC_CALLS)."""
    payload = _stage1_income()
    urls = [f"https://ir.example.com/report-{i}.pdf" for i in range(4)]
    for pt, url in zip(("FY", "Q1", "Q2", "Q3"), urls):
        payload["net_income"][pt] = _null_entry(url)
    _mock_stage1(monkeypatch, {"income": payload})
    downloads = _mock_download(monkeypatch)
    doc_calls = _mock_doc_call(monkeypatch, {})  # Dokument liefert nichts

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])

    assert len(doc_calls) == sr.MAX_DOC_CALLS == 2
    assert len(downloads) == 2


def test_balance_budget_three_document_calls(db, company, monkeypatch):
    """Bilanz-Gruppe: Doc-Budget 3 (MAX_DOC_CALLS_BALANCE) — der
    Schulden-Split steht oft erst im dritten Kandidaten."""
    urls = [f"https://ir.example.com/report-{i}.pdf" for i in range(4)]
    payload = {
        "st_debt": {
            pt: _null_entry(url)
            for pt, url in zip(("FY", "Q1", "Q2", "Q3"), urls)
        },
    }
    _mock_stage1(monkeypatch, {"balance": payload})
    downloads = _mock_download(monkeypatch)
    doc_calls = _mock_doc_call(monkeypatch, {})  # Dokumente liefern nichts

    sr.fetch_statement_research(db, company, YEAR, groups=["balance"])

    assert len(doc_calls) == sr.MAX_DOC_CALLS_BALANCE == 3
    assert len(downloads) == 3


def test_failed_download_does_not_consume_call_budget(db, company, monkeypatch):
    """Fehlgeschlagene Downloads (None) zaehlen nicht gegen den
    Call-Deckel — der naechste Kandidat wird probiert."""
    broken = "https://ir.example.com/broken.pdf"
    # broken deckt die meisten beduerftigen Perioden -> erster Kandidat.
    payload = _stage1_income(doc_url=broken)
    for pt in ("Q1", "Q2", "Q3", "Q4"):
        payload["net_income"][pt] = _null_entry(DOC_URL)
    _mock_stage1(monkeypatch, {"income": payload})

    downloads: list[str] = []

    def fake_download(url):
        downloads.append(url)
        if "broken" in url:
            return None
        return (PDF_BYTES, "pdf")

    monkeypatch.setattr(sr, "_download_document", fake_download)
    doc_calls = _mock_doc_call(monkeypatch, {
        "net_income": {
            "FY": _doc_entry("FY", 4_000_000_000),
            "Q1": _doc_entry("Q1", 1_000_000_000),
            "Q2": _doc_entry("Q2", 1_000_000_000),
            "Q3": _doc_entry("Q3", 1_000_000_000),
            "Q4": _doc_entry("Q4", 1_000_000_000),
        },
    })

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    assert downloads == [broken, DOC_URL]
    assert len(doc_calls) == 1  # nur der erfolgreiche Download fuehrt zum Call
    assert doc_calls[0][4] == DOC_URL
    assert _rows(db, company, "net_income", "FY")[0].numeric_value == Decimal("4000000000")


def test_stage2_stops_when_need_satisfied(db, company, monkeypatch):
    """Deckt das erste Dokument den kompletten Restbedarf, entfaellt der
    zweite Dokument-Call trotz weiterer Kandidaten."""
    payload = _stage1_income()
    payload["net_income"]["FY"] = _null_entry("https://ir.example.com/other.pdf")
    _mock_stage1(monkeypatch, {"income": payload})
    _mock_download(monkeypatch)

    def _flows(fy, q):
        return {
            "FY": _doc_entry("FY", fy), "Q1": _doc_entry("Q1", q),
            "Q2": _doc_entry("Q2", q), "Q3": _doc_entry("Q3", q),
            "Q4": _doc_entry("Q4", q),
        }

    doc_calls = _mock_doc_call(monkeypatch, {
        "net_income": _flows(4_000_000_000, 1_000_000_000),
        "eps_diluted": {pt: _doc_entry(pt, "1.00") for pt in ("FY", "Q1", "Q2", "Q3", "Q4")},
        "ebitda": _flows(8_000_000_000, 2_000_000_000),
    })

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])

    assert len(doc_calls) == 1


# --- FY-only-Backfill: N+1-Dokumente + Vergleichsspalten-Modus ---------------


def test_doc_system_prompt_backfill_mentions_comparative_column(company):
    """fy_backfill: der Doc-Prompt verlangt die Vorjahres-Vergleichs-
    spalte des N+1-Berichts; Default-Prompt unveraendert."""
    sp = sr._build_doc_system_prompt(company, YEAR, "income", fy_backfill=True)
    assert str(YEAR + 1) in sp
    assert "Vergleichsspalte" in sp
    assert "Vergleichsspalte" not in sr._build_doc_system_prompt(company, YEAR, "income")


def test_fy_only_document_stage_runs_in_backfill_mode(db, company, monkeypatch):
    """FY-only-Modus: der Dokument-Call laeuft mit fy_backfill=True
    (Vergleichsspalten-Prompt), und N+1-Spaltenlabels passieren das
    Gate, wenn period_end_date auf das N-Jahresende passt."""
    payload = {"net_income": {"FY": _null_entry()}}

    def fake_stage1(company_, year, group, cost_tracker=None, periods=None):
        return payload if group == "income" else {}

    monkeypatch.setattr(sr, "_call_claude", fake_stage1)
    _mock_download(monkeypatch)

    doc_calls: list[dict] = []

    def fake_doc(company_, year, group, needed, doc_bytes, kind, doc_url,
                 cost_tracker=None, fy_backfill=False):
        doc_calls.append({"group": group, "fy_backfill": fy_backfill})
        return {
            "net_income": {
                "FY": _doc_entry("FY", 3_000_000_000,
                                 column_label=f"Q4 {YEAR + 1}"),
            },
        }

    monkeypatch.setattr(sr, "_call_claude_document", fake_doc)

    sr.fetch_statement_research(db, company, YEAR, groups=["income"],
                                periods=("FY",))
    db.commit()

    assert doc_calls and doc_calls[0]["fy_backfill"] is True
    assert _rows(db, company, "net_income", "FY")[0].numeric_value == Decimal("3000000000")


# --- Bilanz-Stichtags-Gate auch in Stufe 2 -----------------------------------


def test_stage2_balance_q4_fy_contradiction_discards_both(db, company, monkeypatch):
    """Q4/FY-Stichtags-Widerspruch greift auch fuer Dokument-Werte —
    beide verworfen, not_found-Stempel bleibt."""
    payload = {
        "cash_and_equivalents": {
            pt: _null_entry() for pt in ("FY", "Q1", "Q2", "Q3", "Q4")
        },
    }
    _mock_stage1(monkeypatch, {"balance": payload})
    _mock_download(monkeypatch)
    _mock_doc_call(monkeypatch, {
        "cash_and_equivalents": {
            "FY": _doc_entry("FY", 15_263_000_000),
            "Q4": _doc_entry("Q4", 14_495_000_000),
        },
    })

    sr.fetch_statement_research(db, company, YEAR, groups=["balance"])
    db.commit()

    assert _rows(db, company, "cash_and_equivalents", "FY")[0].primary_method == "not_found"
    assert _rows(db, company, "cash_and_equivalents", "Q4")[0].primary_method == "not_found"


# --- Karenz unveraendert -----------------------------------------------------


def test_unreported_periods_untouched_by_stage2(db, company, monkeypatch):
    """Laufendes Jahr: Stufe 2 schreibt/stempelt nur berichtete Perioden
    — unberichtete bleiben komplett ohne Zeile, auch wenn das Dokument
    Werte liefert."""
    from tests.test_statement_research import _running_year

    year = _running_year(db, company)  # Q1/Q2 berichtet, Q3/Q4/FY nicht
    payload = {
        "revenue": {"Q1": _entry(9_000_000_000), "Q2": _entry(9_500_000_000)},
        "net_income": {pt: _null_entry() for pt in ("FY", "Q1", "Q2", "Q3", "Q4")},
    }
    _mock_stage1(monkeypatch, {"income": payload})
    _mock_download(monkeypatch)
    # Verschobenes FY-Ende: Periodenenden fuer das strikte Stufe-2-Gate
    # aus derselben Konvention wie der Produktionscode ableiten.
    from app.values.detail_page import quarter_end_date

    def _q_end(pt):
        return quarter_end_date(
            year, pt, company.fiscal_year_end_month, company.fiscal_year_end_day,
        ).isoformat()

    doc_calls = _mock_doc_call(monkeypatch, {
        "net_income": {
            **{pt: _entry(700_000_000, period_end=_q_end(pt))
               for pt in ("Q1", "Q2", "Q3", "Q4")},
            "FY": _entry(700_000_000),
        },
    })

    sr.fetch_statement_research(db, company, year, groups=["income"])
    db.commit()

    # needed enthaelt nur berichtete Perioden.
    _, needed, *_ = doc_calls[0]
    assert set(needed["net_income"]) == {"Q1", "Q2"}
    assert _rows(db, company, "net_income", "Q1", year=year)[0].numeric_value == Decimal("700000000")
    assert _rows(db, company, "net_income", "Q2", year=year)[0].numeric_value == Decimal("700000000")
    for pt in ("Q3", "Q4", "FY"):
        assert _rows(db, company, "net_income", pt, year=year) == []


def test_year_in_grace_no_stage2(db, company, monkeypatch):
    """Keine berichtete Periode (Karenz): weder Stufe-1- noch
    Dokument-Call."""
    from datetime import timedelta

    t = date.today() + timedelta(days=330)
    company.fiscal_year_end_month = t.month
    company.fiscal_year_end_day = t.day
    db.commit()
    _mock_stage1(monkeypatch, {"income": _stage1_income()})
    downloads = _mock_download(monkeypatch)
    doc_calls = _mock_doc_call(monkeypatch, {})

    assert sr.fetch_statement_research(db, company, t.year) == 0
    assert downloads == []
    assert doc_calls == []
