import re
import logging
from datetime import date
from decimal import Decimal, InvalidOperation

import anthropic

from app.config import settings
from app.llm.rate_limiter import claude_limiter

logger = logging.getLogger(__name__)


def _is_forward_year(period_year: int | None) -> bool:
    """True if no 10-K has been filed yet for the given FY."""
    if period_year is None:
        return False
    return period_year >= date.today().year


FORWARD_YEAR_HINT = (
    "DIESES JAHR LIEGT IN DER ZUKUNFT: Das Unternehmen hat dafür NOCH KEINEN "
    "10-K / 20-F veröffentlicht. Liefere trotzdem den BESTEN verfügbaren "
    "Zahlenwert — IMMER eine Zahl, im aller-letzten Fall 0 mit ehrlicher "
    "Begründung in QUELLE.\n\n"
    "Suche in dieser Reihenfolge:\n"
    "1. IR-Guidance aus dem letzten Q4/Q1-Earnings-Call Transcript oder "
    "Press Release (Management-Outlook).\n"
    "2. Investor Presentations / Guidance-Folien (z.B. 'FY{YEAR} Outlook').\n"
    "3. Analysten-Konsens (Yahoo Finance Analyst Estimates, Factset, "
    "Refinitiv, Seeking Alpha Consensus).\n"
    "4. Fallback: letzter verfügbarer Istwert aus dem jüngsten Quartals-"
    "oder Jahresbericht (10-Q / 10-K).\n\n"
    "Kategorisierung:\n"
    "- Gut prognostizierbar (echte Guidance): FCF, Net Income, Sales, "
    "SBC, Dividenden-Policy, Buyback-Authorization. Für diese Keys "
    "muss ein Guidance-Wert oder Analysten-Konsens her.\n"
    "- Balance-Sheet-Positionen (Cash & Equivalents, Marketable Securities "
    "ST/LT, Long-term Debt, Lease Liabilities, Net Debt): Für diese gibt "
    "es keine Forward-Guidance. LIEFERE TROTZDEM EINEN WERT — nämlich "
    "den letzten im jüngsten 10-K oder 10-Q veröffentlichten Istwert "
    "als Approximation. Kennzeichne QUELLE explizit als "
    "'Approximation: letzter 10-Q/10-K-Wert per <Stichtag>'. "
    "Das ist eine valide Näherung — kein 'erraten'.\n\n"
    "Du MUSST eine echte Approximations-Zahl liefern — nimm letzten 10-K-Wert × "
    "plausible Wachstumsrate, oder Equivalent-Konzept. NIE 0 als Default-Fallback.\n\n"
    "QUELLE muss den Modus explizit machen: "
    "'Guidance FY{YEAR}' / 'Analysten-Konsens FY{YEAR}' / "
    "'Approximation: letzter 10-Q-Wert per <Datum>'."
)


def get_client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise ValueError("Anthropic API key is not configured")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 5,
}


def _collect_text(response) -> str:
    parts = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _parse_numeric_string(raw: str) -> Decimal | None:
    """Parse a numeric string like '1.45', '1,45', '1.450.000', '4.38 %'."""
    s = raw.strip().replace(" ", "")
    s = re.sub(r"[%]", "", s)
    is_neg = s.startswith("-")
    if is_neg:
        s = s[1:]
    if "," in s and "." in s:
        # German format: 1.234,56 → 1234.56
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # German decimal: 1,45 → 1.45
        last_comma = s.rfind(",")
        before = s[:last_comma].replace(",", "")
        after = s[last_comma + 1:]
        if len(after) <= 2:
            s = f"{before}.{after}"
        else:
            s = before + after
    elif s.count(".") > 1:
        s = s.replace(".", "")
    s = re.sub(r"[^0-9.]", "", s)
    if not s:
        return None
    try:
        v = Decimal(s)
        return -v if is_neg else v
    except InvalidOperation:
        return None


_UNIT_SCALE_PATTERNS = [
    (re.compile(r"\b(Mrd|Milliarden|billion|[Bb])\.?\b", re.IGNORECASE), Decimal("1000000000")),
    (re.compile(r"\b(Mio|Millionen|million|[Mm])\.?\b", re.IGNORECASE), Decimal("1000000")),
    (re.compile(r"\b(Tsd|Tausend|thousand|[Kk])\.?\b", re.IGNORECASE), Decimal("1000")),
]


