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
# Hard cap on text we send to Claude (rough: ~4 chars per token)
MAX_TEXT_CHARS = 600_000

logger = logging.getLogger(__name__)

# Keys we try to extract from every uploaded IR document.
# (subset of the catalog — only Primärwerte, no calc-keys)
EXTRACTION_KEYS: list[tuple[str, str]] = [
    ("net_income", "Net Income (Konzern-Nettogewinn nach Steuern)"),
    ("fcf", "Free Cash Flow (Operating Cash Flow − CapEx)"),
    ("sbc", "Stock Based Compensation Expense (anteilsbasierte Vergütung)"),
    ("buyback_volume", "Aktienrückkäufe / Repurchases of common stock (Cash-Outflow)"),
    ("dividends", "Dividenden (Cash-Outflow für Dividends Paid)"),
    ("cash_and_equivalents", "Cash and Cash Equivalents (Bilanz, Stichtag FY-Ende)"),
    ("marketable_securities_st", "Short-term Marketable Securities / Short-term Investments"),
    ("marketable_securities_lt", "Long-term Marketable Securities / Long-term Investments"),
    ("lease_liabilities", "Lease Liabilities (Operating + Finance, Bilanz)"),
    ("long_term_debt", "Long-term Debt (langfristige Finanzschulden, Bilanz)"),
    ("shares_outstanding", "Shares Outstanding (zum FY-Ende oder Diluted Weighted Average)"),
]


SYSTEM_PROMPT = """Du bist ein Finanzanalyst mit Spezialisierung auf das Lesen
von Geschäftsberichten / 10-K / 20-F / Quartalsberichten.

Deine Aufgabe: Extrahiere präzise Finanzkennzahlen aus dem hochgeladenen PDF
für einen spezifischen Berichts-Zeitraum.

WICHTIGE REGELN:
1. Antworte AUSSCHLIESSLICH als gültiges JSON. Kein Markdown, keine Erklärungen drumherum.
2. Werte als Zahl in Base-Units (z.B. 1450000000 für 1,45 Mrd, NICHT '1.45B').
3. Wenn EUR oder USD, gib die Währung explizit an. Sonst null.
4. Pro Wert IMMER die Seitenzahl angeben aus dem PDF wo du den Wert gefunden hast.
5. Wenn ein Wert nicht eindeutig im PDF steht: value=null, reason kurz erklären.
6. KEINE Schätzungen / Approximationen. Nur was wirklich im Dokument steht.
7. Bei Quartalsberichten: prüfe ob der Wert YTD (year-to-date kumuliert) ODER
   nur das einzelne Quartal ist und dokumentiere das im 'period_basis'-Feld.
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


def _extract_pdf_text(pdf_path: Path) -> str:
    """Extract text from all pages of a PDF, prefixed with [Page N] markers
    so Claude can cite page numbers. Truncated to MAX_TEXT_CHARS."""
    reader = PdfReader(str(pdf_path))
    parts: list[str] = []
    total = 0
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        chunk = f"\n\n[Page {i}]\n{text}"
        total += len(chunk)
        if total > MAX_TEXT_CHARS:
            chunk = chunk[: MAX_TEXT_CHARS - (total - len(chunk))]
            parts.append(chunk)
            parts.append(f"\n\n[... PDF truncated at page {i}, {len(reader.pages)} pages total ...]")
            break
        parts.append(chunk)
    return "".join(parts)


def extract_values_from_pdf(
    pdf_path: Path,
    *,
    company_name: str,
    document_type: str,
    period_coverage: str,
    period_year: int,
) -> tuple[dict, str | None]:
    """Run Claude extraction on the PDF. Returns (results_per_key, raw_response).

    Sends the full PDF as document if it's small (<5 MB) so Claude can read tables
    natively as images. For larger PDFs (Annual Reports >> 100 pages), falls back
    to extracted-text mode to stay under the 1M token limit.

    results_per_key shape:
      {"net_income": {"value": Decimal, "currency": "USD", "page": 31, ...}, ...}
    Failed keys carry value=None.
    """
    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")

    pdf_bytes = pdf_path.read_bytes()
    user_prompt = _build_user_prompt(period_coverage, period_year, document_type, company_name)

    # Count pages cheaply once so we can decide which mode to use.
    try:
        page_count = len(PdfReader(str(pdf_path)).pages)
    except Exception as e:
        logger.warning("Could not count pages for %s, assuming >limit: %s", pdf_path, e)
        page_count = PDF_IMAGE_PAGES_LIMIT + 1

    use_image_mode = (
        len(pdf_bytes) <= PDF_IMAGE_BYTES_LIMIT
        and page_count <= PDF_IMAGE_PAGES_LIMIT
    )

    if use_image_mode:
        # Native PDF — Claude reads visual layout including tables as images
        pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
        content_blocks = [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": pdf_b64,
                },
            },
            {"type": "text", "text": user_prompt},
        ]
        mode_note = f"PDF-as-image ({page_count}p)"
    else:
        # Text-extraction fallback for large PDFs / >100 pages (saves tokens
        # and bypasses Anthropic's PDF-as-image 100-page limit).
        text = _extract_pdf_text(pdf_path)
        content_blocks = [
            {
                "type": "text",
                "text": (
                    f"Text-Extraktion aus PDF ({len(pdf_bytes) // 1024 // 1024} MB, {page_count} Seiten — "
                    f"zu gross fuer Image-Modus). Seitenangaben aus '[Page N]'-Markern uebernehmen.\n\n"
                    f"{text}\n\n---\n\n{user_prompt}"
                ),
            },
        ]
        mode_note = f"PDF-as-text ({page_count}p)"

    logger.info("PDF extraction mode=%s for %s/%s", mode_note, company_name, period_year)
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    # Haiku 4.5 is ~4x cheaper than Sonnet for input tokens and is well-suited
    # for structured extraction tasks like reading financial tables.
    response = claude_limiter.call(lambda: client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content_blocks}],
    ))

    text_parts = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", "") or "")
    raw = "\n".join(text_parts)

    parsed = _parse_json_from_response(raw)
    if parsed is None:
        logger.warning("PDF extraction: could not parse JSON from Claude response. Raw: %s", raw[:500])
        return ({}, raw)

    results: dict[str, dict] = {}
    for key, _desc in EXTRACTION_KEYS:
        entry = parsed.get(key)
        if not isinstance(entry, dict):
            results[key] = {"value": None, "reason": "Key missing in response"}
            continue
        raw_val = entry.get("value")
        if raw_val is None:
            results[key] = {
                "value": None,
                "currency": entry.get("currency"),
                "page": entry.get("page"),
                "reason": entry.get("reason") or "Not extracted",
            }
            continue
        try:
            decimal_val = Decimal(str(raw_val))
        except (InvalidOperation, ValueError):
            results[key] = {
                "value": None,
                "currency": entry.get("currency"),
                "page": entry.get("page"),
                "reason": f"Could not parse value: {raw_val!r}",
            }
            continue
        results[key] = {
            "value": decimal_val,
            "currency": entry.get("currency"),
            "page": entry.get("page"),
            "quote": entry.get("quote"),
            "period_basis": entry.get("period_basis"),
        }
    return (results, raw)
