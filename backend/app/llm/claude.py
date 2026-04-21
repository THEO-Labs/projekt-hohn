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
WERT: [volle Zahl in Base-Units, KEINE Mio/Mrd Notation]
EINHEIT: [NUR Waehrung (USD/EUR/...) oder % oder 'keine' — NIEMALS 'Mio' oder 'Mrd']
QUELLE: [Woher der Wert stammt]
QUELLE_URL: [URL zur Quelle]
ZEITRAUM: [z.B. FY2024, TTM, aktuell]
KONFIDENZ: [hoch/mittel/niedrig]

Wenn du keinen verifizierbaren Wert findest: WERT: NICHT_GEFUNDEN

ZAHLENFORMAT-Beispiele:
- 1,45 Mrd USD  →  WERT: 1450000000   EINHEIT: USD
- 1.450 Mio USD →  WERT: 1450000000   EINHEIT: USD
- 139,9 Mrd EUR →  WERT: 139900000000 EINHEIT: EUR
- 4,38 %        →  WERT: 4.38         EINHEIT: %

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


_UNIT_SCALE_PATTERNS = [
    (re.compile(r"\b(billion|mrd|milliarde|mia)\b", re.IGNORECASE), Decimal("1000000000")),
    (re.compile(r"\b(million|mio|mill)\b", re.IGNORECASE), Decimal("1000000")),
    (re.compile(r"\b(thousand|tsd|tausend)\b", re.IGNORECASE), Decimal("1000")),
]


def _apply_unit_scale(value: Decimal, text: str, wert_raw: str) -> Decimal:
    """When WERT has no scale suffix but EINHEIT contains 'Mio' / 'Mrd' / etc.,
    multiply the value accordingly. Prevents Claude's 'WERT: 1450 / EINHEIT: USD Mio.'
    from landing as 1450 instead of 1_450_000_000."""
    if re.search(r"(mrd|milliarde|mia|mio|million|billion|thousand|tsd|tausend|[bmtk])\b", wert_raw, re.IGNORECASE):
        return value
    einheit_match = re.search(r"EINHEIT:\s*([^\n]+)", text, re.IGNORECASE)
    if not einheit_match:
        return value
    einheit = einheit_match.group(1)
    for pattern, multiplier in _UNIT_SCALE_PATTERNS:
        if pattern.search(einheit):
            return value * multiplier
    return value


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
    value = _parse_numeric_string(raw)
    if value is None:
        return None
    return _apply_unit_scale(value, text, raw)


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
- ZAHLENFORMAT: WERT MUSS die volle Zahl in Base-Units sein, ohne 'Mio', 'Mrd',
  'Million', 'Billion' etc. als Suffix oder in EINHEIT.
  RICHTIG:   WERT: 1450000000   EINHEIT: USD
  FALSCH:    WERT: 1450          EINHEIT: USD Mio.
  RICHTIG:   WERT: 139947000000  EINHEIT: EUR
  FALSCH:    WERT: 139.9 Mrd     EINHEIT: EUR
  Prozente direkt als Prozentwert: WERT: 4.38  EINHEIT: %
- EINHEIT enthaelt NUR die Waehrung / 'keine' / '%', niemals einen
  Skalierungs-Hinweis wie 'Mio' oder 'Mrd'.
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
    value = _parse_numeric_string(raw)
    if value is None:
        return None
    return _apply_unit_scale(value, text, raw)


_CLAUDE_SANITY_CHECKS: dict[str, tuple[float, float]] = {
    "stock_price": (0, 1_000_000),
    "market_cap": (0, 15_000_000_000_000),
    "shares_outstanding": (0, 1_000_000_000_000),
    "debt": (0, 1_000_000_000_000),
    "cash": (0, 1_000_000_000_000),
    "exchange_rate": (0, 1_000_000),
    "sbc": (0, 500_000_000_000),
    "net_income": (-5_000_000_000_000, 5_000_000_000_000),
    "eps": (-10_000, 100_000),
    "eps_adj": (-10_000, 100_000),
    "op_cash_flow": (-5_000_000_000_000, 5_000_000_000_000),
    "capex": (0, 5_000_000_000_000),
    "dividends": (0, 1_000_000_000_000),
}