def _apply_unit_scale(value: Decimal, text: str, wert_raw: str) -> Decimal:
    for pattern, scale in _UNIT_SCALE_PATTERNS:
        if pattern.search(wert_raw):
            return value * scale
    return value


RESEARCH_PROMPT = """Du bist Senior Equity Research Analyst. Du lieferst IMMER eine
fundierte Schaetzung — auch wenn keine konkrete Quelle findbar ist. Du bist Experte
und bezahlst dir deine Einschaetzungen mit deinem Wissen ueber das Unternehmen,
seinen Sektor, vergleichbare Firmen und historische Pattern.

ABSOLUTE GRUNDREGEL:
WERT ist IMMER eine plausible Zahl — entweder aus konkreter Quelle oder als
Experten-Einschaetzung. Es gibt KEINE Antwort wo du keine Zahl lieferst.
'Konnte nichts finden' ist KEINE gueltige Antwort — als Senior Analyst hast du
immer eine fundierte Vorstellung was plausibel ist. Liefere die Zahl mit
ehrlicher Quelle 'KI-Einschaetzung basierend auf [Begruendung]' wenn keine
oeffentliche Quelle existiert.

0 ist NUR gueltig wenn deine Schaetzung tatsaechlich 0 ergibt (z.B. Firma hat
nachweisbar kein Buyback-Programm in dem Jahr → wirklich 0). NIEMALS 0 weil
'ich finde nichts'.

WIE DU EINE ZAHL ABLEITEST (verbindliche Reihenfolge):

1. **Exakter Wert** aus Aggregator (stockanalysis, macrotrends, wsj, wisesheets)
   oder direkt aus IR/SEC-Filing.

2. **Management-Guidance / Konsens**: IR-Seite, Earnings Call Transcript,
   Investor Day, Yahoo/Seeking Alpha Analyst Estimates.

3. **Approximation aus letztem bekannten Wert** (= Standard-Pfad bei Forward-Year):
     letzter_FY_Istwert × (1 + plausible Wachstumsrate)
   Wachstumsrate aus: historische 3-5J-CAGR, Industrie-Trend, Q-Run-Rate
   (z.B. Q1+Q2+Q3 YTD × 4/3 fuer FY-Schaetzung).

4. **Equivalent-Konzept bei Sektor-Mismatch**:
     Versicherer FCF       → Operating Profit / Operating Cash Flow
     Versicherer SBC       → Personnel Expenses × 0,5-1% (typische SBC-Quote)
     Versicherer Net Debt  → Total Borrowings − Cash (ohne LT Marketable Sec.)
     Bank FCF              → Operating Profit
   Du MUSST die Equivalent-Zahl AUSRECHNEN und liefern — nicht nur "Konzept fehlt
   → 0".

5. **Branchen-Mittel**: vergleichbare Firma der gleichen Sektor/Groessenklasse
   als Bezug, dann skaliert auf das gesuchte Unternehmen.

6. **KI-EXPERTEN-EINSCHAETZUNG (PFLICHT wenn 1-5 nichts ergibt)**:
   Wenn alle obigen Quellen versagen, BIST DU AN DER REIHE als Senior Equity
   Analyst. Du hast Wissen ueber:
     - Die Firma (Geschaeftsmodell, Profitabilitaet, Capital-Allocation-Politik)
     - Den Sektor (typische Margen, Wachstumsraten, SBC-Quoten, Buyback-Quoten)
     - Vergleichbare Firmen (gleicher Sektor/Groesse)
     - Historische Pattern (z.B. 'Industrials wachsen typisch 3-5%, Tech 10-15%')
   Daraus leitest du eine PLAUSIBLE Zahl ab. Beispiel: "Airbus FY2026 SBC nicht
   aggregator-listed → SBC bei Aerospace-Industrials typisch 0.3-0.5% von Sales,
   Airbus Sales ~80B → SBC ~280M". QUELLE markiert das ehrlich als
   "KI-Einschaetzung: <Begruendung>". KONFIDENZ: niedrig.

7. **0 ist gueltig NUR wenn**: Firma hat in der Periode dokumentiert nichts
   gezahlt/zurueckgekauft/etc. (z.B. "kein Buyback-Programm aktiv" → buyback=0).
   NICHT als Fallback bei Schwierigkeit.

ANTWORT — exakt dieses Format, nichts anderes davor/danach:
WERT: [echte Zahl in Base-Units — bei Approximation den BERECHNETEN Zahlenwert]
EINHEIT: [USD/EUR/...|%|keine]
QUELLE: [Kurzbezeichnung — bei Approximation z.B.
         "Approximation: Airbus FY2025 SBC ~250M × 1,05 CAGR = 262.5M (IR FY2025-Bericht)"]
QUELLE_URL: [echte direkte URL der Datenbasis]
ZEITRAUM: [z.B. FY2024, FY2026e, TTM, Q3 2025]
KONFIDENZ: [hoch|mittel|niedrig]
BEGRUENDUNG: [2-3 Sätze zu Quelle/Schaetzweg]

ZAHLENFORMAT — strikt:
- Volle Zahl in Base-Units, OHNE Suffix.  RICHTIG: 1450000000 USD.  FALSCH: 1450 USD Mio.
- Bei Approximation: Berechne das Endergebnis und gib NUR die Endzahl in WERT.
  Den Rechenweg in QUELLE/BEGRUENDUNG.
- Prozente direkt als Wert.  RICHTIG: 4.38 %.  FALSCH: 0.0438.
- EINHEIT enthaelt NUR Währung / "%" / "keine" — NIE "Mio"/"Mrd".
- Punkt als Dezimaltrenner.
- Keine erfundenen URLs."""


