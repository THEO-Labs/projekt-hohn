"""Gemini-basierte Web-Recherche fuer Equity-Kennzahlen — second opinion zu Claude.

Spiegelt research_value() aus claude.py mit equivalentem System-Prompt, gleichem
WERT/QUELLE/URL-Format und gleichen Validators (validate_claude_value etc.).
Wird parallel zu Claude in app/llm/research.py aufgerufen, der Aggregator
mittelt die Werte und persistiert beide Einzel-Ergebnisse als
forecast_alternates.
"""
import logging
import re
from decimal import Decimal

from app.config import settings
from app.llm.claude import (
    RESEARCH_PROMPT,
    _DATE_PATTERN,
    _YOY_CAP_KEYS,
    _has_specific_url,
    _is_forward_year,
    detect_calculation_inconsistency,
    extract_research_value,
    validate_claude_value,
)
from app.llm.claude import KEY_RESEARCH_HINTS, FORWARD_YEAR_HINT

logger = logging.getLogger(__name__)

_gemini_client = None


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY not configured")
        try:
            from google import genai
        except ImportError as e:
            raise RuntimeError(f"google-genai not installed: {e}") from e
        _gemini_client = genai.Client(api_key=settings.gemini_api_key)
    return _gemini_client


def research_value_gemini(
    company_name: str,
    ticker: str,
    value_label: str,
    currency: str,
    period_type: str = "FY",
    period_year: int | None = None,
    value_key: str | None = None,
    prev_fy_val: Decimal | None = None,
) -> tuple[Decimal | None, str | None, str | None, str | None, str | None]:
    """Gemini-Aequivalent zu claude.research_value(). Gleiche Signatur,
    gleiches Output-Format. Returns (value, source, url, user_prompt, response_text)."""
    if not settings.gemini_api_key:
        return None, None, None, None, None

    is_forward = _is_forward_year(period_year)
    is_quarter = period_type in ("Q1", "Q2", "Q3", "Q4")
    if period_type == "FY" and period_year:
        marker = "e" if is_forward else ""
        period_str = f"Geschäftsjahr {period_year}{marker} (FY{period_year}{marker})"
    elif is_quarter and period_year:
        period_str = f"{period_type} {period_year} (Quartal — kumulativ year-to-date wenn möglich, sonst standalone)"
    else:
        period_str = "aktueller/letzter verfügbarer Wert"

    hint = KEY_RESEARCH_HINTS.get(value_key or "", "")
    hint_block = f"\n\nKontext zur Datenquelle:\n{hint}" if hint else ""
    forward_block = ""
    if is_forward and period_year:
        forward_block = "\n\n" + FORWARD_YEAR_HINT.replace("{YEAR}", str(period_year))

    anchor_block = ""
    if is_forward and prev_fy_val is not None and value_key in _YOY_CAP_KEYS:
        max_growth, max_shrink = _YOY_CAP_KEYS[value_key]
        try:
            prev_f = float(prev_fy_val)
            anchor_block = (
                f"\n\nFY{period_year - 1}-ANKER: Der zuletzt bekannte FY-Wert fuer "
                f"{value_label} ist {prev_f:,.0f} {currency}. Deine FY{period_year}e-"
                f"Schaetzung MUSS in einem plausiblen YoY-Korridor liegen: "
                f"{prev_f * max_shrink:,.0f} bis {prev_f * max_growth:,.0f} {currency} "
                f"(= {(max_shrink-1)*100:+.0f}% bis {(max_growth-1)*100:+.0f}% YoY). "
                f"Werte ausserhalb werden serverseitig REJECTED — pruefe Konsens und "
                f"Quelle nochmal. Wenn deine Quelle einen Sprung >+50% andeutet, "
                f"zitiere die Management-Guidance/Earnings-Call wortwoertlich in "
                f"BEGRUENDUNG."
            )
        except (ValueError, TypeError):
            pass

    user_prompt = (
        f"{RESEARCH_PROMPT}\n\n---\n\n"
        f"Unternehmen: {company_name} ({ticker}, {currency})\n"
        f"Gesuchte Kennzahl: {value_label}\n"
        f"Zeitraum: {period_str}\n\n"
        f"WAEHRUNG-PFLICHT: Liefere den Wert in {currency} (Original-Reporting-"
        f"Währung der Firma). KEINE Umrechnung in USD/EUR/anderes Currency. "
        f"Wenn die Quelle in einer anderen Währung berichtet, rechne KORREKT um "
        f"in {currency} und nenne den verwendeten Kurs in BEGRUENDUNG.\n\n"
        f"Nutze Google Search um IR-Seite, Annual-Report-PDFs und Aggregatoren "
        f"(stockanalysis, macrotrends, wsj, wisesheets) aktiv zu durchsuchen. "
        f"Verlasse dich NICHT nur auf dein Gedächtnis."
        f"{forward_block}"
        f"{hint_block}"
        f"{anchor_block}"
    )

    def _do_call(prompt: str) -> str:
        import time
        try:
            from google.genai import types
        except ImportError:
            return ""
        client = _get_gemini_client()
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                response = client.models.generate_content(
                    model=settings.gemini_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        max_output_tokens=2048,
                    ),
                )
                # Gemini response.text aggregiert alle text-parts.
                return response.text or ""
            except Exception as e:
                msg = str(e).lower()
                is_retriable = any(
                    s in msg for s in ("429", "503", "overloaded", "rate", "timeout", "deadline")
                )
                if not is_retriable or attempt == max_attempts - 1:
                    logger.warning("Gemini research attempt %d failed (non-retriable or final): %s",
                                   attempt + 1, str(e)[:120])
                    if attempt == max_attempts - 1:
                        return ""
                    raise
                wait = 2 ** attempt
                logger.warning("Gemini research attempt %d failed (%s) — retry in %ds",
                               attempt + 1, str(e)[:80], wait)
                time.sleep(wait)
        return ""

    try:
        content = _do_call(user_prompt)
        if not content:
            return None, None, None, user_prompt, None
        value = extract_research_value(content)
        sanity_failed_reason: str | None = None

        if value is not None and value_key:
            validated = validate_claude_value(
                value_key, value, prev_fy_val=prev_fy_val, is_forward_year=is_forward,
            )
            if validated is None:
                sanity_failed_reason = (
                    f"Gemini-Wert {value} wurde verworfen (Sanity/Unit/YoY-Cap)"
                )
                value = None
            else:
                inc = detect_calculation_inconsistency(value, content)
                if inc is not None:
                    logger.warning("Gemini research %s/%s: %s — retry",
                                   ticker, value_key or "?", inc)
                    sanity_failed_reason = inc
                    value = None

        if value is None:
            # Eine Retry-Runde mit explizitem Hinweis (analog zu Claude).
            logger.info("Gemini research %s/%s: erster Versuch lieferte keine valide Zahl — retry (%s)",
                        ticker, value_key or "?", sanity_failed_reason or "extract=None")
            sanity_hint = ""
            if sanity_failed_reason:
                sanity_hint = (
                    f"\n\nVorsicht: dein letzter Wert wurde verworfen. Grund: "
                    f"{sanity_failed_reason}. Korrigiere das beim Retry."
                )
            retry_prompt = (
                f"{user_prompt}\n\n---\n\n"
                f"Vorheriger Versuch lieferte keinen validen Wert. Gib JETZT als "
                f"Senior Equity Analyst eine plausible Zahl in {currency} BASE-UNITS. "
                f"QUELLE = 'KI-Einschätzung: <Begruendung>' wenn keine externe Quelle. "
                f"KONFIDENZ: niedrig.{sanity_hint}"
            )
            content_retry = _do_call(retry_prompt)
            if content_retry:
                value = extract_research_value(content_retry)
                if value is not None and value_key:
                    validated = validate_claude_value(
                        value_key, value, prev_fy_val=prev_fy_val, is_forward_year=is_forward,
                    )
                    if validated is None:
                        value = None
                if value is not None:
                    content = content_retry

        if value is None:
            return None, None, None, user_prompt, content
        source_match = re.search(r"QUELLE:\s*(.+)", content)
        source = source_match.group(1).strip() if source_match else "Gemini-Recherche"
        url_match = re.search(r"QUELLE_URL:\s*(https?://\S+)", content)
        source_url = url_match.group(1).strip() if url_match else None
        if _DATE_PATTERN.search(source) and not _has_specific_url(source_url):
            source = f"⚠ unverifizierte Datumsangabe ohne spezifische URL — {source}"
        return value, source, source_url, user_prompt, content
    except Exception as e:
        logger.warning("Gemini research failed for %s/%s: %s", ticker, value_label, e)
        return None, None, None, user_prompt, None
