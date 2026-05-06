"""Claude-based extraction of financial values from IR PDFs.

Sends a PDF to Claude (native PDF support) along with a structured-extraction
prompt for the requested period. Returns a dict keyed by our value_keys.
"""
import base64
import json
import logging
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import anthropic
from pypdf import PdfReader

from app.config import settings
from app.llm.rate_limiter import claude_limiter

# Heuristic: PDFs above this raw-bytes size are likely too token-heavy as images.
# We then fall back to extracted text, which is ~5-10x smaller.
PDF_IMAGE_BYTES_LIMIT = 5 * 1024 * 1024  # 5 MB raw PDF
# Anthropic's PDF-as-image mode caps at 100 pages per request, so anything
# longer must go through the text-extraction path regardless of bytes.
PDF_IMAGE_PAGES_LIMIT = 100
# Hard cap on text we send to Claude. Sonnet 4.6 mit 1M-context-Beta
# erlaubt ~3M chars Eingabe — wir lassen viel Headroom fuer System-Prompt
# + Per-Key-Beschreibungen + Claude's eigene Antwort, und damit smart
# truncation bei wirklich riesigen PDFs (38 MB Adidas 2025) noch greifen
# kann ohne unter die wichtigen Notes zu rutschen.
MAX_TEXT_CHARS = 1_500_000

