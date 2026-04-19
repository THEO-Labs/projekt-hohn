import re
import logging
from decimal import Decimal, InvalidOperation

import anthropic

from app.config import settings
from app.llm.rate_limiter import claude_limiter

logger = logging.getLogger(__name__)

QUALITATIVE_SYSTEM_PROMPT = """Du bist ein erfahrener Finanzanalyst bei einem Investmentunternehmen.

Deine Aufgabe: Bewerte qualitative Faktoren auf einer Skala von 0.5 bis 1.5.
0.5 = sehr hohes Risiko / sehr schlecht, 1.0 = neutral, 1.5 = sehr gut

Antworte immer mit:
SCORE: [Zahl zwischen 0.5 und 1.5]
BEGRÜNDUNG: [Deine Begründung]
FAKTOREN: [Entscheidende Faktoren]
QUELLEN: [Verwendete Quellen]

Sei präzise. Antworte auf Deutsch, Fachbegriffe auf Englisch.
Wenn du unsicher bist, sei ehrlich und gib SCORE: 1.0 mit entsprechendem Hinweis."""

RESEARCH_SYSTEM_PROMPT = """Du bist ein erfahrener Finanzanalyst bei einem Investmentunternehmen.

Deine Aufgabe: Recherchiere konkrete Finanzkennzahlen für Unternehmen.
Antworte NUR mit numerischen Werten in diesem Format:
WERT: [Zahl]
EINHEIT: [z.B. USD, EUR, %, keine]
QUELLE: [Woher der Wert stammt]
QUELLE_URL: [URL zur Quelle]
ZEITRAUM: [z.B. FY2024, TTM, aktuell]
KONFIDENZ: [hoch/mittel/niedrig]

Wenn du keinen verifizierbaren Wert findest: WERT: NICHT_GEFUNDEN

Nutze echte Quellen (Geschäftsberichte, Analystenkonsens, Finanzdatenbanken).
Sei präzise. Antworte auf Deutsch, Fachbegriffe auf Englisch."""


def get_client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def extract_score(text: str) -> Decimal | None:
    match = re.search(r"SCORE:\s*(\d+[.,]\d+)", text, re.IGNORECASE)
    if not match:
        return None
    try:
        score = Decimal(match.group(1).replace(",", "."))
        if Decimal("0.5") <= score <= Decimal("1.5"):
            return score
        return None
    except InvalidOperation:
        return None


def _parse_numeric_string(raw: str) -> Decimal | None:
    """Parse a numeric string handling both German (1.234,56) and US (1,234.56) formats,
    as well as plain integers, negatives, and percent signs."""
    raw = raw.strip().rstrip(".").rstrip("%").strip()
    if not raw:
        return None

    # Detect and extract suffix multiplier
    multiplier = Decimal("1")
    for suffix, mult in [
        ("mrd", "1000000000"),
        ("billion", "1000000000"),
        ("mio", "1000000"),
        ("million", "1000000"),
    ]:
        if suffix in raw.lower():
            multiplier = Decimal(mult)
            raw = re.sub(r"\s*(?:Mrd|Milliarden|Mio|Millionen|billion|million)\.?", "", raw, flags=re.IGNORECASE).strip()
            break
    else:
        for suffix, mult in [("B", "1000000000"), ("T", "1000000000000"), ("M", "1000000"), ("K", "1000")]:
            # Match suffix at end of string (possibly after optional whitespace), case-insensitive.
            # The suffix must not be followed by another letter to avoid false matches.
            if re.search(r"\s*" + re.escape(suffix) + r"\s*$", raw, re.IGNORECASE):
                multiplier = Decimal(mult)
                raw = re.sub(r"\s*" + re.escape(suffix) + r"\s*$", "", raw, flags=re.IGNORECASE).strip()
                break

    raw = raw.strip().rstrip(".")

    # Determine format: German (1.234.567,89 or 1.234,56) vs US (1,234,567.89 or 1,234.56)
    has_dot = "." in raw
    has_comma = "," in raw

    if has_dot and has_comma:
        # Determine which is thousands separator vs decimal separator
        last_dot = raw.rfind(".")
        last_comma = raw.rfind(",")
        if last_comma > last_dot:
            # German: 1.234,56 - dot=thousands, comma=decimal
            raw = raw.replace(".", "").replace(",", ".")
        else:
            # US: 1,234.56 - comma=thousands, dot=decimal
            raw = raw.replace(",", "")
    elif has_comma and not has_dot:
        # Could be German decimal (14,77) or German thousands with no decimal
        # If comma is followed by exactly 3 digits at end and no other commas → thousands sep
        if re.match(r"^[+-]?\d{1,3}(,\d{3})+$", raw):
            raw = raw.replace(",", "")
        else:
            raw = raw.replace(",", ".")
    elif has_dot and not has_comma:
        # Could be US decimal (14.77) or German thousands (1.234.567)
        # If multiple dots → German thousands sep
        if raw.count(".") > 1:
            raw = raw.replace(".", "")
        # Single dot: treat as decimal point (standard)

    try:
        return Decimal(raw) * multiplier
    except (InvalidOperation, ValueError):
        return None


