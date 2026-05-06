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


SINGLE_METRIC_ANALYSIS_PROMPT = """Du bist ein erfahrener Finanzanalyst. Deine Aufgabe: EINE einzelne
berechnete Kennzahl, die in der ersten User-Nachricht im Bezug-Header
angegeben ist, fuer den Nutzer zerlegen und wirtschaftlich einordnen.

SCOPE-REGEL (kritisch — Verstoss = falsche Antwort):
Antworte STRIKT NUR zur Kennzahl im "Bezug:"-Header. Wenn der Bezug
'FCF Yield' ist, sprichst du AUSSCHLIESSLICH ueber FCF Yield (=
FCF / Market Cap). Erwaehne NICHT die Hohn-Rendite, NICHT andere
Komponenten der Hohn-Rendite (NI Growth, SBC, Dividenden, Net Debt
Change), es sei denn sie sind DIREKTE INPUTS oder operative Treiber
der gefragten Kennzahl. Keine "uebergeordnete Hohn-Analyse".

Antwortstruktur (kurz, fokussiert auf die EINE Kennzahl):

1. **Was misst diese Kennzahl** (1-2 Saetze + Formel): klare Definition
   der gefragten Kennzahl plus ihre exakte Berechnungsformel mit den
   relevanten Eingangsgroessen.

2. **Komponenten-Tabelle** (nur Inputs DIESER Kennzahl): Markdown
   `Komponente | Wert | Effekt` mit ✅ positiv / ⚠️ neutral / ❌ negativ.
   Letzte Zeile: Ergebnis-Wert mit 🔴 / 🟢 / 🟡.
   Beispiel fuer FCF Yield: Zeile 1 = FCF, Zeile 2 = Market Cap,
   Zeile 3 = Resultat. KEINE Zeilen fuer NI Growth o.ae.

3. **Treiber-Analyse** (2-3 Haupttreiber DIESER Kennzahl): Was treibt
   den Wert hoch oder runter? Bezug zu operativen Metriken (Margins,
   CapEx-Intensitaet, Working-Capital, Akquisitionen). Typische
   Fallstricke (one-time items, Tax-Settlements).

4. **Business-Interpretation** (2-4 Saetze): Was bedeutet GENAU DIESER
   Wert aus Sicht eines langfristigen Aktionaers? Ist die Hoehe
   nachhaltig? Branche-typisch hoch/niedrig?

DATENQUELLE-REGEL: Arbeite ausschliesslich mit den Finanzdaten im
Kontext plus allgemeinem Business-Wissen. web_search nur wenn das Tool
verfuegbar ist UND der User-Prompt explizit nach historischer
Begruendung / One-Time-Items / Earnings-Call-Commentary fragt. Maximal
2 Suchen, gezielt auf IR-Seite oder 10-K. Wenn Tool nicht verfuegbar:
keine Suche, keine erfundenen Quellen.

Antworte auf Deutsch, Fachbegriffe auf Englisch. Keine WERT:/EINHEIT:-
Marker — das ist Analyse, keine Wert-Recherche."""