def extract_research_value(text: str) -> Decimal | None:
    """Extract WERT: from Claude research responses.
    Returns None wenn Claude sich nicht ans Format gehalten hat oder NICHT_GEFUNDEN
    geliefert hat — KEIN 0-Fallback (User-Anforderung: 0 nur wenn Approximation
    tatsaechlich 0 ergibt, nicht als Default)."""
    match = re.search(
        r"WERT:\s*([+-]?[\d.,]+(?:\s*(?:Mrd|Milliarden|Mio|Millionen|billion|million|[BMTK])\.?)?(?:\s*%)?|NICHT[_\s]?GEFUNDEN)",
        text,
        re.IGNORECASE,
    )
    if not match:
        # Diagnose: was hat Claude eigentlich geantwortet? Erste 500 chars logged
        # damit wir sehen ob es ein Format-Issue ist (kein WERT-Praefix), web_search
        # tool-block ohne text, oder ein Refusal.
        preview = (text or "")[:500].replace("\n", " | ")
        logger.warning("research: Claude antwortete ohne WERT-Pattern — caller bekommt None. Antwort-Preview: %s",
                       preview)
        return None
    raw = match.group(1).strip()
    if re.match(r"nicht.{0,2}gefunden", raw, re.IGNORECASE):
        logger.warning("research: Claude antwortete NICHT_GEFUNDEN trotz Verbot — caller bekommt None")
        return None
    value = _parse_numeric_string(raw)
    if value is None:
        logger.warning("research: WERT '%s' parst nicht — caller bekommt None", raw)
        return None
    return _apply_unit_scale(value, text, raw)


# Currency-Keys aus values/currency_keys importieren wuerde Zirkular machen —
# duplizieren als Set fuer die unit-heuristik unten.
_LIKELY_CURRENCY_KEYS = frozenset({
    "net_income", "fcf", "sbc", "buyback_volume", "dividends",
    "net_debt", "market_cap",
})


def detect_unit_error(key: str, value: Decimal) -> bool:
    """True wenn ein Currency-Key suspicious klein ist (z.B. WERT: 1.45 USD
    statt 1450000000 USD — Claude hat vergessen die Approximation in
    Base-Units zu liefern). Threshold: 1 Million als Untergrenze fuer
    sinnvolle Konzern-Kennzahlen. shares_outstanding ausgenommen weil
    legitim klein bei Bruchwerten."""
    if key not in _LIKELY_CURRENCY_KEYS:
        return False
    try:
        v = abs(float(value))
    except (ValueError, OverflowError):
        return True
    if v == 0:
        return False  # echte 0 ist OK
    return v < 1_000_000