def extract_value(text: str) -> Decimal | None:
    """Extract WERT: value from Claude chat responses. Falls back to SCORE: if no WERT: found."""
    match = re.search(
        r"WERT:\s*([+-]?[\d.,]+(?:\s*(?:Mrd|Milliarden|Mio|Millionen|Billion|billion|million|[BMTK])\.?)?(?:\s*%)?)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return extract_score(text)
    raw = match.group(1).strip()
    return _parse_numeric_string(raw)


RESEARCH_PROMPT = """Du bist ein Finanzanalyst. Dir wird eine Finanzkennzahl für ein Unternehmen gefragt,
die nicht über die API verfügbar war. Recherchiere den Wert basierend auf deinem Wissen.

Antworte NUR in diesem Format:
WERT: [Zahl]
EINHEIT: [z.B. USD, EUR, %, keine]
QUELLE: [Kurze Beschreibung der Quelle]
QUELLE_URL: [Direkte URL zur Quelle, z.B. https://www.allianz.com/en/investor_relations/ oder https://finance.yahoo.com/quote/ALV.DE/ oder https://www.wsj.com/... ]
ZEITRAUM: [z.B. FY2024, TTM, aktuell]
KONFIDENZ: [hoch/mittel/niedrig]

Wenn du den Wert nicht findest, antworte mit:
WERT: NICHT_GEFUNDEN

Wichtig:
- Gib nur verifizierbare Zahlen an. Im Zweifel NICHT_GEFUNDEN.
- Die QUELLE_URL muss eine echte, existierende URL sein (Investor Relations Seite, Yahoo Finance, Bloomberg, Reuters, etc.)
- Keine erfundenen URLs.
- ZAHLENFORMAT: Gib Zahlen immer in vollen Zahlen in Base-Units an, NICHT in Millionen/Milliarden-Notation.
  Beispiel: WERT: 139947000000 (statt WERT: 139,9 Mrd)
  Beispiel: WERT: 10775000000 (statt WERT: 10,78 Mrd)
  Beispiel: WERT: 4.38 (für Prozente, direkt als Prozentwert, nicht als Dezimal)
- Verwende Punkt als Dezimalzeichen (z.B. 27.65), kein Komma."""


def extract_research_value(text: str) -> Decimal | None:
    """Extract WERT: from Claude research responses.
    Handles: plain integers, German/US number formats, suffixes (Mrd/B/Mio/M),
    negative values, percent values, and NICHT_GEFUNDEN sentinel."""
    match = re.search(
        r"WERT:\s*([+-]?[\d.,]+(?:\s*(?:Mrd|Milliarden|Mio|Millionen|billion|million|[BMTK])\.?)?(?:\s*%)?|NICHT[_\s]?GEFUNDEN)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    raw = match.group(1).strip()
    if re.match(r"nicht.{0,2}gefunden", raw, re.IGNORECASE):
        return None
    return _parse_numeric_string(raw)


# Sanity ranges imported from yahoo provider to avoid duplication.
# Maps value_key → (min, max). Values outside are rejected before storing.
_CLAUDE_SANITY_CHECKS: dict[str, tuple[float, float]] = {
    "stock_price": (0, 1_000_000),
    "market_cap": (0, 15_000_000_000_000),
    "shares_outstanding": (0, 1_000_000_000_000),
    "dividends": (0, 10_000),
    "dividend_return": (0, 100),       # Claude returns percent directly (e.g. 4.38)
    "analysts_target": (0, 1_000_000),
    "eps_ttm": (-100_000, 100_000),
    "eps_forward": (-100_000, 100_000),
    "pe_ttm": (0, 10_000),
    "pe_forward": (0, 10_000),
    "ev": (0, 15_000_000_000_000),
    "ebitda": (-1_000_000_000_000, 5_000_000_000_000),
    "ev_ebitda": (-1_000, 1_000),
    "peg": (-100, 1_000),
    "free_cash_flow": (-5_000_000_000_000, 5_000_000_000_000),
    "op_cash_flow": (-5_000_000_000_000, 5_000_000_000_000),
    "cash": (0, 5_000_000_000_000),
    "debt": (0, 10_000_000_000_000),
    "sales": (0, 5_000_000_000_000),
    "op_margin": (-100, 100),
    "net_profit": (-5_000_000_000_000, 5_000_000_000_000),
    "sales_growth": (-100, 500),
    "op_profit": (-5_000_000_000_000, 5_000_000_000_000),
    "buybacks": (0, 1_000_000_000_000),
}


def validate_claude_value(key: str, value: Decimal) -> Decimal | None:
    """Check that a Claude-returned value is within the expected range for the given key.
    Returns the value unchanged if OK, or None if it fails the sanity check."""
    limits = _CLAUDE_SANITY_CHECKS.get(key)
    if limits is None:
        return value
    lo, hi = limits
    try:
        fval = float(value)
    except (ValueError, OverflowError):
        logger.warning("Claude value sanity: cannot convert value for key=%s, dropping", key)
        return None
    if fval < lo or fval > hi:
        logger.warning(
            "Claude value sanity failed for key=%s: value=%s out of range [%s, %s], dropping",
            key, value, lo, hi,
        )
        return None
    return value


def research_value(company_name: str, ticker: str, value_label: str, currency: str, period_type: str = "SNAPSHOT", period_year: int | None = None) -> tuple[Decimal | None, str | None, str | None, str | None, str | None]:
    """Returns (value, source_name, source_url, user_prompt, assistant_response)."""
    if period_type == "FY" and period_year:
        period_str = f"Geschäftsjahr {period_year} (FY{period_year})"
    elif period_type == "LTM":
        period_str = "letzte 12 Monate (LTM)"
    elif period_type == "TTM":
        period_str = "trailing twelve months (TTM)"
    else:
        period_str = "aktueller/letzter verfügbarer Wert"

    user_prompt = f"Unternehmen: {company_name} ({ticker}, {currency})\nGesuchte Kennzahl: {value_label}\nZeitraum: {period_str}\n\nWichtig: Liefere NUR den Wert für den angegebenen Zeitraum. Wenn du für {period_str} keinen verifizierbaren Wert findest, antworte mit WERT: NICHT_GEFUNDEN."

    try:
        client = get_client()
        response = claude_limiter.call(lambda: client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=[{"type": "text", "text": RESEARCH_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_prompt}],
        ))
        content = response.content[0].text
        value = extract_research_value(content)
        if value is None:
            return None, None, None, user_prompt, content
        source_match = re.search(r"QUELLE:\s*(.+)", content)
        source = source_match.group(1).strip() if source_match else "Claude-Recherche"
        url_match = re.search(r"QUELLE_URL:\s*(https?://\S+)", content)
        source_url = url_match.group(1).strip() if url_match else None
        return value, f"Claude-Recherche: {source}", source_url, user_prompt, content
    except Exception as e:
        logger.warning("Claude research failed for %s/%s: %s", ticker, value_label, e)
        return None, None, None, user_prompt, None


_RESEARCH_USER_RE = re.compile(
    r"Unternehmen:\s*(.+?)\n.*?Gesuchte Kennzahl:\s*(.+?)(?:\n|$)",
    re.DOTALL,
)


def _rewrite_research_message(content: str) -> str:
    m = _RESEARCH_USER_RE.search(content)
    if m:
        company = m.group(1).strip()
        label = m.group(2).strip()
        return f"Frage: Welchen Wert hat {label} fuer {company}?"
    return content


def call_claude(messages: list[dict[str, str]], company_context: str, mode: str = "qualitative") -> tuple[str, Decimal | None]:
    client = get_client()

    system_prompt = QUALITATIVE_SYSTEM_PROMPT if mode == "qualitative" else RESEARCH_SYSTEM_PROMPT

    user_messages = []
    for msg in messages:
        content = msg["content"]
        if msg["role"] == "user" and "Unternehmen:" in content and "Gesuchte Kennzahl:" in content:
            content = _rewrite_research_message(content)
        user_messages.append({"role": msg["role"], "content": content})

    response = claude_limiter.call(lambda: client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": company_context,
            },
        ],
        messages=user_messages,
    ))

    content = response.content[0].text
    if mode == "qualitative":
        score = extract_score(content)
    else:
        score = extract_value(content)
    return content, score