HOHN_RETURN_ANALYSIS_PROMPT = """Du bist ein erfahrener Kapitalallokations-Analyst im Stil von
Sir Christopher Hohn (TCI Fund). Deine Aufgabe: Die GESAMTE Hohn-Rendite
fuer den Nutzer zerlegen — alle Komponenten, ihre Beitraege, und das
Big-Picture fuer einen langfristigen Aktionaer.

SCOPE: Diese Konversation behandelt entweder Hohn Return (simple) oder
Hohn Return (detailed) — siehe Bezug-Header. Beide sind Aggregat-
Kennzahlen aus mehreren Komponenten. Decke ALLE Komponenten ab.

FORMELN:
- Hohn Return (simple)   = FCF Yield + NI Growth - SBC Yield + ΔND/MCap
- Hohn Return (detailed) = Dividend Yield + NI Growth + Net Buyback Yield + ΔND/MCap

Komponenten-Definitionen:
- FCF Yield         = FCF / Market Cap
- SBC Yield         = SBC / Market Cap
- Net Buyback Yield = (Buyback Volume - SBC) / Market Cap
- Dividend Yield    = Dividends / Market Cap
- NI Growth         = (NI(t) / NI(t-1) - 1) * 100
- Net Debt          = (Long-term Debt + Lease Liabilities) - (Cash + Marketable Securities)
- Net Debt Change   = Net Debt(t-1) - Net Debt(t)  (positiv = Schulden-Abbau)
- ΔND / MCap        = Net Debt Change / Market Cap

Antwortstruktur (strikt):

1. **Kurze Einordnung** (1-2 Saetze): Was misst die Hohn-Rendite, und
   um welche Variante (simple oder detailed) geht es hier?

2. **Komponenten-Tabelle** im Markdown-Format mit Spalten
   `Komponente | Wert | Beitrag (pp) | Effekt`. Jede Komponente der
   jeweiligen Hohn-Formel bekommt eine Zeile mit Wert, ihrem Beitrag in
   Prozentpunkten, und Marker:
     ✅ positiv   ⚠️ neutral   ❌ negativ
   Letzte Zeile: Hohn-Rendite Gesamt mit 🔴 rot / 🟢 gruen / 🟡 gelb.

3. **Treiber-Analyse** (welche Komponente dominiert, welche zieht runter,
   warum). Bezug zu operativen Metriken / Capital-Allocation-Politik
   des Managements. Typische Fallstricke (one-time tax items, valuation
   allowance release, Akquisitions-Effekte).

4. **Business-Interpretation** (3-5 Saetze): Wie ist die Qualitaet
   dieser Rendite? Nachhaltig oder one-time-getrieben? Wie steht das
   Management zu Capital Allocation (Buybacks vs Dividenden vs Wachstum
   vs Schulden-Abbau)? Was sollte ein langfristiger Aktionaer beobachten?

DATENQUELLE-REGEL: Arbeite primaer mit den Finanzdaten im Kontext.
web_search nur wenn das Tool verfuegbar ist UND der User-Prompt explizit
nach historischer Begruendung / Sondereffekten / Earnings-Commentary
fragt. Maximal 2 Suchen, gezielt auf IR-Seite oder 10-K.

Antworte auf Deutsch, Fachbegriffe auf Englisch. Keine WERT:/EINHEIT:-
Marker — das ist Analyse, keine Wert-Recherche."""


HOHN_RETURN_KEYS: frozenset[str] = frozenset({
    "hohn_return_simple",
    "hohn_return_detailed",
})


# Keys whose drawer chat should open in analysis (decomposition) mode
# instead of research mode.
ANALYSIS_MODE_KEYS: frozenset[str] = frozenset({
    "hohn_return_simple",
    "hohn_return_detailed",
    "fcf_yield",
    "sbc_yield",
    "net_buyback_yield",
    "buyback_yield",
    "dividend_yield",
    "ni_growth",
    "net_debt_change",
    "net_debt_change_pct",
    "net_buyback",
    "market_cap_calc",
    "actual_return",
})


def get_client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 5,
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


RESEARCH_PROMPT = """Du bist ein Finanzanalyst. Recherchiere EINE konkrete Finanzkennzahl
fuer ein Unternehmen via web_search und liefere genau eine Zahl + kurze Begruendung.

VORGEHEN
1. Such gezielt mit explizitem Jahr UND Site-Hint, z.B.:
     "Allianz net_income FY2024 stockanalysis"
     "ASML lease liabilities 2024 macrotrends"
2. Bevorzuge Aggregatoren (Snippet-tauglich):
     - Cash-Flow:  stockanalysis.com/.../cash-flow-statement/
     - Bilanz:     stockanalysis.com/.../balance-sheet/, wsj.com/market-data/quotes/.../financials/annual/balance-sheet
     - GuV:        macrotrends.net/.../net-income, stockanalysis.com/.../financials/
     - Guidance:   investor.<domain>/, seekingalpha.com (Transcripts), Yahoo Analyst Estimates
   Direkte 10-K/PDF-Links liefern via web_search meist nur leeren Snippet — vermeiden.
3. Kreuz-Check mind. 2 Quellen wenn moeglich. Bei Konflikt die mit hoeherer Konfidenz.
4. Fallback nur wenn Aggregatoren nichts liefern: Analysten-Konsens / IR-Guidance —
   QUELLE muss das ehrlich kennzeichnen.

ANTWORT — exakt dieses Format, nichts anderes davor/danach:
WERT: [Zahl in Base-Units]
EINHEIT: [USD/EUR/...|%|keine]
QUELLE: [Kurzbezeichnung, z.B. "stockanalysis.com FY2024 Income Statement"]
QUELLE_URL: [echte direkte URL]
ZEITRAUM: [z.B. FY2024, TTM, aktuell]
KONFIDENZ: [hoch|mittel|niedrig]
BEGRUENDUNG: [1-2 Saetze: woher der Wert genau stammt + ggf. Sondereffekte]

Wenn nichts Verifizierbares findbar ist:
WERT: NICHT_GEFUNDEN
BEGRUENDUNG: [1 Satz warum]

ZAHLENFORMAT — strikt:
- Volle Zahl in Base-Units, OHNE Suffix.  RICHTIG: 1450000000 USD.  FALSCH: 1450 USD Mio.
- Prozente direkt als Wert.  RICHTIG: 4.38 %.  FALSCH: 0.0438.
- EINHEIT enthaelt NUR Waehrung / "%" / "keine" — NIE "Mio"/"Mrd".
- Punkt als Dezimaltrenner.
- Keine erfundenen URLs."""


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
    "fcf": (-5_000_000_000_000, 5_000_000_000_000),
    "buyback_volume": (0, 1_000_000_000_000),
    "dividends": (0, 1_000_000_000_000),
    # net_debt darf negativ sein (Net Cash Position) — daher symmetrische Range.
    "net_debt": (-5_000_000_000_000, 10_000_000_000_000),
}