_CLAUDE_SANITY_CHECKS: dict[str, tuple[float, float]] = {
    "market_cap": (0, 15_000_000_000_000),
    "shares_outstanding": (0, 1_000_000_000_000),
    "sbc": (0, 500_000_000_000),
    "net_income": (-5_000_000_000_000, 5_000_000_000_000),
    "fcf": (-5_000_000_000_000, 5_000_000_000_000),
    "buyback_volume": (0, 1_000_000_000_000),
    "dividends": (0, 1_000_000_000_000),
    "net_debt": (-5_000_000_000_000, 10_000_000_000_000),
}


KEY_RESEARCH_HINTS: dict[str, str] = {
    "net_income": (
        "Net Income (Nettogewinn, GAAP) zum Ende des exakten Geschäftsjahrs. "
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
        "Aktienrückkaeufe in Cash, jaehrliches Volumen. Cash Flow Statement → "
        "'Repurchase of common stock' / 'Treasury stock purchases'. Immer POSITIV "
        "(Output-Sicht; Aggregatoren zeigen es teils negativ — Vorzeichen ignorieren)."
    ),
    "dividends": (
        "Dividenden-Cashout im Geschäftsjahr. Cash Flow Statement → "
        "'Dividends paid' / 'Cash dividends'. Immer POSITIV als Auszahlungsbetrag."
    ),
    "net_debt": (
        "Net Financial Debt / Nettofinanzschulden zum Bilanzstichtag. EINE Zahl, "
        "Vorzeichen erhalten — POSITIV = mehr Debt als Cash, NEGATIV = Net Cash. "
        "Suchorte: Highlights, Management Report (Liquidity/Capital Structure), "
        "Notes zu Borrowings (Net-Debt-Reconciliation). Bei Versicherern KEINE "
        "Long-term Marketable Securities als Cash zählen — die decken Reserven."
    ),
    "shares_outstanding": (
        "Diluted Weighted Average Shares Outstanding aus der Income Statement, "
        "ODER (bei Stammdaten-Snapshot) aktueller Bestand zum letzten Stichtag aus IR."
    ),
}


