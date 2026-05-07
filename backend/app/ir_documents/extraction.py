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
# Hard cap on text we send to Claude. Tuned per model context window:
#   - Haiku 4.5  : 200k token context, KEIN 1M-Beta-Support → ~600k chars budget
#   - Sonnet 4.6 : 1M-Beta-Support → ~2.5M chars budget
# Smart truncation kuerzt bei Bedarf intelligent (high-value pages first).
MAX_TEXT_CHARS_HAIKU = 600_000
MAX_TEXT_CHARS_SONNET = 2_500_000

# Keywords whose presence on a page signals "this is a financial statement
# or a note that we very likely need". Used to prioritise pages when a PDF's
# raw text exceeds MAX_TEXT_CHARS — typically at the END of long IFRS
# annual reports where statements + notes live behind ~200 pages of narrative.
_HIGH_VALUE_PAGE_PATTERNS = re.compile(
    r"\b("
    r"consolidated balance sheet|consolidated income statement|"
    r"consolidated statement of (financial position|cash flows?|comprehensive income|changes in equity|profit or loss)|"
    r"konzernbilanz|konzern-?gewinn- und verlustrechnung|konzern-?kapitalflussrechnung|"
    r"konzern-?eigenkapitalveränderungsrechnung|kapitalflussrechnung|gewinn- und verlustrechnung|"
    r"cash flows from (operating|investing|financing) activities|"
    r"net cash (provided by|used in) (operating|investing|financing) activities|"
    r"share-based payment|stock-based compensation|equity-settled|"
    r"long-?term debt|borrowings|lease liabilit(ies|y)|leasingverbindlichkeiten|"
    r"cash and cash equivalents|liquide mittel|"
    r"marketable securities|short-?term investments|"
    r"shares outstanding|share capital|grundkapital|"
    r"net income( attributable| for the period)?|nettogewinn|"
    r"dividends paid|dividenden(zahlung|ausschüttung)?|"
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
    "shares_outstanding",
    # NOTE: net_debt absichtlich NICHT hier — kann legitim negativ sein
    # (Net Cash Position bei cash-rich Firmen wie Apple oder Allianz).
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
     "Suchorte (PRUEFE ALLE 4 BEVOR du value=0 setzt): "
     "(1) Cash Flow Statement → Add-back-Zeile 'Stock-based compensation expense' / 'Share-based payment expense' (typisch US-GAAP). "
     "(2) Statement of Changes in Equity → Spalte 'Equity-settled share-based payment' / 'Share-based payments' (typisch IFRS — Adidas, BMW, Siemens, ASML — Summe pro Periode). "
     "(3) Notes-Abschnitt 'Share-based payments' / 'Anteilsbasierte Vergütung' / 'Note X: Share-based compensation' — dort steht meist 'Total share-based payment expense for the period: X'. "
     "(4) Personnel-Cost-Note → Aufschlüsselung enthält oft eine SBC-Zeile. "
     "WERT: immer POSITIV als Aufwandsbetrag (Vorzeichen ignorieren). "
     "value=0 NUR wenn alle 4 Quellen leer sind — und dann reason muss sagen welche du geprüft hast (NIE value=null)."),
    ("buyback_volume",
     "Aktienrückkäufe (Cash-Outflow für Treasury Share Purchases) für die Periode. "
     "Suchorte: (1) Cash Flow Statement → 'Repurchase of common stock' / 'Treasury stock purchases' / 'Acquisition of treasury shares' im Financing-Abschnitt. "
     "(2) Statement of Changes in Equity → 'Purchase of treasury shares' / 'Aktienrückkauf'. "
     "(3) Notes/Press Release zu Buyback Programs — VORSICHT: nur das in der "
     "GEMELDETEN PERIODE tatsaechlich AUSGEGEBENE Cash. NICHT eine Multi-Year-"
     "Authorisation (z.B. '$50B Buyback Program approved' = ueber mehrere Jahre, "
     "nicht jaehrlich!). Im Zweifel den CF-Statement-Wert nehmen, der ist "
     "periodenecht. "
     "WERT: POSITIV als Cash-Outflow-Betrag (Vorzeichen ignorieren). Wenn KEIN Rückkaufprogramm aktiv war, value=0 und reason='Kein Rückkauf in der Periode'."),
    ("dividends",
     "Dividenden-Cashout / Dividends Paid für die Periode (zur Vermeidung: Bezahlte Dividenden ≠ je Aktie ≠ vorgeschlagene Dividende). "
     "Suchorte: (1) Cash Flow Statement → 'Dividends paid to shareholders' / 'Cash dividends' im Financing-Abschnitt. "
     "(2) Statement of Changes in Equity → 'Dividends paid' / 'Dividenden-Ausschüttung'. "
     "WERT: POSITIV als Auszahlungsbetrag. Wenn KEINE Dividende, value=0 und reason='Keine Dividendenzahlung in der Periode'."),
    ("net_debt",
     "Net Financial Debt / Nettofinanzschulden / Net Debt zum Bilanzstichtag. "
     "EINE einzige Zahl, wie sie das Unternehmen selbst definiert (Total Financial Debt minus Cash & near-Cash). "
     "Vorzeichen-konvention: POSITIV = mehr Debt als Cash, NEGATIV = Net Cash Position (z.B. Apple, Allianz). "
     "Suchorte (PRÜFE ALLE bevor du auf Null schließt — der Wert ist fast immer im Bericht prominent ausgewiesen):"
     " (1) HIGHLIGHTS / Financial Highlights / Five-Year-Summary am Anfang des AR — meist als 'Net Financial Debt: X' oder 'Net Debt: X' direkt angegeben (höchste Konfidenz). "
     " (2) Management Report / Operating & Financial Review → Sektion 'Liquidity', 'Financial Position' oder 'Capital Structure' — typische Bridge: 'Cash and equivalents − Total borrowings = Net Cash/Debt'. "
     " (3) Notes zu 'Borrowings' / 'Financial Liabilities' — am Ende der Note steht oft eine Net-Debt-Reconciliation. "
     " (4) IFRS-Filer: 'Nettofinanzschulden' / 'Nettoverschuldung' im Lagebericht. "
     " (5) Letzter Fallback: ableiten als (Total Borrowings + Lease Liabilities) − (Cash + Short-term Investments). KEINE long-term marketable securities reinrechnen — bei Versicherern/Banken sind das Reserve-Assets, kein freies Cash. "
     "VORZEICHEN ERHALTEN — Net Cash Position muss negativ sein. NICHT abs() anwenden."),
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
- Im 'reason'-Feld dokumentieren WO du gesucht hast und WARUM nichts da war
  (z.B. "geprüft: CF-Statement S.91, Equity Statement S.97, Notes 27 —
  überall keine separate SBC-Zeile").
- value=null ist OK wenn die Kennzahl wirklich nicht im PDF steht — der
  auto-web-fallback im Backend approximiert dann via Web-Recherche statt
  dass du Zahlen aus der Luft erfindest.

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
7. Wenn Wert null: 'reason' MUSS konkret die geprüften Stellen nennen
   (Auto-Web-Fallback approximiert dann via Web-Recherche).
8. Vorzeichen-Regel: Net Income mit echtem Vorzeichen (Verluste negativ),
   Bilanz-Posten und Cash-Outflows (SBC, Buyback, Dividends) immer POSITIV
   als Betrag (Vorzeichen ignorieren).
"""


def _build_user_prompt(period_coverage: str, period_year: int, doc_type: str, company_name: str) -> str:
    keys_block = "\n".join(f'  "{k}": {{ ... }}    // {desc}' for k, desc in EXTRACTION_KEYS)
    is_quarterly = period_coverage in ("Q1", "Q2", "Q3", "Q4", "H1", "H2")
    is_annual = period_coverage == "FY" and doc_type in ("ANNUAL_REPORT", "FORM_10K", "FORM_20F")
    # Guidance-Zieljahr:
    #  - Q-Bericht: gleiches FY (Q1 2026 → Outlook FY2026)
    #  - Annual Report: NAECHSTES FY (AR2025 → Outlook FY2026)
    if is_quarterly:
        guidance_target = period_year
    elif is_annual:
        guidance_target = period_year + 1
    else:
        guidance_target = None

    guidance_keys_block = "\n".join(f'  "{k}": {{ ... }}' for k, _ in EXTRACTION_KEYS)
    guidance_section = (
        f"""

ZUSAETZLICH: Suche nach Management Guidance / Outlook für das gesamte
FY{guidance_target}. Typische Stellen:
- 'Outlook'-/'Guidance'-/'Forecast'-Sektion (oft am Ende des Management Reports)
- Earnings Release / Press Release zum Quartal
- Highlights-Folie ('FY{guidance_target} Outlook')
- Annual Report Schluss-Kapitel 'Outlook for {guidance_target}'

Phrasen wie:
- 'We expect Net Income FY{guidance_target} in the range X to Y'
- 'FY{guidance_target} Free Cash Flow guidance: ~Z'
- 'Wir erwarten Nettofinanzschulden FY{guidance_target} von X'
- 'Confirmed dividend payout policy of N%'

Wenn eine Range gegeben ist (X bis Y), nimm den Mittelwert als value und
notiere die Range im quote-Feld. Wenn nur ein Punktwert: nimm den.

Output unter dem Extra-Schluessel "guidance_fy" mit gleichem Schema wie oben:

  "guidance_fy": {{
{guidance_keys_block}
  }}

Pro Guidance-Key:
{{
  "value": <Zahl in Base-Units oder null>,
  "currency": ...,
  "page": ...,
  "quote": "<Originalzitat inkl. Range falls gegeben>",
  "reason": null oder "Keine Guidance für diesen Wert im Bericht"
}}

Wenn der Bericht KEINE Guidance enthaelt: setze "guidance_fy": {{}} (leeres Objekt).
""" if guidance_target is not None else ""
    )
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
  "reason": null wenn Wert gefunden, sonst Erklärung welche Stellen geprüft
            wurden und warum nichts da war (Auto-Web-Fallback approximiert dann)
}}{guidance_section}

PDF-DISZIPLIN: value=null ist ehrlich, wenn die Kennzahl wirklich nicht im
hochgeladenen Dokument steht (z.B. Versicherer-Financial-Supplement ohne
Cash Flow Statement). LIEFERE keine erfundenen Zahlen aus diesem PDF — der
auto-web-fallback im Backend approximiert dann mit echter Web-Recherche.
'reason' MUSS konkret sein (welche Seiten/Notes du geprüft hast).

Beispiel für net_income wenn gefunden:
"net_income": {{
  "value": 93736000000,
  "currency": "USD",
  "page": 31,
  "quote": "Net income: $93,736 million",
  "period_basis": "FY",
  "reason": null
}}

Beispiel für sbc wenn nicht im Bericht ausgewiesen:
"sbc": {{
  "value": null,
  "currency": null,
  "page": null,
  "quote": null,
  "period_basis": null,
  "reason": "Geprüft: CF-Statement S.91, Equity Statement S.97, Notes 27 — keine separate SBC-Zeile gefunden"
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


def _extract_pdf_text(pdf_path: Path, max_chars: int) -> str:
    """Extract text from all pages, prefixed with [Page N] markers so Claude
    can cite page numbers.

    If the raw text exceeds max_chars, do smart truncation: keep ALL
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
    if total_chars + n * 12 <= max_chars:
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

    # 4. Wenn high-value pages selbst schon über Budget — dann Score-prio
    #    droppen (kleinste Scores zuerst) bis Budget passt.
    if chars_used > max_chars:
        scored = sorted(
            ((i, t, s) for i, t, s in pages if i in keep),
            key=lambda x: (x[2], -x[0]),  # niedriger Score zuerst raus, dann fruehe Seiten
        )
        for i, t, _ in scored:
            if chars_used <= max_chars:
                break
            keep.discard(i)
            chars_used -= len(t) + 12

    # 5. Fill remaining budget with neutral pages (score=0) PROXIMATE to
    #    high-value pages (i.e. not at the very start of the doc).
    if chars_used < max_chars:
        candidates = [(i, t) for i, t, _ in pages if i not in keep]
        # Prefer later pages (IFRS reports tend to put valuable stuff at the end).
        candidates.sort(key=lambda x: -x[0])
        for i, t in candidates:
            cost = len(t) + 12
            if chars_used + cost > max_chars:
                continue
            keep.add(i)
            chars_used += cost

    # 6. Output in page-number order with a marker for skipped runs.
    final: list[str] = []
    skipped = 0
    for i, t, _ in pages:
        if i not in keep:
            skipped += 1
            continue
        if skipped > 0:
            final.append(f"\n\n[... {skipped} pages skipped (low-value, e.g. narrative/CSR) ...]")
            skipped = 0
        final.append(f"\n\n[Page {i}]\n{t}")
    if skipped > 0:
        final.append(f"\n\n[... {skipped} trailing pages skipped ...]")
    n_kept = len(keep)
    logger.info("PDF text smart-truncation: kept %d/%d pages (%d chars) of %s",
                n_kept, n, chars_used, pdf_path.name)
    return "".join(final)


EXTRACTION_MODEL_CHEAP = "claude-haiku-4-5-20251001"
EXTRACTION_MODEL_BIG = "claude-sonnet-4-6"


def _build_pdf_content_blocks(
    pdf_path: Path, pdf_bytes: bytes, page_count: int, user_prompt: str, max_chars: int,
) -> tuple[list, str]:
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

    text = _extract_pdf_text(pdf_path, max_chars=max_chars)
    return ([
        {
            "type": "text",
            "text": (
                f"Text-Extraktion aus PDF ({len(pdf_bytes) // 1024 // 1024} MB, {page_count} Seiten — "
                f"zu gross für Image-Modus). Seitenangaben aus '[Page N]'-Markern übernehmen.\n\n"
                f"{text}\n\n---\n\n{user_prompt}"
            ),
        },
    ], f"PDF-as-text ({page_count}p, ~{len(text)//1000}k chars)")


def _is_context_too_long(exc: Exception) -> bool:
    """Heuristik: war es ein 'prompt too long'-Fehler?"""
    msg = str(exc).lower()
    return "prompt is too long" in msg or "max_tokens" in msg and "exceed" in msg


def _call_claude_extraction(client, content_blocks: list, model: str) -> str:
    """Single Claude call with retry on transient errors. Returns raw text.

    1M-Context-Beta wird mitgeschickt — Sonnet 4.6 nutzt es, Haiku ignoriert es.
    """
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            response = claude_limiter.call(lambda: client.messages.create(
                model=model,
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
            # Bei "prompt too long" sofort raus — Retry mit gleichem Modell wuerde
            # nichts aendern. Caller soll auf größeres Modell eskalieren.
            if _is_context_too_long(e):
                raise
            logger.warning("Claude extraction attempt %d failed: %s", attempt + 1, e)
    raise last_exc or RuntimeError("Claude extraction failed without exception")


def _call_extraction_with_escalation(
    client, pdf_path: Path, pdf_bytes: bytes, page_count: int, user_prompt: str,
) -> str:
    """Versucht Haiku mit kleinem Char-Budget. Bei Context-Overflow → Sonnet
    mit grossem Budget (1M-Beta)."""
    blocks_haiku, mode_haiku = _build_pdf_content_blocks(
        pdf_path, pdf_bytes, page_count, user_prompt, max_chars=MAX_TEXT_CHARS_HAIKU,
    )
    logger.info("Extraction mode=%s model=haiku", mode_haiku)
    try:
        return _call_claude_extraction(client, blocks_haiku, EXTRACTION_MODEL_CHEAP)
    except anthropic.APIError as e:
        if not _is_context_too_long(e):
            raise
        logger.info("Haiku context too long → eskaliere auf Sonnet 1M-context")
    blocks_sonnet, mode_sonnet = _build_pdf_content_blocks(
        pdf_path, pdf_bytes, page_count, user_prompt, max_chars=MAX_TEXT_CHARS_SONNET,
    )
    logger.info("Extraction mode=%s model=sonnet (escalated)", mode_sonnet)
    return _call_claude_extraction(client, blocks_sonnet, EXTRACTION_MODEL_BIG)


def _parse_one_entry(key: str, entry: dict | None) -> dict:
    """Normalize a single key's entry from Claude's JSON output.
    PDF-Extraction ist ehrlich: wenn der Wert nicht im PDF steht, returnen wir
    None mit reason. Der Auto-Web-Fallback approximiert dann (= echte Zahl)
    statt dass PDF-Claude Zahlen aus der Luft erfindet."""
    if not isinstance(entry, dict):
        return {"value": None, "reason": "Key missing in response"}
    raw_val = entry.get("value")
    if raw_val is None:
        return {
            "value": None,
            "currency": entry.get("currency"),
            "page": entry.get("page"),
            "reason": entry.get("reason") or "Im Bericht nicht ausgewiesen",
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
    return f"""Beim ersten Versuch wurden die folgenden Werte für
{company_name} / {period_coverage} {period_year} mit value=null zurückgegeben.

Bitte gehe nochmal SYSTEMATISCH durch das gesamte PDF — nicht nur durch die
gleiche Sektion wie eben. Für JEDEN dieser Keys gilt:
- Cash Flow Statement geprüft? Falls nichts → weiter.
- Statement of Changes in Equity (Eigenkapital-Veränderungen) geprüft?
- ALLE Notes durchgehen (Inhaltsverzeichnis am Anfang nutzen).
- Highlights / Five-Year-Summary / Management Report?
- Vorgehen IFRS-spezifisch: typische DE/EU-Konzern-Berichte verstecken
  Items oft in Notes 'Personnel', 'Other liabilities', 'Equity'.

Liefere wieder das gleiche JSON-Format. Nur Werte die du JETZT findest.
Wenn weiterhin nichts da ist, im 'reason' KONKRET die geprüften Stellen
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
  "reason": null oder kurze Erklärung wenn value=null
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
             {key: {value, currency, page, quote, reason}, ...}  guidance_fy_results,
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
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    logger.info("PDF extraction pass=1 for %s/%s (%d pages)", company_name, period_year, page_count)

    raw_first = _call_extraction_with_escalation(client, pdf_path, pdf_bytes, page_count, user_prompt)

    parsed = _parse_json_from_response(raw_first)
    if parsed is None:
        logger.warning("PDF extraction pass=1 JSON parse failed for %s/%s. Raw: %s",
                       company_name, period_year, raw_first[:500])
        return ({}, {}, raw_first)

    results: dict[str, dict] = {key: _parse_one_entry(key, parsed.get(key)) for key, _ in EXTRACTION_KEYS}
    guidance_fy = _parse_guidance_block(parsed.get("guidance_fy"))

    # Identify keys we still don't have — but skip those Claude marked
    # explicitly as "Kein X in der Periode" (e.g. no buyback this year).
    missing_keys = [
        k for k, r in results.items()
        if r.get("value") is None and "kein" not in (r.get("reason") or "").lower()
    ]
    if not missing_keys:
        logger.info("PDF extraction %s/%s: all %d keys found in pass 1 (%d guidance entries)",
                    company_name, period_year, len(EXTRACTION_KEYS),
                    len([k for k, v in guidance_fy.items() if v.get("value") is not None]))
        return (results, guidance_fy, raw_first)

    logger.info("PDF extraction pass=2 retrying %d missing keys for %s/%s: %s",
                len(missing_keys), company_name, period_year, missing_keys)
    retry_prompt = _build_retry_prompt(missing_keys, period_coverage, period_year, company_name)
    try:
        raw_second = _call_extraction_with_escalation(client, pdf_path, pdf_bytes, page_count, retry_prompt)
    except Exception as e:
        logger.warning("PDF extraction pass=2 failed for %s/%s: %s", company_name, period_year, e)
        return (results, guidance_fy, raw_first)

    parsed_second = _parse_json_from_response(raw_second)
    if parsed_second is None:
        logger.warning("PDF extraction pass=2 JSON parse failed for %s/%s", company_name, period_year)
        return (results, guidance_fy, raw_first)

    recovered = 0
    for key in missing_keys:
        new_entry = _parse_one_entry(key, parsed_second.get(key))
        if new_entry.get("value") is not None:
            results[key] = new_entry
            recovered += 1
        elif new_entry.get("reason"):
            results[key] = {**results[key], "reason": new_entry["reason"]}
    logger.info("PDF extraction %s/%s pass=2 recovered %d/%d missing",
                company_name, period_year, recovered, len(missing_keys))
    return (results, guidance_fy, raw_first)


def _parse_guidance_block(raw: dict | None) -> dict[str, dict]:
    """Parse the optional 'guidance_fy' block from Claude's JSON.
    Returns dict {key: entry} for keys with non-null values, ignores empty/null entries.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for key, _desc in EXTRACTION_KEYS:
        entry = raw.get(key)
        parsed = _parse_one_entry(key, entry)
        if parsed.get("value") is not None:
            out[key] = parsed
    return out