# Keywords whose presence on a page signals "this is a financial statement
# or a note that we very likely need". Used to prioritise pages when a PDF's
# raw text exceeds MAX_TEXT_CHARS — typically at the END of long IFRS
# annual reports where statements + notes live behind ~200 pages of narrative.
_HIGH_VALUE_PAGE_PATTERNS = re.compile(
    r"\b("
    r"consolidated balance sheet|consolidated income statement|"
    r"consolidated statement of (financial position|cash flows?|comprehensive income|changes in equity|profit or loss)|"
    r"konzernbilanz|konzern-?gewinn- und verlustrechnung|konzern-?kapitalflussrechnung|"
    r"konzern-?eigenkapitalveraenderungsrechnung|kapitalflussrechnung|gewinn- und verlustrechnung|"
    r"cash flows from (operating|investing|financing) activities|"
    r"net cash (provided by|used in) (operating|investing|financing) activities|"
    r"share-based payment|stock-based compensation|equity-settled|"
    r"long-?term debt|borrowings|lease liabilit(ies|y)|leasingverbindlichkeiten|"
    r"cash and cash equivalents|liquide mittel|"
    r"marketable securities|short-?term investments|"
    r"shares outstanding|share capital|grundkapital|"
    r"net income( attributable| for the period)?|nettogewinn|"
    r"dividends paid|dividenden(zahlung|ausschuettung)?|"
    r"repurchase of (common|treasury) (stock|shares)|aktienrueckkauf|"
    r"^\s*note\s+\d+|^\s*nr\.\s+\d+\s+—"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

logger = logging.getLogger(__name__)

# Keys whose value MUST be stored as a non-negative number.
# Cash-flow statements often show outflows in parentheses or with a leading
# minus (e.g. "Dividends paid: (174)"). Different filers use different
# conventions, so we normalise all of these to absolute value at extraction
# time. Same convention as the Yahoo provider (yahoo.py:_fetch_from_cashflow
# uses abs_value=True for buyback/dividends).
ALWAYS_POSITIVE_KEYS = frozenset({
    # cash outflow events (always reported as positive amount paid)
    "dividends",
    "buyback_volume",
    "sbc",
    # balance sheet items (always positive when shown)
    "cash_and_equivalents",
    "marketable_securities_st",
    "marketable_securities_lt",
    "lease_liabilities",
    "long_term_debt",
    "shares_outstanding",
})

# Keys we try to extract from every uploaded IR document.
# Each entry has detailed location hints for both US-GAAP (10-K/10-Q) and
# IFRS filers (20-F, German/EU annual reports), plus Notes-Abschnitt
# locations as fallback. The goal is to give Claude enough context that
# "value=null" is only the answer when the data really isn't in the PDF.
EXTRACTION_KEYS: list[tuple[str, str]] = [
    ("net_income",
     "Net Income (Konzern-Nettogewinn nach Steuern, attributable to shareholders/parent). "
     "Suchorte: (1) Income Statement / Konzern-GuV → letzte Zeile 'Net income' / 'Profit for the period attributable to shareholders'. "
     "(2) Bei IFRS oft 'Net income/(loss) attributable to shareholders' (NICHT 'attributable to non-controlling interests'). "
     "(3) Konzernzahlen, KEINE non-GAAP-Adjustments / 'underlying' / 'adjusted'. "
     "VORZEICHEN beibehalten (Verluste negativ)."),
    ("fcf",
     "Free Cash Flow = Operating Cash Flow − Capital Expenditures (CapEx). "
     "Suchorte: (1) Cash Flow Statement: 'Net cash from operating activities' MINUS 'Purchases of property, plant and equipment' / 'Capital expenditures' / 'Investments in PP&E'. "
     "(2) Manche Berichte zeigen FCF direkt als Highlight-Kennzahl in Management Report / Highlights ('Free Cash Flow: X'). "
     "(3) IFRS: 'Cash flow from operating activities' minus 'Acquisition of property, plant and equipment'. "
     "Wenn FCF direkt ausgewiesen ist, diesen Wert nehmen. Sonst Komponenten subtrahieren. POSITIV bei normaler Cash-Generierung."),
    ("sbc",
     "Stock Based Compensation Expense / anteilsbasierte Vergütung (jährlicher Aufwand, nicht kumulativ). "
     "Suchorte (PRÜFE ALLE 4 BEVOR du value=null setzt): "
     "(1) Cash Flow Statement → Add-back-Zeile 'Stock-based compensation expense' / 'Share-based payment expense' (typisch US-GAAP). "
     "(2) Statement of Changes in Equity → Spalte 'Equity-settled share-based payment' / 'Share-based payments' (typisch IFRS — Adidas, BMW, Siemens, ASML — Summe pro Periode). "
     "(3) Notes-Abschnitt 'Share-based payments' / 'Anteilsbasierte Vergütung' / 'Note X: Share-based compensation' — dort steht meist 'Total share-based payment expense for the period: X'. "
     "(4) Personnel-Cost-Note → Aufschlüsselung enthält oft eine SBC-Zeile. "
     "WERT: immer POSITIV als Aufwandsbetrag (Vorzeichen ignorieren). "
     "value=null NUR wenn alle 4 Quellen leer sind — und dann reason muss sagen welche du geprüft hast."),
    ("buyback_volume",
     "Aktienrückkäufe (Cash-Outflow für Treasury Share Purchases) für die Periode. "
     "Suchorte: (1) Cash Flow Statement → 'Repurchase of common stock' / 'Treasury stock purchases' / 'Acquisition of treasury shares' im Financing-Abschnitt. "
     "(2) Statement of Changes in Equity → 'Purchase of treasury shares' / 'Aktienrückkauf'. "
     "(3) Notes/Press Release zu Buyback Programs: kumuliertes Volumen. "
     "WERT: POSITIV als Cash-Outflow-Betrag (Vorzeichen ignorieren). Wenn KEIN Rückkaufprogramm aktiv war, value=0 und reason='Kein Rückkauf in der Periode'."),
    ("dividends",
     "Dividenden-Cashout / Dividends Paid für die Periode (zur Vermeidung: Bezahlte Dividenden ≠ je Aktie ≠ vorgeschlagene Dividende). "
     "Suchorte: (1) Cash Flow Statement → 'Dividends paid to shareholders' / 'Cash dividends' im Financing-Abschnitt. "
     "(2) Statement of Changes in Equity → 'Dividends paid' / 'Dividenden-Ausschüttung'. "
     "WERT: POSITIV als Auszahlungsbetrag. Wenn KEINE Dividende, value=0 und reason='Keine Dividendenzahlung in der Periode'."),
    ("cash_and_equivalents",
     "Cash and Cash Equivalents zum Bilanzstichtag. "
     "Suchorte: (1) Balance Sheet → ASSETS-Seite oben 'Cash and cash equivalents'. "
     "(2) IFRS: 'Cash and cash equivalents' / 'Liquide Mittel'. "
     "OHNE Marketable Securities (die werden separat extrahiert). "
     "Wenn nur ein aggregierter Wert 'Cash and short-term investments' ausgewiesen ist, diesen unter cash_and_equivalents nehmen und marketable_securities_st auf 0 setzen mit Begründung."),
    ("marketable_securities_st",
     "Short-term Marketable Securities / Short-term Investments (current assets). "
     "Suchorte: (1) Balance Sheet → current assets, separat NACH 'Cash and equivalents'. "
     "(2) Bezeichnungen: 'Short-term investments', 'Marketable securities', 'Investment securities (current)', 'Wertpapiere (kurzfristig)'. "
     "Falls nicht separat ausgewiesen: value=0 mit reason='Nicht separat im Balance Sheet'."),
    ("marketable_securities_lt",
     "Long-term Marketable Securities / Long-term Investments (non-current assets). "
     "Suchorte: (1) Balance Sheet → non-current assets, 'Long-term investments' / 'Other investments'. "
     "(2) Notes zur Investments-Aufschlüsselung. "
     "Falls nicht separat ausgewiesen: value=0 mit reason='Nicht separat'."),
    ("lease_liabilities",
     "Lease Liabilities = Operating Lease Liabilities + Finance Lease Liabilities, current + non-current zusammen. "
     "Suchorte: (1) Balance Sheet → 'Lease liabilities (current)' UND 'Lease liabilities (non-current)' summieren. "
     "(2) IFRS 16: 'Leasingverbindlichkeiten kurzfristig + langfristig'. "
     "(3) Notes zu Leasing → Total Lease Liabilities. "
     "Wenn nur eine Position vorhanden, diese; sonst Summe. POSITIV. "
     "Falls Lease Liabilities nicht separat ausgewiesen sind (z.B. in 'Other liabilities' versteckt), value=null und reason='In Other liabilities aggregiert'."),
    ("long_term_debt",
     "Long-term Debt / langfristige Finanzschulden zum Bilanzstichtag. "
     "Suchorte: (1) Balance Sheet → non-current liabilities 'Long-term debt' / 'Bonds and notes' / 'Borrowings (non-current)'. "
     "(2) IFRS: 'Langfristige Finanzverbindlichkeiten' / 'Anleihen' / 'Bankdarlehen'. "
     "OHNE current portion of long-term debt, OHNE Lease Liabilities (separat). POSITIV."),
    ("shares_outstanding",
     "Shares Outstanding — entweder (a) Diluted Weighted Average Shares Outstanding aus dem Income Statement (für Earnings-Berechnung) oder (b) Shares Outstanding zum Bilanzstichtag (für Market Cap). "
     "Suchorte: (1) Income Statement Bottom: 'Diluted weighted average shares outstanding'. "
     "(2) Notes 'Equity / Capital Stock' → 'Shares issued / outstanding at year-end'. "
     "(3) Cover/Highlights des Annual Report. "
     "Im Zweifel: zeitpunktbezogene Shares (Bilanz) bevorzugen, weil wir damit Market Cap berechnen. "
     "WERT als ABSOLUTE STÜCK-ZAHL (z.B. 178549084), NICHT in Mio."),
]


SYSTEM_PROMPT = """Du bist Senior Equity Research Analyst, spezialisiert auf das
exhaustive Lesen von Geschäftsberichten, 10-K, 20-F, IFRS- und US-GAAP-
Konzernabschlüssen sowie Quartalsberichten.

DEINE AUFGABE: Extrahiere präzise Finanzkennzahlen aus dem hochgeladenen
PDF für genau den angegebenen Berichts-Zeitraum.

KERN-DISZIPLIN — bevor du value=null lieferst, MUSST du:
- ALLE im Per-Key-Hinweis genannten Suchorte explizit prüfen
- Sowohl Cash Flow Statement ALS AUCH Statement of Changes in Equity
  ALS AUCH die Notes durchgehen (typische Suchreihenfolge unten)
- Bei IFRS-Filern (20-F, deutsche/EU-Konzernabschlüsse, Adidas/BMW/Siemens/
  ASML/SAP/etc.) zusätzlich gezielt im Equity Statement und im Notes-
  Abschnitt suchen — IFRS gliedert anders als US-GAAP
- Im 'reason'-Feld dokumentieren WO du gesucht hast und WARUM nichts
  vorhanden ist (z.B. "geprüft: CF-Statement S.91, Equity Statement S.97,
  Notes 27 — überall keine separate SBC-Zeile, in Note 28
  'Personnel costs' nur Total ohne Aufgliederung").

TYPISCHE ABSCHNITTE in einem Annual Report (in dieser Reihenfolge prüfen):
1. Highlights / Five-Year Overview (oft alle Kernzahlen kompakt)
2. Management Report / Operating & Financial Review
3. Consolidated Income Statement (GuV)
4. Consolidated Balance Sheet (Bilanz)
5. Consolidated Statement of Cash Flows (Kapitalflussrechnung)
6. Consolidated Statement of Changes in Equity (Eigenkapital-Veränderungen)
7. Notes to the Financial Statements (alle einzelnen Notes durchgehen!)

WICHTIGE REGELN:
1. Antworte AUSSCHLIESSLICH als gültiges JSON, kein Markdown davor/danach.
2. Werte in Base-Units (1450000000 für 1,45 Mrd, NICHT '1.45B' / '1450 Mio').
3. Währung explizit (USD/EUR/...) wenn aus Kontext klar, sonst null.
4. Pro gefundenem Wert: Seitenzahl + exaktes Quote-Snippet aus dem PDF.
5. Bei Quartalsberichten: 'period_basis' angeben (Q1_YTD vs Q1_standalone vs H1).
6. KEINE Schätzungen, keine Hochrechnungen. Nur was wirklich im PDF steht.
7. Wenn Wert null: 'reason' MUSS konkret die geprüften Stellen nennen.
8. Vorzeichen-Regel: Net Income mit echtem Vorzeichen (Verluste negativ),
   Bilanz-Posten und Cash-Outflows (SBC, Buyback, Dividends) immer POSITIV
   als Betrag (Vorzeichen ignorieren).
"""


def _build_user_prompt(period_coverage: str, period_year: int, doc_type: str, company_name: str) -> str:
    keys_block = "\n".join(f'  "{k}": {{ ... }}    // {desc}' for k, desc in EXTRACTION_KEYS)
    return f"""Unternehmen: {company_name}
Berichtstyp: {doc_type}
Berichts-Zeitraum: {period_coverage} {period_year}
{"(Achtung: Quartalsbericht — beachte ob YTD oder standalone)" if period_coverage != "FY" else ""}

Extrahiere folgende Werte EXAKT für {period_coverage} {period_year}.

Antworte ausschließlich in diesem JSON-Format:

{{
{keys_block}
}}

Pro Key folgendes Schema:
{{
  "value": <Zahl in Base-Units oder null>,
  "currency": "USD" | "EUR" | "GBP" | "CHF" | ... | null,
  "page": <Seitennummer im PDF>,
  "quote": "<exaktes Zitat aus dem PDF>",
  "period_basis": "FY" | "Q1_YTD" | "Q1_standalone" | "H1" | ... ,
  "reason": null oder kurze Erklärung wenn value=null
}}

Beispiel für net_income wenn gefunden:
"net_income": {{
  "value": 93736000000,
  "currency": "USD",
  "page": 31,
  "quote": "Net income: $93,736 million",
  "period_basis": "FY",
  "reason": null
}}

Beispiel für sbc wenn nicht gefunden:
"sbc": {{
  "value": null,
  "currency": null,
  "page": null,
  "quote": null,
  "period_basis": null,
  "reason": "Im Cash Flow Statement nicht als separater Posten ausgewiesen"
}}
"""


def _parse_json_from_response(text: str) -> dict | None:
    """Best-effort JSON parsing — tolerates ```json fences."""
    text = text.strip()
    if text.startswith("```"):
        # strip code fence
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # try to locate first { ... last }
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def _score_page(text: str) -> int:
    """Score a page by how likely it is to contain financial-statement data.
    Counts matches of high-value patterns (Balance Sheet keywords, Notes,
    cash flow lines etc.) — pages with score>0 are prioritised when the
    PDF is too big to fit MAX_TEXT_CHARS."""
    if not text:
        return 0
    return len(_HIGH_VALUE_PAGE_PATTERNS.findall(text))


def _extract_pdf_text(pdf_path: Path) -> str:
    """Extract text from all pages, prefixed with [Page N] markers so Claude
    can cite page numbers.

    If the raw text exceeds MAX_TEXT_CHARS, do smart truncation: keep ALL
    high-value pages (statements + notes detected via _HIGH_VALUE_PAGE_PATTERNS)
    plus a buffer of 2 surrounding pages, and drop low-value narrative pages
    until budget fits. This rescues IFRS reports where statements live behind
    150-200 pages of management commentary.
    """
    reader = PdfReader(str(pdf_path))
    pages: list[tuple[int, str, int]] = []  # (page_num, text, score)
    total_chars = 0
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        pages.append((i, text, _score_page(text)))
        total_chars += len(text)

    n = len(pages)
    if total_chars + n * 12 <= MAX_TEXT_CHARS:
        # All pages fit comfortably — no truncation needed.
        return "".join(f"\n\n[Page {i}]\n{t}" for i, t, _ in pages)

    # Smart truncation: select pages by priority.
    # 1. ALWAYS keep high-value pages (score > 0) and their +/-2 neighbours.
    keep: set[int] = set()
    for i, _, score in pages:
        if score > 0:
            for j in range(max(1, i - 2), min(n, i + 2) + 1):
                keep.add(j)
    # 2. ALWAYS keep first 5 pages (highlights / summary often sit there).
    for j in range(1, min(5, n) + 1):
        keep.add(j)
    # 3. Build kept-pages chunks, count chars.
    kept_pages = [(i, t) for i, t, _ in pages if i in keep]
    chars_used = sum(len(t) + 12 for _, t in kept_pages)

    # 4. Fill remaining budget with neutral pages (score=0) PROXIMATE to
    #    high-value pages (i.e. not at the very start of the doc).
    if chars_used < MAX_TEXT_CHARS:
        candidates = [(i, t) for i, t, _ in pages if i not in keep]
        # Prefer later pages (IFRS reports tend to put valuable stuff at the end).
        candidates.sort(key=lambda x: -x[0])
        for i, t in candidates:
            cost = len(t) + 12
            if chars_used + cost > MAX_TEXT_CHARS:
                continue
            keep.add(i)
            chars_used += cost

    # 5. Output in page-number order with a marker for skipped runs.
    final: list[str] = []
    last_page = 0
    skipped = 0
    for i, t, _ in pages:
        if i not in keep:
            skipped += 1
            last_page = i
            continue
        if skipped > 0:
            final.append(f"\n\n[... {skipped} pages skipped (low-value, e.g. narrative/CSR) ...]")
            skipped = 0
        final.append(f"\n\n[Page {i}]\n{t}")
        last_page = i
    if skipped > 0:
        final.append(f"\n\n[... {skipped} trailing pages skipped ...]")
    n_kept = len(keep)
    logger.info("PDF text smart-truncation: kept %d/%d pages (%d chars) of %s",
                n_kept, n, chars_used, pdf_path.name)
    return "".join(final)


EXTRACTION_MODEL = "claude-haiku-4-5-20251001"


def _build_pdf_content_blocks(pdf_path: Path, pdf_bytes: bytes, page_count: int, user_prompt: str) -> tuple[list, str]:
    """Returns (content_blocks, mode_note) — Image mode for small PDFs, Text fallback for large."""
    use_image_mode = (
        len(pdf_bytes) <= PDF_IMAGE_BYTES_LIMIT
        and page_count <= PDF_IMAGE_PAGES_LIMIT
    )
    if use_image_mode:
        pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
        return ([
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64},
            },
            {"type": "text", "text": user_prompt},
        ], f"PDF-as-image ({page_count}p)")

    text = _extract_pdf_text(pdf_path)
    return ([
        {
            "type": "text",
            "text": (
                f"Text-Extraktion aus PDF ({len(pdf_bytes) // 1024 // 1024} MB, {page_count} Seiten — "
                f"zu gross fuer Image-Modus). Seitenangaben aus '[Page N]'-Markern uebernehmen.\n\n"
                f"{text}\n\n---\n\n{user_prompt}"
            ),
        },
    ], f"PDF-as-text ({page_count}p)")


