import re
import logging
from datetime import date
from decimal import Decimal, InvalidOperation

import anthropic

from app.config import settings
from app.llm.rate_limiter import claude_limiter

logger = logging.getLogger(__name__)


def _is_forward_year(period_year: int | None) -> bool:
    """True if no 10-K has been filed yet for the given FY.
    Uses `>=` because the FY XXXX report typically lands in Feb XXXX+1, so
    during most of calendar year XXXX the report is still pending and the
    value must come from IR Guidance / analyst consensus."""
    if period_year is None:
        return False
    return period_year >= date.today().year


FORWARD_YEAR_HINT = (
    "DIESES JAHR LIEGT IN DER ZUKUNFT: Das Unternehmen hat dafuer NOCH KEINEN "
    "10-K / 20-F veroeffentlicht. Liefere trotzdem den BESTEN verfuegbaren "
    "Zahlenwert — kein NICHT_GEFUNDEN solange es eine brauchbare "
    "Approximation gibt.\n\n"
    "Suche in dieser Reihenfolge:\n"
    "1. IR-Guidance aus dem letzten Q4/Q1-Earnings-Call Transcript oder "
    "Press Release (Management-Outlook).\n"
    "2. Investor Presentations / Guidance-Folien (z.B. 'FY{YEAR} Outlook').\n"
    "3. Analysten-Konsens (Yahoo Finance Analyst Estimates, Factset, "
    "Refinitiv, Seeking Alpha Consensus).\n"
    "4. Fallback: letzter verfuegbarer Istwert aus dem juengsten Quartals-"
    "oder Jahresbericht (10-Q / 10-K).\n\n"
    "Kategorisierung:\n"
    "- Gut prognostizierbar (echte Guidance): FCF, Net Income, Sales, "
    "SBC, Dividenden-Policy, Buyback-Authorization. Fuer diese Keys "
    "muss ein Guidance-Wert oder Analysten-Konsens her.\n"
    "- Balance-Sheet-Positionen (Cash & Equivalents, Marketable Securities "
    "ST/LT, Long-term Debt, Lease Liabilities, Net Debt): Fuer diese gibt "
    "es keine Forward-Guidance. LIEFERE TROTZDEM EINEN WERT — naemlich "
    "den letzten im juengsten 10-K oder 10-Q veroeffentlichten Istwert "
    "als Approximation. Kennzeichne QUELLE explizit als "
    "'Approximation: letzter 10-Q/10-K-Wert per <Stichtag>'. "
    "Das ist eine valide Naeherung — kein 'erraten'.\n\n"
    "WERT: NICHT_GEFUNDEN nur wenn wirklich gar kein historischer "
    "Referenzwert auffindbar ist.\n\n"
    "QUELLE muss den Modus explizit machen: "
    "'Guidance FY{YEAR}' / 'Analysten-Konsens FY{YEAR}' / "
    "'Approximation: letzter 10-Q-Wert per <Datum>'."
)

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


ANALYSIS_SYSTEM_PROMPT = """Du bist ein erfahrener Kapitalallokations-Analyst im Stil von
Sir Christopher Hohn (TCI Fund). Deine Aufgabe: Berechnete Kennzahlen
(insbesondere Hohn-Rendite, FCF Yield, NI Growth, Net Debt Change) fuer
den Nutzer zerlegen und wirtschaftlich einordnen.

Antwortstruktur (strikt):

1. **Kurze Einordnung** (1-2 Saetze): Was misst die Kennzahl?

2. **Komponenten-Tabelle** im Markdown-Format mit Spalten
   `Komponente | Wert | Effekt`. Jede Input-Komponente bekommt eine
   Zeile mit Wert und einem Marker:
     ✅ positiv   ⚠️ neutral   ❌ negativ
   Letzte Zeile: Gesamtergebnis mit 🔴 rot / 🟢 gruen / 🟡 gelb.

3. **Treiber-Analyse** (2-3 Haupttreiber, pos und neg getrennt).
   Pro Treiber: kurze Ursachenbeschreibung, ggf. Zusammenhang zu
   operativen Metriken, typische Fallstricke (z.B. one-time tax items,
   valuation allowance release).

4. **Business-Interpretation** (3-5 Saetze): Was bedeutet das aus
   Sicht eines langfristigen Aktionaers? Qualitaet des Wachstums,
   Kapitalallokation, Dilution.

Nutze die im Kontext gelieferten Finanzdaten. Fuer historische Perspektive
oder Begruendungen (z.B. "warum war 2023 das NI so hoch?") aktiv
web_search nutzen und IR-Seite / 10-K des Unternehmens pruefen.

Formeln zur Referenz:
- Hohn Return (simple)  = FCF Yield + NI Growth - SBC/MCap + ΔND/MCap
- Hohn Return (detailed) = Div Yield + NI Growth + Net Buyback/MCap + ΔND/MCap

Antworte auf Deutsch, Fachbegriffe auf Englisch. Keine WERT:/EINHEIT:-
Marker in dieser Mode — das ist eine Analyse, keine Wert-Recherche."""