def validate_claude_value(key: str, value: Decimal) -> Decimal | None:
    """Range-Check für Claude-Werte. None wenn ausserhalb plausibler Range
    oder wenn die unit-heuristik anschlaegt (Currency-Wert verdaechtig klein
    -> Claude hat vermutlich Mio/Mrd vergessen)."""
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
    if detect_unit_error(key, value):
        logger.warning(
            "Claude value sanity: %s=%s suspicious klein (Unit-Verwechslung Mio/Mrd?) — dropping",
            key, value,
        )
        return None
    if fval == 0 and key in ("sbc", "buyback_volume", "dividends", "net_income", "fcf"):
        logger.info(
            "Claude value: %s=0 — kann legitim sein (Firma zahlt z.B. keine Dividende) "
            "oder ein Default-Fallback. Source-Name pruefen.", key,
        )
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
    """Web-Recherche für eine einzelne Kennzahl.
    Returns (value, source_name, source_url, user_prompt, assistant_response)."""
    is_forward = _is_forward_year(period_year)
    is_quarter = period_type in ("Q1", "Q2", "Q3", "Q4")
    if period_type == "FY" and period_year:
        marker = "e" if is_forward else ""
        period_str = f"Geschäftsjahr {period_year}{marker} (FY{period_year}{marker})"
    elif is_quarter and period_year:
        period_str = f"{period_type} {period_year} (Quartal — kumulativ year-to-date wenn moeglich, sonst standalone)"
    else:
        period_str = "aktueller/letzter verfügbarer Wert"

    hint = KEY_RESEARCH_HINTS.get(value_key or "", "")
    hint_block = f"\n\nKontext zur Datenquelle:\n{hint}" if hint else ""

    if is_forward:
        forward_block = "\n\n" + FORWARD_YEAR_HINT.replace("{YEAR}", str(period_year))
        historical_constraint = ""
        not_found_clause = (
            "PFLICHT: Liefere IMMER eine echte berechnete Zahl. Reihenfolge:\n"
            f"  1. Konkrete Management-Guidance für FY{period_year} aus IR/Earnings-Calls\n"
            f"  2. Analysten-Konsens für FY{period_year} (Yahoo, Seeking Alpha, Reuters)\n"
            f"  3. Approximation: FY{period_year - 1} Istwert × plausibler Wachstumsrate "
            "(Industrie-Growth, historische CAGR der letzten 3-5 Jahre, Q-trend "
            f"YTD-Hochrechnung). RECHNE die Endzahl aus und liefere sie als WERT — "
            f"nicht 0 + Beschreibung in BEGRUENDUNG.\n"
            f"  4. Fallback-Approximation: FY{period_year - 1} Istwert ohne Growth — "
            f"als konkrete Zahl in WERT, nicht 0.\n"
            f"WERT: 0 ist NUR gueltig wenn deine Approximation echt 0 ergibt "
            f"(z.B. Firma hat dokumentiert kein Buyback-Programm). NIE 0 als Fallback."
        )
    elif is_quarter:
        forward_block = ""
        historical_constraint = (
            f" Ziel ist der {period_type}-Wert aus dem Quartalsbericht "
            f"(idealerweise YTD-kumulativ wenn so ausgewiesen)."
        )
        not_found_clause = (
            "PFLICHT: Liefere IMMER eine echte berechnete Zahl. Reihenfolge:\n"
            f"  1. Exakter {period_type} {period_year}-Wert aus IR/Aggregator/10-Q.\n"
            f"  2. YTD-kumulativ wenn standalone-Q nicht findbar.\n"
            f"  3. Bei Versicherer/Bank/Sektor-Mismatch: AUSRECHNEN das Equivalent "
            f"(z.B. 'Personnel Expenses × 0.5%' fuer SBC bei Versicherer, "
            f"'Total Borrowings - Cash' fuer Net Debt). RECHNE die konkrete Zahl, "
            f"liefere sie als WERT.\n"
            f"  4. Approximation: FY{period_year}-Wert × Quartal-Anteil "
            f"(0.25 fuer einzelnes Quartal, 0.50/0.75 fuer YTD) — als Zahl in WERT.\n"
            f"WERT: 0 nur wenn deine Approximation tatsaechlich 0 ergibt. NIE als Fallback."
        )
    else:
        forward_block = ""
        historical_constraint = (
            " Ziel ist der exakte Jahreswert aus dem 10-K/20-F. Keine TTM/LTM."
        )
        not_found_clause = (
            "PFLICHT: Liefere IMMER eine echte berechnete Zahl. Reihenfolge:\n"
            f"  1. Exakter Wert für {period_str} aus Aggregator (stockanalysis, "
            f"macrotrends, wsj).\n"
            f"  2. Approximation: FY{period_year - 1} Istwert (oder jüngstes 10-Q) "
            f"× plausibler Adjustment-Faktor — als konkrete Zahl in WERT. "
            f"KONFIDENZ: niedrig.\n"
            f"  3. Equivalent-Konzept fuer Sektor-Mismatch (siehe System-Prompt) — "
            f"AUSRECHNEN, nicht 0.\n"
            f"WERT: 0 nur wenn deine Approximation tatsaechlich 0 ergibt. NIE als Fallback."
        )

    user_prompt = (
        f"Unternehmen: {company_name} ({ticker}, {currency})\n"
        f"Gesuchte Kennzahl: {value_label}\n"
        f"Zeitraum: {period_str}\n\n"
        f"WAEHRUNG-PFLICHT: Liefere den Wert in {currency} (Original-Reporting-"
        f"Waehrung der Firma). KEINE Umrechnung in USD/EUR/anderes Currency. "
        f"Wenn die Quelle in einer anderen Waehrung berichtet, rechne KORREKT um "
        f"in {currency} und nenne den verwendeten Kurs in BEGRUENDUNG.\n\n"
        f"Wichtig: Liefere AUSSCHLIESSLICH den Wert für {period_str}.{historical_constraint} "
        f"{not_found_clause}\n\n"
        f"Nutze das Web-Search-Tool um die IR-Seite des Unternehmens, "
        f"Annual-Report-PDFs und SEC-Filings aktiv zu durchsuchen. "
        f"Verlasse dich NICHT nur auf dein Gedächtnis."
        f"{forward_block}"
        f"{hint_block}"
    )

    def _do_call(messages: list[dict]) -> str:
        response = claude_limiter.call(lambda: client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=[{"type": "text", "text": RESEARCH_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=[WEB_SEARCH_TOOL],
            messages=messages,
        ))
        return _collect_text(response)

    try:
        client = get_client()
        content = _do_call([{"role": "user", "content": user_prompt}])
        value = extract_research_value(content)
        sanity_failed_reason: str | None = None

        # Sanity-Check: wenn Wert extrahiert wurde aber sanity-range/unit-error
        # ihn rejected, soll der Retry auch dann greifen. Sonst landet ein
        # 'Web fehlte' im UI obwohl Claude geantwortet hat — nur halt mit
        # offensichtlichem Unit-Fehler (z.B. WERT: 5 statt 5_000_000_000).
        if value is not None and value_key:
            validated = validate_claude_value(value_key, value)
            if validated is None:
                sanity_failed_reason = (
                    f"Vorheriger Wert {value} wurde wegen Sanity-Range oder "
                    f"Unit-Verdacht (Mio/Mrd-Verwechslung?) verworfen"
                )
                value = None

        # Retry-Stufe: Wenn Claude beim ersten Versuch keine Zahl liefert
        # (oder die Zahl Sanity-Check nicht besteht), zwingen wir ihn als
        # Equity-Analyst zur Experten-Einschaetzung. Eine einzige Wiederholung.
        if value is None:
            logger.info("Web-Recherche %s/%s: erster Versuch lieferte keine valide Zahl — "
                        "retry mit Experten-Einschaetzungs-Aufforderung (%s)",
                        ticker, value_key or "?", sanity_failed_reason or "extract=None")
            sanity_hint = ""
            if sanity_failed_reason:
                sanity_hint = (
                    f"\n\nVorsicht: dein letzter Wert wurde verworfen weil er "
                    f"verdaechtig klein war — wahrscheinlich hast du Mio/Mrd "
                    f"vergessen. Gib die Zahl in BASE-UNITS: 1.45 Mrd USD = "
                    f"1450000000, NICHT 1.45 oder 1450."
                )
            retry_prompt = (
                f"Du hast eben keine valide numerische Antwort geliefert. Das ist "
                f"nicht akzeptabel — du bist Senior Equity Analyst, du hast IMMER "
                f"eine fundierte Einschaetzung.\n\n"
                f"Gib JETZT eine plausible Schaetzung fuer {value_label} bei "
                f"{company_name} ({ticker}) im Zeitraum {period_str} basierend auf "
                f"deinem Wissen ueber:\n"
                f"  - Sektor-typische Werte (Margen, Wachstumsraten, "
                f"Capital-Allocation-Quoten)\n"
                f"  - Vergleichbare Firmen aehnlicher Groesse\n"
                f"  - Historische Werte des Unternehmens\n"
                f"  - Aktueller Markt-Konsens / News\n\n"
                f"Liefere die Zahl in {currency} in vollen BASE-UNITS (z.B. "
                f"1450000000 fuer 1,45 Mrd, NICHT 1.45 oder 1450). "
                f"QUELLE = 'KI-Einschaetzung: <dein Schaetzweg in 1 Satz>'. "
                f"KONFIDENZ: niedrig.\n"
                f"NIEMALS 'NICHT_GEFUNDEN' — du bist Experte, gib eine Zahl."
                f"{sanity_hint}"
            )
            content_retry = _do_call([
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": content},
                {"role": "user", "content": retry_prompt},
            ])
            value = extract_research_value(content_retry)
            # Auch hier Sanity-Check anwenden
            if value is not None and value_key:
                validated = validate_claude_value(value_key, value)
                if validated is None:
                    logger.warning("Web-Recherche %s/%s: retry-Wert %s ebenfalls Sanity-Fail",
                                   ticker, value_key, value)
                    value = None
            if value is not None:
                content = content_retry  # nutze die Retry-Antwort fuer source/url
                logger.info("Web-Recherche %s/%s: retry erfolgreich, Wert=%s",
                            ticker, value_key or "?", value)

        if value is None:
            return None, None, None, user_prompt, content
        source_match = re.search(r"QUELLE:\s*(.+)", content)
        source = source_match.group(1).strip() if source_match else "Claude-Recherche"
        url_match = re.search(r"QUELLE_URL:\s*(https?://\S+)", content)
        source_url = url_match.group(1).strip() if url_match else None
        # source ohne Praefix zurueckgeben — Caller (Web-Guidance / Research-
        # Endpoint / Auto-Fallback) entscheidet das passende Praefix.
        return value, source, source_url, user_prompt, content
    except Exception as e:
        logger.warning("Claude research failed for %s/%s: %s", ticker, value_label, e)
        return None, None, None, user_prompt, None