def _call_claude_extraction(client, content_blocks: list) -> str:
    """Single Claude call with retry on transient errors. Returns raw text.

    Sonnet 4.6 mit 1M-Context-Beta laesst grosse PDFs (>200k Tokens) in einem
    Call durch; ohne den Header wuerde der Request mit 400 abgelehnt sobald
    wir ueber das Standard-Context-Limit gehen.
    """
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            response = claude_limiter.call(lambda: client.messages.create(
                model=EXTRACTION_MODEL,
                max_tokens=8192,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content_blocks}],
                extra_headers={"anthropic-beta": "context-1m-2025-08-07"},
            ))
            text_parts = [
                getattr(b, "text", "") or ""
                for b in response.content
                if getattr(b, "type", None) == "text"
            ]
            return "\n".join(text_parts)
        except anthropic.APIError as e:
            last_exc = e
            logger.warning("Claude extraction attempt %d failed: %s", attempt + 1, e)
    raise last_exc or RuntimeError("Claude extraction failed without exception")


def _parse_one_entry(key: str, entry: dict | None) -> dict:
    """Normalize a single key's entry from Claude's JSON output."""
    if not isinstance(entry, dict):
        return {"value": None, "reason": "Key missing in response"}
    raw_val = entry.get("value")
    if raw_val is None:
        return {
            "value": None,
            "currency": entry.get("currency"),
            "page": entry.get("page"),
            "reason": entry.get("reason") or "Not extracted",
        }
    try:
        decimal_val = Decimal(str(raw_val))
    except (InvalidOperation, ValueError):
        return {
            "value": None,
            "currency": entry.get("currency"),
            "page": entry.get("page"),
            "reason": f"Could not parse value: {raw_val!r}",
        }
    if key in ALWAYS_POSITIVE_KEYS and decimal_val < 0:
        decimal_val = abs(decimal_val)
    return {
        "value": decimal_val,
        "currency": entry.get("currency"),
        "page": entry.get("page"),
        "quote": entry.get("quote"),
        "period_basis": entry.get("period_basis"),
    }