# Keys whose drawer chat should open in analysis (decomposition) mode
# instead of research mode.
ANALYSIS_MODE_KEYS: frozenset[str] = frozenset({
    "hohn_return_simple",
    "hohn_return_detailed",
    "fcf_yield",
    "sbc_yield",
    "net_buyback_yield",
    "dividend_yield",
    "ni_growth",
    "net_debt_change",
    "net_debt_change_pct",
    "net_debt",
    "ev",
    "cash_sum",
    "debt_sum",
    "net_buyback",
    "market_cap_calc",
})


def get_client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 2,
}


def _collect_text(response) -> str:
    """Concatenate all text blocks of a Claude response. Tool-use / server-tool
    blocks are skipped so callers only see the final prose."""
    parts: list[str] = []
    for block in response.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            parts.append(getattr(block, "text", "") or "")
    return "\n".join(p for p in parts if p)


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

QUELLEN-PRIORITAET (strikt einhalten):
1. Investor-Relations-Seite des Unternehmens selbst (investors.<domain>, z.B.
   investors.servicenow.com, ir.apple.com). Hier sind Earnings-Releases,
   Press Releases und Guidance-Dokumente des Managements direkt aus erster Hand.
2. 10-K / 20-F Jahresabschluss bei der SEC (sec.gov/edgar).
3. Earnings Release PDF / 8-K Filing fuer das gefragte Quartal / Jahr.
4. Analysten-Konsens nur als Fallback, wenn 1-3 nicht verfuegbar.
Yahoo Finance / Finanzdatenbanken zaehlen NICHT als Primaerquelle — nur
wenn es ueberhaupt keine Quelle aus 1-4 gibt.

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
    "market_cap": (0, 15_000_000_000_000),
    "shares_outstanding": (0, 1_000_000_000_000),
    "sbc": (0, 500_000_000_000),
    "net_income": (-5_000_000_000_000, 5_000_000_000_000),
    "op_cash_flow": (-5_000_000_000_000, 5_000_000_000_000),
    "capex": (0, 5_000_000_000_000),
    "debt": (0, 10_000_000_000_000),
    "cash": (0, 5_000_000_000_000),
}