KEY_RESEARCH_HINTS: dict[str, str] = {
    "net_income": (
        "Net Income (Nettogewinn, GAAP) zum Ende des exakten Geschaeftsjahrs. "
        "Income Statement → letzte Zeile 'Net Income'. Keine TTM, keine non-GAAP-Adjustments."
    ),
    "fcf": (
        "Free Cash Flow = Operating Cash Flow − Capital Expenditures. Manche "
        "Aggregatoren weisen FCF separat aus (stockanalysis.com), sonst die "
        "beiden Komponenten suchen und subtrahieren. POSITIV bei normaler Cash-Generierung."
    ),
    "sbc": (
        "Stock-Based Compensation Expense (Aufwand). Cash Flow Statement → "
        "'Stock-based compensation' (Add-back im operativen CF). Immer POSITIV."
    ),
    "buyback_volume": (
        "Aktienrueckkaeufe in Cash, jaehrliches Volumen. Cash Flow Statement → "
        "'Repurchase of common stock' / 'Treasury stock purchases'. Immer POSITIV "
        "(Output-Sicht; Aggregatoren zeigen es teils negativ — Vorzeichen ignorieren)."
    ),
    "dividends": (
        "Dividenden-Cashout im Geschaeftsjahr. Cash Flow Statement → "
        "'Dividends paid' / 'Cash dividends'. Immer POSITIV als Auszahlungsbetrag."
    ),
    "net_debt": (
        "Net Financial Debt / Nettofinanzschulden zum Bilanzstichtag. EINE Zahl, "
        "Vorzeichen erhalten — POSITIV = mehr Debt als Cash, NEGATIV = Net Cash. "
        "Suchorte: Highlights, Management Report (Liquidity/Capital Structure), "
        "Notes zu Borrowings (Net-Debt-Reconciliation). Bei Versicherern KEINE "
        "Long-term Marketable Securities als Cash zaehlen — die decken Reserven."
    ),
    "shares_outstanding": (
        "Diluted Weighted Average Shares Outstanding aus der Income Statement, "
        "ODER (bei Stammdaten-Snapshot) aktueller Bestand zum letzten Stichtag aus IR."
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
            " Ziel ist der exakte Jahreswert aus dem 10-K/20-F. Keine TTM/LTM."
        )
        not_found_clause = (
            "FALLBACK-REGEL: Wenn die Web-Suche keinen exakten Wert fuer "
            f"{period_str} in Aggregatoren (stockanalysis.com, macrotrends.net, "
            "wisesheets.io, wsj.com) findet — was fuer sehr junge 10-Ks "
            "(letzte 6 Monate) oder spezifische Bilanznoten haeufig ist — "
            "liefere den naechstliegenden bekannten Istwert (z.B. FY"
            f"{period_year - 1} oder juengstes 10-Q) als Approximation. "
            "QUELLE muss das klar kennzeichnen: z.B. 'Approximation: FY"
            f"{period_year - 1} Istwert — FY{period_year} in Aggregatoren "
            "noch nicht verfuegbar'. KONFIDENZ: niedrig. "
            "WERT: NICHT_GEFUNDEN nur wenn du nicht einmal einen alten "
            "Istwert finden kannst."
        )

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


def call_claude(
    messages: list[dict[str, str]],
    company_context: str,
    mode: str = "qualitative",
    enable_search: bool = True,
    value_key: str | None = None,
) -> tuple[str, Decimal | None]:
    client = get_client()

    if mode == "qualitative":
        system_prompt = QUALITATIVE_SYSTEM_PROMPT
    elif mode == "analysis":
        if value_key in HOHN_RETURN_KEYS:
            system_prompt = HOHN_RETURN_ANALYSIS_PROMPT
        else:
            system_prompt = SINGLE_METRIC_ANALYSIS_PROMPT
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
                # Cache the company context too — repeated chats on the same cell
                # within 5 min get a ~10x cost reduction on this large block.
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=user_messages,
    )
    if mode == "research" or (mode == "analysis" and enable_search):
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