def _build_retry_prompt(missing_keys: list[str], period_coverage: str, period_year: int, company_name: str) -> str:
    """Second-pass prompt — focused on the keys that came back null."""
    keys_block = "\n".join(
        f'  "{k}": {{ ... }}    // {desc}'
        for k, desc in EXTRACTION_KEYS if k in set(missing_keys)
    )
    return f"""Beim ersten Versuch wurden die folgenden Werte fuer
{company_name} / {period_coverage} {period_year} mit value=null zurueckgegeben.

Bitte gehe nochmal SYSTEMATISCH durch das gesamte PDF — nicht nur durch die
gleiche Sektion wie eben. Fuer JEDEN dieser Keys gilt:
- Cash Flow Statement geprueft? Falls nichts → weiter.
- Statement of Changes in Equity (Eigenkapital-Veraenderungen) geprueft?
- ALLE Notes durchgehen (Inhaltsverzeichnis am Anfang nutzen).
- Highlights / Five-Year-Summary / Management Report?
- Vorgehen IFRS-spezifisch: typische DE/EU-Konzern-Berichte verstecken
  Items oft in Notes 'Personnel', 'Other liabilities', 'Equity'.

Liefere wieder das gleiche JSON-Format. Nur Werte die du JETZT findest.
Wenn weiterhin nichts da ist, im 'reason' KONKRET die geprueften Stellen
nennen (z.B. "Note 27 S.193, Equity Statement S.197, CF Statement S.91 —
keine separate SBC-Zeile, alles in 'Personnel costs' aggregiert").

JSON-Schema (NUR diese Keys):

{{
{keys_block}
}}

Pro Key:
{{
  "value": <Zahl in Base-Units oder null>,
  "currency": "USD" | "EUR" | ... | null,
  "page": <Seitennummer>,
  "quote": "<exaktes Zitat>",
  "period_basis": "FY" | "Q1_YTD" | ... ,
  "reason": null oder kurze Erklaerung wenn value=null
}}
"""