KEY_RESEARCH_HINTS: dict[str, str] = {
    "sbc": (
        "Stock Based Compensation (SBC): jaehrlicher Betrag aus dem 10-K "
        "(oder 20-F bei Non-US-Filern) unter 'Share-based compensation "
        "expense' bzw. im Cash Flow Statement als 'Stock-based compensation'. "
        "Primaer von der IR-Seite (Annual Report PDF), sekundaer SEC Edgar."
    ),
    "net_income": (
        "Net Income (Nettogewinn) fuer das exakte Geschaeftsjahr. Primaer "
        "IR-Seite (Press Release / Annual Report), sekundaer 10-K. Keine TTM, "
        "keine non-GAAP-Adjustments."
    ),
    "op_cash_flow": (
        "Operating Cash Flow (Net cash provided by operating activities) "
        "fuer das exakte Geschaeftsjahr. Primaer IR-Press-Release, sekundaer "
        "Cashflow-Statement im 10-K. Fuer das laufende Jahr sind Guidance-"
        "Zahlen akzeptabel — dann QUELLE explizit als 'IR Guidance FY XXXX'."
    ),
    "capex": (
        "Capital Expenditures fuer das exakte Geschaeftsjahr, Zeile 'Purchase "
        "of property and equipment' / 'Capital expenditures'. Immer POSITIV. "
        "Primaer IR-Seite, sekundaer 10-K. Fuer laufendes Jahr Guidance ok."
    ),
    "debt": (
        "Total Debt aus dem Balance Sheet zum Ende des exakten Geschaeftsjahrs. "
        "Long-Term Debt + Short-Term Debt + Lease Liabilities (sofern als "
        "Finanzschuld klassifiziert). Primaer IR (Annual Report), sekundaer 10-K."
    ),
    "cash": (
        "Cash and Cash Equivalents + Short-Term Investments/Marketable "
        "Securities aus dem Balance Sheet zum Ende des exakten Geschaeftsjahrs. "
        "Primaer IR, sekundaer 10-K."
    ),
    "shares_outstanding": (
        "Diluted Weighted Average Shares Outstanding aus dem jeweiligen 10-K "
        "(oder aktuelle Zahl zum letzten Stichtag aus IR)."
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
    is_forward = _is_forward_year(period_year)
    if period_type == "FY" and period_year:
        marker = "e" if is_forward else ""
        period_str = f"Geschaeftsjahr {period_year}{marker} (FY{period_year}{marker})"
    else:
        period_str = "aktueller/letzter verfügbarer Wert"

    hint = KEY_RESEARCH_HINTS.get(value_key or "", "")
    hint_block = f"\n\nKontext zur Datenquelle:\n{hint}" if hint else ""

    if is_forward:
        forward_block = "\n\n" + FORWARD_YEAR_HINT.replace("{YEAR}", str(period_year))
        historical_constraint = ""
        not_found_clause = (
            "Wenn wirklich weder Guidance noch Analysten-Konsens noch ein "
            "historischer Referenzwert auffindbar ist, antworte mit "
            "WERT: NICHT_GEFUNDEN — sonst immer einen Zahlenwert liefern "
            "und die QUELLE entsprechend markieren."
        )
    else:
        forward_block = ""
        historical_constraint = (
            " Keine TTM/LTM/Trailing-Werte, keine Forward-Guidance wenn ein "
            "historisches Jahr gefragt ist, keine Schaetzungen aus "
            "Quartalsberichten."
        )
        not_found_clause = (
            "Wenn du fuer {period_str} keinen verifizierbaren Wert aus dem "
            "Jahresabschluss findest, antworte mit WERT: NICHT_GEFUNDEN."
        ).replace("{period_str}", period_str)

    user_prompt = (
        f"Unternehmen: {company_name} ({ticker}, {currency})\n"
        f"Gesuchte Kennzahl: {value_label}\n"
        f"Zeitraum: {period_str}\n\n"
        f"Wichtig: Liefere AUSSCHLIESSLICH den Wert fuer {period_str}.{historical_constraint} "
        f"{not_found_clause}\n\n"
        f"Nutze das Web-Search-Tool um die IR-Seite des Unternehmens, "
        f"Annual-Report-PDFs und SEC-Filings aktiv zu durchsuchen. "
        f"Verlasse dich NICHT nur auf dein Gedaechtnis."
        f"{forward_block}"
        f"{hint_block}"
    )

    try:
        client = get_client()
        response = claude_limiter.call(lambda: client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=[{"type": "text", "text": RESEARCH_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=[WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": user_prompt}],
        ))
        content = _collect_text(response)
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

    if mode == "qualitative":
        system_prompt = QUALITATIVE_SYSTEM_PROMPT
    elif mode == "analysis":
        system_prompt = ANALYSIS_SYSTEM_PROMPT
    else:
        system_prompt = RESEARCH_SYSTEM_PROMPT

    user_messages = []
    for msg in messages:
        content = msg["content"]
        if msg["role"] == "user" and "Unternehmen:" in content and "Gesuchte Kennzahl:" in content:
            content = _rewrite_research_message(content)
        user_messages.append({"role": msg["role"], "content": content})

    kwargs: dict = dict(
        model="claude-sonnet-4-6",
        max_tokens=2048,
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
    )
    if mode != "qualitative":
        kwargs["tools"] = [WEB_SEARCH_TOOL]

    response = claude_limiter.call(lambda: client.messages.create(**kwargs))

    content = _collect_text(response)
    if mode == "qualitative":
        score = extract_score(content)
    elif mode == "analysis":
        # Analysis mode doesn't extract a numeric score; it explains instead.
        score = None
    else:
        score = extract_value(content)
    return content, score