KEY_RESEARCH_HINTS: dict[str, str] = {
    "sbc": (
        "Stock Based Compensation (SBC): jaehrlicher Betrag aus dem 10-K "
        "(oder 20-F bei Non-US-Filern) unter 'Share-based compensation "
        "expense' bzw. im Cash Flow Statement als 'Stock-based compensation'. "
        "NUR aus dem Jahres-Geschaeftsbericht des gefragten Jahres."
    ),
    "op_cash_flow": (
        "Operating Cash Flow: aus dem Jahres-Cashflow-Statement ('Net cash "
        "provided by operating activities'). Fuer das Ziel-Jahr der aktuellen "
        "Analyse sind auch Guidance-Zahlen aus Earnings-Call / IR erlaubt — "
        "dann QUELLE explizit als 'Guidance FY XXXX' ausweisen."
    ),
    "capex": (
        "Capital Expenditures: Zeile 'Purchase of property and equipment' / "
        "'Capital expenditures' im Cashflow-Statement. Immer als POSITIVER "
        "absoluter Betrag melden. Fuer das Ziel-Jahr auch Guidance aus IR."
    ),
    "eps_adj": (
        "Adjusted / non-GAAP EPS: aus dem Earnings-Release, NICHT der GAAP-"
        "Wert. Fuer Forward-Jahre ist Analyst-Konsens oder IR-Guidance "
        "akzeptabel — QUELLE entsprechend ausweisen."
    ),
    "eps": (
        "GAAP Diluted EPS aus dem 10-K / 20-F fuer das exakte Geschaeftsjahr. "
        "Keine TTM-Werte."
    ),
    "net_income": (
        "Net Income (Nettogewinn) aus dem 10-K / 20-F fuer das exakte "
        "Geschaeftsjahr. Keine TTM-Werte, keine bereinigten 'adjusted' Zahlen."
    ),
    "dividends": (
        "Cash Dividends Paid (absoluter Betrag) aus dem Cashflow-Statement. "
        "Fuer Forward-Jahre: erwartete Ausschuettung aus der Dividend Policy "
        "des Unternehmens."
    ),
    "debt": (
        "Total Debt aus dem Balance Sheet zum letzten verfuegbaren Stichtag."
    ),
    "cash": (
        "Cash and Cash Equivalents (inklusive Short-Term Investments wenn "
        "ueblich ausgewiesen) aus dem Balance Sheet."
    ),
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


def research_value(
    company_name: str,
    ticker: str,
    value_label: str,
    currency: str,
    period_type: str = "FY",
    period_year: int | None = None,
    value_key: str | None = None,
) -> tuple[Decimal | None, str | None, str | None, str | None, str | None]:
    """Returns (value, source_name, source_url, user_prompt, assistant_response)."""
    if period_type == "FY" and period_year:
        period_str = f"Geschaeftsjahr {period_year} (FY{period_year})"
    else:
        period_str = "aktueller/letzter verfügbarer Wert"

    hint = KEY_RESEARCH_HINTS.get(value_key or "", "")
    hint_block = f"\n\nKontext zur Datenquelle:\n{hint}" if hint else ""

    user_prompt = (
        f"Unternehmen: {company_name} ({ticker}, {currency})\n"
        f"Gesuchte Kennzahl: {value_label}\n"
        f"Zeitraum: {period_str}\n\n"
        f"Wichtig: Liefere AUSSCHLIESSLICH den Wert fuer {period_str}. "
        f"Keine TTM/LTM/Trailing-Werte, keine Forward-Guidance wenn ein "
        f"historisches Jahr gefragt ist, keine Schaetzungen aus "
        f"Quartalsberichten. Wenn du fuer {period_str} keinen "
        f"verifizierbaren Wert aus dem Jahresabschluss findest, antworte "
        f"mit WERT: NICHT_GEFUNDEN."
        f"{hint_block}"
    )

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