def extract_values_from_pdf(
    pdf_path: Path,
    *,
    company_name: str,
    document_type: str,
    period_coverage: str,
    period_year: int,
) -> tuple[dict, str | None]:
    """Run Claude extraction on the PDF in TWO passes.

    Pass 1: Extract all 11 keys in one call.
    Pass 2: For any keys that came back null, retry with an explicit
            "you missed these — search systematically" prompt. Catches
            cases where Claude default-scanned only the Cash Flow Statement
            and missed IFRS-style placements (Equity Statement, Notes).

    Returns ({key: {value, currency, page, quote, period_basis, reason}, ...},
             raw_first_response_text).
    """
    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")

    pdf_bytes = pdf_path.read_bytes()
    try:
        page_count = len(PdfReader(str(pdf_path)).pages)
    except Exception as e:
        logger.warning("Could not count pages for %s, assuming >limit: %s", pdf_path, e)
        page_count = PDF_IMAGE_PAGES_LIMIT + 1

    user_prompt = _build_user_prompt(period_coverage, period_year, document_type, company_name)
    content_blocks, mode_note = _build_pdf_content_blocks(pdf_path, pdf_bytes, page_count, user_prompt)
    logger.info("PDF extraction pass=1 mode=%s for %s/%s", mode_note, company_name, period_year)

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    raw_first = _call_claude_extraction(client, content_blocks)

    parsed = _parse_json_from_response(raw_first)
    if parsed is None:
        logger.warning("PDF extraction pass=1 JSON parse failed for %s/%s. Raw: %s",
                       company_name, period_year, raw_first[:500])
        return ({}, raw_first)

    results: dict[str, dict] = {key: _parse_one_entry(key, parsed.get(key)) for key, _ in EXTRACTION_KEYS}

    # Identify keys we still don't have — but skip those Claude marked
    # explicitly as "Kein X in der Periode" (e.g. no buyback this year).
    missing_keys = [
        k for k, r in results.items()
        if r.get("value") is None and "kein" not in (r.get("reason") or "").lower()
    ]
    if not missing_keys:
        logger.info("PDF extraction %s/%s: all %d keys found in pass 1",
                    company_name, period_year, len(EXTRACTION_KEYS))
        return (results, raw_first)

    logger.info("PDF extraction pass=2 retrying %d missing keys for %s/%s: %s",
                len(missing_keys), company_name, period_year, missing_keys)
    retry_prompt = _build_retry_prompt(missing_keys, period_coverage, period_year, company_name)
    retry_blocks, _ = _build_pdf_content_blocks(pdf_path, pdf_bytes, page_count, retry_prompt)
    try:
        raw_second = _call_claude_extraction(client, retry_blocks)
    except Exception as e:
        logger.warning("PDF extraction pass=2 failed for %s/%s: %s", company_name, period_year, e)
        return (results, raw_first)

    parsed_second = _parse_json_from_response(raw_second)
    if parsed_second is None:
        logger.warning("PDF extraction pass=2 JSON parse failed for %s/%s", company_name, period_year)
        return (results, raw_first)

    recovered = 0
    for key in missing_keys:
        new_entry = _parse_one_entry(key, parsed_second.get(key))
        if new_entry.get("value") is not None:
            results[key] = new_entry
            recovered += 1
        elif new_entry.get("reason"):
            # Update the reason with the more thorough second-pass explanation
            results[key] = {**results[key], "reason": new_entry["reason"]}
    logger.info("PDF extraction %s/%s pass=2 recovered %d/%d missing",
                company_name, period_year, recovered, len(missing_keys))
    return (results, raw_first)
