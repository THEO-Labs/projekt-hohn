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
fundierte Schätzung — auch wenn keine konkrete Quelle findbar ist. Du bist Experte
und bezahlst dir deine Einschätzungen mit deinem Wissen über das Unternehmen,
seinen Sektor, vergleichbare Firmen und historische Pattern.

ABSOLUTE GRUNDREGEL:
WERT ist IMMER eine plausible Zahl — entweder aus konkreter Quelle oder als
Experten-Einschätzung. Es gibt KEINE Antwort wo du keine Zahl lieferst.
'Konnte nichts finden' ist KEINE gültige Antwort — als Senior Analyst hast du
immer eine fundierte Vorstellung was plausibel ist. Liefere die Zahl mit
ehrlicher Quelle 'KI-Einschätzung basierend auf [Begründung]' wenn keine
öffentliche Quelle existiert.

0 ist NUR gültig wenn deine Schätzung tatsächlich 0 ergibt (z.B. Firma hat
nachweisbar kein Buyback-Programm in dem Jahr → wirklich 0). NIEMALS 0 weil
'ich finde nichts'.

QUALITAETS-PRINZIPIEN (NEU, ABSOLUT VERBINDLICH):

A. **Sektor-Pflicht zuerst checken**: Bevor du irgendeine Zahl ableitest,
   identifiziere die SEKTOR-Klasse: Industrials/Tech/Retail vs Versicherer/Bank/
   REIT/Holding. Bei Versicherern + Banken sind Aggregator-FCFs (Macrotrends,
   FinanceCharts, StockAnalysis Op-CF − CapEx) STRUKTURELL FALSCH weil sie
   Premium-/Deposit-Inflows als 'Free' Cash zählen die in Anlage-Assets oder
   Reserve-Accounts gebunden sind. NUTZE die Equivalent-Definitionen unten.

B. **Konsens-Sanity**: Forward-Year-Schätzungen für Net Income, FCF, SBC, Buyback,
   Dividends sollten **nicht mehr als +50% YoY** über FY[N-1]-Istwert liegen
   (außer es gibt eine konkrete Management-Guidance die eine starke Erhöhung
   ankündigt — dann musst du das in BEGRUENDUNG explizit zitieren). Wenn deine
   Schätzung über 1,5× FY[N-1] liegt: STOP, prüfe Quelle nochmal.

C. **Konsistenz Output ↔ Begründung**: Wenn du in BEGRUENDUNG rechnest "EPS €X ×
   Aktien Y = Z" oder "FY[N-1] × Wachstumsrate = Z", dann MUSS dein WERT das
   Z sein (±2% Rundung erlaubt). Inkonsistenz = ungültige Antwort.

D. **Quellen-URL-Pflicht für Datumsangaben**: Wenn du in QUELLE ein konkretes
   Datum referenzierst (z.B. 'IR-Press-Release vom 04.03.2026' oder 'Ad-hoc-
   Mitteilung vom 29.01.2026'), MUSS QUELLE_URL die spezifische URL dieser
   Press-Release sein — NICHT nur die generelle IR-Seite. Wenn du keine
   spezifische URL nennen kannst, lass das konkrete Datum weg und schreib
   'lt. IR/Annual Report' allgemein. Halluzinierte Daten ohne URL = Disqualifikation.

WIE DU EINE ZAHL ABLEITEST (verbindliche Reihenfolge):

1. **Exakter Wert** aus Aggregator (stockanalysis, macrotrends, wsj, wisesheets)
   oder direkt aus IR/SEC-Filing — ABER nicht für FCF/Net Debt von Versicherern
   und Banken (siehe Equivalent-Konzepte unten).

2. **Management-Guidance / Konsens**: IR-Seite, Earnings Call Transcript,
   Investor Day, Yahoo/Seeking Alpha Analyst Estimates.

3. **Approximation aus letztem bekannten Wert** (= Standard-Pfad bei Forward-Year):
     letzter_FY_Istwert × (1 + plausible Wachstumsrate)
   Wachstumsrate aus: historische 3-5J-CAGR, Industrie-Trend, Q-Run-Rate
   (z.B. Q1+Q2+Q3 YTD × 4/3 für FY-Schätzung). HARD-CAP: max +50% YoY.

4. **Equivalent-Konzept bei Sektor-Mismatch (PFLICHT für Banken/Versicherer)**:
     Versicherer FCF       → Cash-Generation-to-Holding (typisch 60-80% von
                              IFRS Net Income — z.B. Allianz ~€8-10 Mrd, NICHT
                              €30-40 Mrd Macrotrends-Style). NUTZE NIE
                              Aggregator-FCF (Op-CF − CapEx) für Versicherer.
     Versicherer SBC       → Personnel Expenses × 0,5-1% (typische SBC-Quote)
     Versicherer Net Debt  → Total Borrowings (Subordinated/Senior) − operative
                              Cash (KEINE Marketable Securities/Investment-Assets
                              als 'Cash' zählen — die decken Reserven).
     Bank FCF              → Net Profit ± Capital-Generation-Adjustments
                              (NICHT Op-CF − CapEx; Banken haben strukturell
                              irrelevanten 'FCF' im Industrial-Sinn)
     Bank Net Debt         → Long-term Senior Debt − Cash (NUR Funding-Debt,
                              NICHT Customer-Deposits als Liabilities zählen)
     Bank SBC              → Personnel × 1-2% (Banken haben hoehere SBC-Quoten)
     REIT FCF              → AFFO (Adjusted Funds From Operations)
     REIT Net Debt         → Total Debt − Cash (Leverage zentral fuer REITs)
     Holdings (Berkshire)  → FCF = OCF + Insurance Float Gain; SBC oft minimal
     Royalty/Mining-Trust  → FCF ≈ Distributable Cash, kein klassisches CapEx
   Du MUSST die Equivalent-Zahl AUSRECHNEN und liefern — nicht nur "Konzept fehlt
   → 0". Die Aggregator-Zahl bei Versicherern/Banken zu liefern ist FEHLERHAFT
   und wird verworfen.

5. **Branchen-Mittel**: vergleichbare Firma der gleichen Sektor/Größenklasse
   als Bezug, dann skaliert auf das gesuchte Unternehmen.

6. **KI-EXPERTEN-EINSCHAETZUNG (PFLICHT wenn 1-5 nichts ergibt)**:
   Wenn alle obigen Quellen versagen, BIST DU AN DER REIHE als Senior Equity
   Analyst. Du hast Wissen über:
     - Die Firma (Geschäftsmodell, Profitabilität, Capital-Allocation-Politik)
     - Den Sektor (typische Margen, Wachstumsraten, SBC-Quoten, Buyback-Quoten)
     - Vergleichbare Firmen (gleicher Sektor/Größe)
     - Historische Pattern (z.B. 'Industrials wachsen typisch 3-5%, Tech 10-15%')
   Daraus leitest du eine PLAUSIBLE Zahl ab. Beispiel: "Airbus FY2026 SBC nicht
   aggregator-listed → SBC bei Aerospace-Industrials typisch 0.3-0.5% von Sales,
   Airbus Sales ~80B → SBC ~280M". QUELLE markiert das ehrlich als
   "KI-Einschätzung: <Begründung>". KONFIDENZ: niedrig.

7. **0 ist gültig NUR wenn**: Firma hat in der Periode dokumentiert nichts
   gezahlt/zurückgekauft/etc. (z.B. "kein Buyback-Programm aktiv" → buyback=0).
   NICHT als Fallback bei Schwierigkeit.

ANTWORT-FORMAT — ABSOLUT VERBINDLICH:
Deine Antwort MUSS mit der Zeile 'WERT:' BEGINNEN. NICHTS davor — kein
Markdown, kein Header, keine Einleitung wie "Hier die Berechnung:". Die
ersten 5 Zeichen deiner Antwort MUESSEN exakt 'WERT:' sein.

Wenn du gerechnet hast (z.B. €2.519 Mio × 1.17 = $2.960 Mio), kommt das
Ergebnis IN DIE WERT-ZEILE als BASE-UNITS-Zahl, nicht als Markdown.
Den Rechenweg darfst du erst danach in BEGRUENDUNG erklären.

KORREKTES FORMAT (genau so):
WERT: 2960000000
EINHEIT: USD
QUELLE: Approximation: Airbus IR FY2025 Dividende €3,20/Aktie × 787,2 Mio Aktien × 1,1752 EUR/USD
QUELLE_URL: https://www.airbus.com/en/investors
ZEITRAUM: FY2026e
KONFIDENZ: niedrig
BEGRUENDUNG: FY2025-Dividende beschlossen am 14.04.2026, Auszahlung 23.04.2026 → faellt in FY2026 Cashflow. Umrechnung mit EUR/USD-Kurs vom Stichtag.

GAAP/NON-GAAP-PFLICHT (nur fuer net_income, ebitda, fcf):
Wenn der gesuchte Wert net_income, ebitda oder fcf ist, MUSST du zusaetzlich
die Adjusted/Non-GAAP/Underlying-Variante liefern, falls vorhanden. Format:

  WERT_ADJUSTED: 20500000000           (Non-GAAP Net Income, in Base-Units)
  QUELLE_ADJUSTED: Visa Q4 2024 Earnings Release S.3
  ADJUSTMENTS: Litigation Reserve +500M, Contingent Consideration +300M

Wenn die Firma KEINEN Adjusted-Wert reportet (z.B. reine GAAP-Reporting ohne
non-GAAP Reconciliation), liefere:

  WERT_ADJUSTED: keine
  ADJUSTMENTS: Firma reportet keine Adjusted/Non-GAAP-Variante.

Fuer ALLE anderen Keys (sbc, buyback, dividends, net_debt, shares_outstanding)
gibt es per Definition keinen Adjusted-Pendant — KEINE WERT_ADJUSTED-Zeile noetig.

FALSCHES FORMAT (NIE so antworten):
"Hier die Berechnung: **Gesamtdividende: €2,519 Mio**" — fehlt WERT:-Zeile am Anfang.
"**WERT: $2.960 Mio**" — Markdown-Sterne UND nicht in Base-Units.
"Ich konnte keine Daten finden" — kein Wert, verboten (siehe oben).

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
    tatsächlich 0 ergibt, nicht als Default)."""
    match = re.search(
        r"WERT:\s*([+-]?[\d.,]+(?:\s*(?:Mrd|Milliarden|Mio|Millionen|billion|million|[BMTK])\.?)?(?:\s*%)?|NICHT[_\s]?GEFUNDEN)",
        text,
        re.IGNORECASE,
    )
    if match:
        raw = match.group(1).strip()
        if re.match(r"nicht.{0,2}gefunden", raw, re.IGNORECASE):
            logger.warning("research: Claude antwortete NICHT_GEFUNDEN trotz Verbot — caller bekommt None")
            return None
        value = _parse_numeric_string(raw)
        if value is None:
            logger.warning("research: WERT '%s' parst nicht — caller bekommt None", raw)
            return None
        return _apply_unit_scale(value, text, raw)

    # Fallback-Parser: Claude hat das WERT-Format ignoriert aber vielleicht eine
    # Zahl im Markdown stehen. Suche nach Resultat/Ergebnis/= Pattern oder dem
    # letzten Bold-Currency-Wert (typisch '**$2.960 Mio.**').
    fallback = _fallback_extract_value(text)
    if fallback is not None:
        logger.info("research: WERT-Pattern fehlte, Fallback-Parser fand %s", fallback)
        return fallback

    preview = (text or "")[:500].replace("\n", " | ")
    logger.warning("research: Claude antwortete ohne WERT-Pattern — caller bekommt None. Antwort-Preview: %s",
                   preview)
    return None


def extract_research_value_adjusted(text: str) -> tuple[Decimal | None, str | None, str | None]:
    """Sucht WERT_ADJUSTED + QUELLE_ADJUSTED + ADJUSTMENTS aus Claude/Gemini Response.
    Returns (value_adjusted_or_None, source_adjusted, adjustments_note).
    Wenn 'keine' / 'none' / leer → (None, None, note).
    """
    m = re.search(r"WERT_ADJUSTED:\s*([^\n]+)", text)
    if not m:
        return None, None, None
    raw = m.group(1).strip()
    note_match = re.search(r"ADJUSTMENTS:\s*([^\n]+)", text)
    source_match = re.search(r"QUELLE_ADJUSTED:\s*([^\n]+)", text)
    note = note_match.group(1).strip() if note_match else None
    source = source_match.group(1).strip() if source_match else None
    if re.match(r"^(keine|none|n/a|\-|null|nicht\s+vorhanden|nicht\s+reportet)\.?$", raw, re.IGNORECASE):
        return None, source, note
    val = _parse_numeric_string(raw)
    if val is None:
        return None, source, note
    val = _apply_unit_scale(val, text, raw)
    return val, source, note


def _fallback_extract_value(text: str) -> Decimal | None:
    """Wenn Claude das WERT:-Format vergisst (z.B. schreibt '**$2.960 Mio.**'),
    versuche aus Markdown/Prose die wahrscheinlichste Zahl zu extrahieren.
    Reihenfolge:
      1. 'Resultat:' / 'Ergebnis:' / 'Total:' Zeile
      2. Letzter '= **$X Mrd/Mio**' Pattern (Berechnungs-Ergebnis)
      3. Letzter Bold-Currency-Wert
    """
    if not text:
        return None
    # 1. Resultat/Ergebnis-Zeile
    for label in (r"Resultat", r"Ergebnis", r"Endergebnis", r"Total", r"Schätzung", r"Schätzung", r"Wert"):
        m = re.search(
            rf"{label}\s*[:=]\s*\*?\*?[\$€£]?\s*([+-]?[\d.,]+\s*(?:Mrd|Milliarden|Mio|Millionen|billion|million|[BMK])\.?)",
            text,
            re.IGNORECASE,
        )
        if m:
            raw = m.group(1).strip()
            v = _parse_numeric_string(raw)
            if v is not None:
                return _apply_unit_scale(v, raw, raw)
    # 2. Letzter '= **value Mrd/Mio**' Pattern
    matches = list(re.finditer(
        r"=\s*\*?\*?[\$€£]?\s*([+-]?[\d.,]+\s*(?:Mrd|Milliarden|Mio|Millionen|billion|million|[BMK])\.?)\*?\*?",
        text,
        re.IGNORECASE,
    ))
    if matches:
        raw = matches[-1].group(1).strip()
        v = _parse_numeric_string(raw)
        if v is not None:
            return _apply_unit_scale(v, raw, raw)
    # 3. Letzter Bold-Currency-Wert (**$X Mio.** / **€Y Mrd.**)
    matches = list(re.finditer(
        r"\*\*\s*[\$€£]?\s*([+-]?[\d.,]+\s*(?:Mrd|Milliarden|Mio|Millionen|billion|million|[BMK])\.?)\s*\*\*",
        text,
        re.IGNORECASE,
    ))
    if matches:
        raw = matches[-1].group(1).strip()
        v = _parse_numeric_string(raw)
        if v is not None:
            return _apply_unit_scale(v, raw, raw)
    return None


# Currency-Keys aus values/currency_keys importieren wuerde Zirkular machen —
# duplizieren als Set für die unit-heuristik unten.
_LIKELY_CURRENCY_KEYS = frozenset({
    "net_income", "fcf", "sbc", "buyback_volume", "dividends",
    "net_debt", "market_cap", "ebitda",
})


def detect_calculation_inconsistency(value: Decimal, content: str) -> str | None:
    """Sucht in Claude-Output nach 'X × Y = Z' / 'X * Y = Z' / 'Resultat: Z'-Pattern
    und prueft ob Z mit dem ausgegebenen WERT konsistent ist.

    Returns: error-message wenn Inkonsistenz >5% gefunden wurde (Caller koennte
    retry triggern), sonst None (alles plausibel).
    """
    if not content or value == 0:
        return None
    try:
        wert = float(value)
    except (ValueError, OverflowError):
        return None
    # Kandidaten-Resultate aus Multiplikations-/Divisions-/Resultat-Patterns
    # extrahieren. Wir suchen nach Zahlen mit Einheits-Suffix (Mio/Mrd/Mio EUR/etc).
    candidates: list[float] = []
    for pat in (
        r"=\s*\*?\*?[\$€£]?\s*([\d.,]+)\s*(Mrd|Milliarden|Mio|Millionen|billion|million|[BMK])\.?",
        r"(?:Resultat|Ergebnis|Endergebnis|Total)\s*[:=]\s*\*?\*?[\$€£]?\s*([\d.,]+)\s*(Mrd|Milliarden|Mio|Millionen|billion|million|[BMK])\.?",
    ):
        for m in re.finditer(pat, content, re.IGNORECASE):
            raw_num = m.group(1).replace(".", "").replace(",", ".") if m.group(1).count(",") == 1 else m.group(1).replace(",", "")
            try:
                num = float(raw_num)
            except ValueError:
                continue
            unit = (m.group(2) or "").lower()
            if unit in ("mrd", "milliarden", "billion", "b"):
                num *= 1_000_000_000
            elif unit in ("mio", "millionen", "million", "m"):
                num *= 1_000_000
            elif unit == "k":
                num *= 1_000
            if num > 1_000_000:  # nur konzern-skalige Zahlen
                candidates.append(num)
    if not candidates:
        return None
    # Best-Match: die nachvollziehbarste Berechnung ist die mit kleinster Diff
    # zu WERT — wenn aber selbst die best-match >5% abweicht, ist die
    # Begruendung inkonsistent.
    diffs = [abs(c - abs(wert)) / max(abs(wert), 1.0) for c in candidates]
    best_diff = min(diffs)
    if best_diff > 0.05:
        # Mindestens ein Result-Kandidat existiert aber alle weichen >5% ab.
        # Das deutet auf inkonsistente Begruendung hin.
        best_candidate = candidates[diffs.index(best_diff)]
        return (
            f"Begruendung enthaelt Berechnung mit Resultat ~{best_candidate:,.0f} aber "
            f"WERT={wert:,.0f} — Abweichung {best_diff*100:.1f}%. Begruendung muss "
            f"rechnerisch zum Output passen."
        )
    return None


def detect_unit_error(key: str, value: Decimal) -> bool:
    """True wenn ein Currency-Key suspicious klein ist (z.B. WERT: 1.45 USD
    statt 1450000000 USD — Claude hat vergessen die Approximation in
    Base-Units zu liefern). Threshold: 1 Million als Untergrenze für
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
    # EBITDA: meist 2-5x Net Income, kann bei extrem-CapEx-lastigen Firmen
    # (Telekom, Versorger) hoeher sein. Range groesszuegig.
    "ebitda": (-2_000_000_000_000, 10_000_000_000_000),
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
        "Aktienrückkaeufe in Cash, jährliches Volumen. Cash Flow Statement → "
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
    "ebitda": (
        "EBITDA (Earnings Before Interest, Tax, Depreciation & Amortization). "
        "Standard-Definition: Operating Income + D&A. Suchorte: Highlights/KPI-"
        "Tabelle, Income Statement (oft als 'EBITDA' direkt ausgewiesen), Notes "
        "zu Segment-Reporting. Bei IFRS-Filern oft als 'EBITDA adjusted' oder "
        "'EBITDA before special items' — nutze die GAAP-/IFRS-Reporting-Zahl, "
        "NICHT die Adjusted/Pro-Forma-Variante. Bei Banken/Versicherern: nicht "
        "anwendbar — Equivalent: Operating Income / Pre-Tax Profit."
    ),
}


_YOY_CAP_KEYS: dict[str, tuple[float, float]] = {
    # (max_growth_factor, max_shrink_factor) für FORWARD-Year-Schätzungen.
    # Stabile Posten: max +50% YoY, max -40%. Buyback/Dividends/Net Debt
    # schwanken stärker — laxere Caps.
    "net_income": (1.5, 0.5),
    "fcf": (1.6, 0.4),
    "sbc": (1.4, 0.6),
    "ebitda": (1.5, 0.5),
    # Buyback: neue Programme koennen 10-30x vom ESOP-Cycling-Vorjahr abweichen
    # (z.B. Adidas FY2025 €43M ESOP-only -> FY2026 €1Mrd neues Programm = 23x).
    # Cap auf 30x damit echte Programme durchgehen, aber 100x+ als Halluzinationen
    # erkannt werden.
    "buyback_volume": (30.0, 0.05),
    # Dividends: Sprünge nach Krisenjahren legitim (z.B. Adidas 2024 €0.70 -> 2026 €2.80)
    "dividends": (5.0, 0.1),
}


def validate_claude_value(
    key: str,
    value: Decimal,
    *,
    prev_fy_val: Decimal | None = None,
    is_forward_year: bool = False,
) -> Decimal | None:
    """Range-Check für Claude-Werte. None wenn ausserhalb plausibler Range
    oder wenn die unit-heuristik anschlaegt (Currency-Wert verdaechtig klein
    -> Claude hat vermutlich Mio/Mrd vergessen).

    prev_fy_val (optional): wenn vorhanden, machen wir mehrere Cross-Checks:
      - Currency-Mismatch via Größenordnungs-Ratio (>100x oder <0.01x → reject).
      - YoY-Cap fuer Forward-Year: pro Key max plausibles Wachstum/Schrumpfen.
        Hard-Reject wenn ueberschritten (Konsens-Sanity).
    """
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
    # Currency/Unit-Cross-Check via FY[N-1]: Wert sollte in Groessenordnung
    # liegen. 100x Diff = wahrscheinlich Currency-Mismatch (INR vs EUR ~80x,
    # JPY vs USD ~150x), 0.01x Diff = umgekehrt. Mind. 1M-Schwelle damit
    # 0-Vergleiche oder Mikro-Werte das nicht triggern.
    if prev_fy_val is not None and key in _LIKELY_CURRENCY_KEYS:
        try:
            prev_abs = abs(float(prev_fy_val))
            curr_abs = abs(fval)
            if prev_abs > 1_000_000 and curr_abs > 1_000_000:
                ratio = max(curr_abs / prev_abs, prev_abs / curr_abs)
                if ratio > 100:
                    logger.warning(
                        "Claude value sanity: %s=%s vs prev_fy=%s ratio %.1fx — "
                        "Currency-Mismatch verdaechtig (z.B. INR statt EUR), dropping",
                        key, value, prev_fy_val, ratio,
                    )
                    return None
            # YoY-Cap fuer Forward-Year: Konsens-Sanity. Per-Key plausible
            # Range fuer YoY-Aenderung. Net Debt ausgenommen (Sign-Swings sind
            # legitim z.B. Net Cash → Net Debt nach grosser Akquisition).
            if is_forward_year and key in _YOY_CAP_KEYS and prev_abs > 1_000_000:
                max_growth, max_shrink = _YOY_CAP_KEYS[key]
                # Vorzeichen-Aware: bei gleichem Vorzeichen pruefen wir die
                # absolute Skalierung. Bei verschiedenen Vorzeichen
                # (z.B. NI von Verlust → Profit) gilt der Cap nicht.
                if (fval * float(prev_fy_val)) > 0:
                    growth_factor = curr_abs / prev_abs
                    if growth_factor > max_growth:
                        logger.warning(
                            "Claude YoY-Cap %s: forward=%s vs prev_fy=%s growth=%.2fx "
                            "(max %.2fx) — vermutlich zu optimistisch / Konsens-Drift, "
                            "dropping fuer Retry",
                            key, value, prev_fy_val, growth_factor, max_growth,
                        )
                        return None
                    if growth_factor < max_shrink:
                        logger.warning(
                            "Claude YoY-Cap %s: forward=%s vs prev_fy=%s shrink=%.2fx "
                            "(min %.2fx) — vermutlich zu pessimistisch, dropping",
                            key, value, prev_fy_val, growth_factor, max_shrink,
                        )
                        return None
        except (ValueError, OverflowError):
            pass
    if fval == 0:
        if key == "sbc":
            # SBC=0 ist bei boersennotierten Firmen praktisch nie korrekt
            # (selbst Versicherer/Banken haben Personnel-share-based payments).
            # Reject damit der Retry-Mechanismus eine echte Schaetzung erzwingt.
            logger.warning(
                "Claude value: sbc=0 — bei boersennotierten Firmen praktisch nie korrekt. "
                "Reject damit Retry eine echte Approximation erzwingt."
            )
            return None
        if key in ("buyback_volume", "dividends", "net_income", "fcf"):
            logger.info(
                "Claude value: %s=0 — kann legitim sein (Firma zahlt z.B. keine Dividende) "
                "oder ein Default-Fallback. Source-Name pruefen.", key,
            )
    return value


_DATE_PATTERN = re.compile(r"\b(\d{1,2}\.\d{1,2}\.\d{4})\b|\b(?:\d{4}-\d{2}-\d{2})\b")


def _has_specific_url(url: str | None) -> bool:
    """Heuristik: True wenn URL eine spezifische Sub-Page referenziert (mehr als
    nur Hostname/IR-Landingpage). Wird für Halluzinations-Detection genutzt:
    Datums-Referenzen ohne spezifische URL = unverifizierbar."""
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        path = (p.path or "").rstrip("/")
        # Nur Hostname oder triviale Pfade wie /investors, /ir → unspezifisch
        if not path or len(path.split("/")) <= 2:
            return False
        if path.lower() in ("/investors", "/ir", "/de/investors", "/en/investors", "/investor-relations"):
            return False
        return True
    except Exception:
        return False


def research_value(
    company_name: str,
    ticker: str,
    value_label: str,
    currency: str,
    period_type: str = "FY",
    period_year: int | None = None,
    value_key: str | None = None,
    prev_fy_val: Decimal | None = None,
) -> tuple[Decimal | None, str | None, str | None, str | None, str | None]:
    """Web-Recherche für eine einzelne Kennzahl.
    Returns (value, source_name, source_url, user_prompt, assistant_response).

    prev_fy_val (optional): wird fuer YoY-Cap-Sanity-Check und Konsens-Sanity-Hint
    im Prompt genutzt. Reduziert deutlich Konsens-Optimismus und Halluzinations-
    Drift bei Forward-Forecasts.
    """
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
            f"WERT: 0 ist NUR gültig wenn deine Approximation echt 0 ergibt "
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
            f"(z.B. 'Personnel Expenses × 0.5%' für SBC bei Versicherer, "
            f"'Total Borrowings - Cash' für Net Debt). RECHNE die konkrete Zahl, "
            f"liefere sie als WERT.\n"
            f"  4. Approximation: FY{period_year}-Wert × Quartal-Anteil "
            f"(0.25 für einzelnes Quartal, 0.50/0.75 für YTD) — als Zahl in WERT.\n"
            f"WERT: 0 nur wenn deine Approximation tatsächlich 0 ergibt. NIE als Fallback."
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
            f"  3. Equivalent-Konzept für Sektor-Mismatch (siehe System-Prompt) — "
            f"AUSRECHNEN, nicht 0.\n"
            f"WERT: 0 nur wenn deine Approximation tatsächlich 0 ergibt. NIE als Fallback."
        )

    # Konsens-Sanity-Hint: prev_fy_val als Anker im Prompt benennen damit Claude
    # weiss welcher YoY-Cap gilt. Reduziert Konsens-Drift bei Forward-Forecasts.
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
        f"Unternehmen: {company_name} ({ticker}, {currency})\n"
        f"Gesuchte Kennzahl: {value_label}\n"
        f"Zeitraum: {period_str}\n\n"
        f"WAEHRUNG-PFLICHT: Liefere den Wert in {currency} (Original-Reporting-"
        f"Währung der Firma). KEINE Umrechnung in USD/EUR/anderes Currency. "
        f"Wenn die Quelle in einer anderen Währung berichtet, rechne KORREKT um "
        f"in {currency} und nenne den verwendeten Kurs in BEGRUENDUNG.\n\n"
        f"Wichtig: Liefere AUSSCHLIESSLICH den Wert für {period_str}.{historical_constraint} "
        f"{not_found_clause}\n\n"
        f"Nutze das Web-Search-Tool um die IR-Seite des Unternehmens, "
        f"Annual-Report-PDFs und SEC-Filings aktiv zu durchsuchen. "
        f"Verlasse dich NICHT nur auf dein Gedächtnis."
        f"{forward_block}"
        f"{hint_block}"
        f"{anchor_block}"
    )

    def _do_call(messages: list[dict]) -> str:
        # Explizites Retry-Backoff bei 429/529/Overloaded — Anthropic-API hat
        # bei Lastspitzen oft 1-2 transiente Rate-Limit-Antworten, die wir mit
        # exponential-backoff (1s, 4s, 16s) abfangen statt silent zu failen.
        import time
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                response = claude_limiter.call(lambda: client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=2048,
                    system=[{"type": "text", "text": RESEARCH_PROMPT, "cache_control": {"type": "ephemeral"}}],
                    tools=[WEB_SEARCH_TOOL],
                    messages=messages,
                ))
                return _collect_text(response)
            except Exception as e:
                msg = str(e).lower()
                is_retriable = any(s in msg for s in ("429", "529", "overloaded", "rate", "timeout"))
                if not is_retriable or attempt == max_attempts - 1:
                    raise
                wait = 4 ** attempt  # 1s, 4s, 16s
                logger.warning("research API attempt %d failed (%s) — retry in %ds",
                               attempt + 1, str(e)[:80], wait)
                time.sleep(wait)
        return ""

    try:
        client = get_client()
        content = _do_call([{"role": "user", "content": user_prompt}])
        value = extract_research_value(content)
        sanity_failed_reason: str | None = None

        # Sanity-Check: Range/Unit + neu YoY-Cap (Konsens-Sanity) + Self-
        # Consistency (BEGRUENDUNG vs WERT). Reject zwingt Retry.
        if value is not None and value_key:
            validated = validate_claude_value(
                value_key, value, prev_fy_val=prev_fy_val, is_forward_year=is_forward,
            )
            if validated is None:
                if prev_fy_val is not None and value_key in _YOY_CAP_KEYS:
                    sanity_failed_reason = (
                        f"Vorheriger Wert {value} wurde verworfen — entweder Sanity-"
                        f"Range, Unit-Verdacht (Mio/Mrd-Verwechslung) oder YoY-Cap "
                        f"(zu starke Abweichung von FY{period_year - 1 if period_year else '?'}-Anker {prev_fy_val})"
                    )
                else:
                    sanity_failed_reason = (
                        f"Vorheriger Wert {value} wurde wegen Sanity-Range oder "
                        f"Unit-Verdacht (Mio/Mrd-Verwechslung?) verworfen"
                    )
                value = None
            else:
                # Self-Consistency: BEGRUENDUNG-Berechnung muss zum WERT passen.
                inc = detect_calculation_inconsistency(value, content)
                if inc is not None:
                    logger.warning("research %s/%s: %s — retry mit Korrektur-Hinweis",
                                   ticker, value_key or "?", inc)
                    sanity_failed_reason = inc
                    value = None
                # URL-Halluzinations-Check: Datums-Referenz in QUELLE ohne
                # spezifische QUELLE_URL → wahrscheinlich halluziniert. Wir
                # markieren das im Source-Name aber rejecten nicht hard
                # (User entscheidet beim Reviewen).
                # (Source/URL erst später extrahiert — Marker wird unten gesetzt.)

        # Retry-Stufe: Wenn Claude beim ersten Versuch keine Zahl liefert
        # (oder die Zahl Sanity-Check nicht besteht), zwingen wir ihn als
        # Equity-Analyst zur Experten-Einschätzung. Eine einzige Wiederholung.
        if value is None:
            logger.info("Web-Recherche %s/%s: erster Versuch lieferte keine valide Zahl — "
                        "retry mit Experten-Einschätzungs-Aufforderung (%s)",
                        ticker, value_key or "?", sanity_failed_reason or "extract=None")
            sanity_hint = ""
            if sanity_failed_reason:
                sanity_hint = (
                    f"\n\nVorsicht: dein letzter Wert wurde verworfen. Grund: "
                    f"{sanity_failed_reason}. Korrigiere das beim Retry — pruefe "
                    f"Unit (Base-Units, nicht Mio/Mrd), Konsistenz zwischen "
                    f"BEGRUENDUNG-Rechnung und WERT, und ob deine YoY-Aenderung "
                    f"plausibel ist (siehe Anker im Original-Prompt)."
                )
            retry_prompt = (
                f"Du hast eben keine valide numerische Antwort geliefert. Das ist "
                f"nicht akzeptabel — du bist Senior Equity Analyst, du hast IMMER "
                f"eine fundierte Einschätzung.\n\n"
                f"Gib JETZT eine plausible Schätzung für {value_label} bei "
                f"{company_name} ({ticker}) im Zeitraum {period_str} basierend auf "
                f"deinem Wissen über:\n"
                f"  - Sektor-typische Werte (Margen, Wachstumsraten, "
                f"Capital-Allocation-Quoten)\n"
                f"  - Vergleichbare Firmen ähnlicher Größe\n"
                f"  - Historische Werte des Unternehmens\n"
                f"  - Aktueller Markt-Konsens / News\n\n"
                f"Liefere die Zahl in {currency} in vollen BASE-UNITS (z.B. "
                f"1450000000 für 1,45 Mrd, NICHT 1.45 oder 1450). "
                f"QUELLE = 'KI-Einschätzung: <dein Schätzweg in 1 Satz>'. "
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
            # Auch hier voller Sanity-Check (Range/Unit + YoY-Cap + Self-Consistency).
            if value is not None and value_key:
                validated = validate_claude_value(
                    value_key, value, prev_fy_val=prev_fy_val, is_forward_year=is_forward,
                )
                if validated is None:
                    logger.warning("Web-Recherche %s/%s: retry-Wert %s ebenfalls Sanity-Fail",
                                   ticker, value_key, value)
                    value = None
                else:
                    inc = detect_calculation_inconsistency(value, content_retry)
                    if inc is not None:
                        logger.warning("Web-Recherche %s/%s: retry-Wert %s self-consistency-fail (%s) — accept aber markiere",
                                       ticker, value_key, value, inc)
                        # Beim Retry akzeptieren wir die Inkonsistenz statt endlos
                        # zu loopen, aber loggen + Caller koennte das im Source vermerken.
            if value is not None:
                content = content_retry  # nutze die Retry-Antwort für source/url
                logger.info("Web-Recherche %s/%s: retry erfolgreich, Wert=%s",
                            ticker, value_key or "?", value)

        if value is None:
            return None, None, None, user_prompt, content
        source_match = re.search(r"QUELLE:\s*(.+)", content)
        source = source_match.group(1).strip() if source_match else "Claude-Recherche"
        url_match = re.search(r"QUELLE_URL:\s*(https?://\S+)", content)
        source_url = url_match.group(1).strip() if url_match else None
        # URL-Halluzinations-Marker: Wenn QUELLE konkretes Datum referenziert
        # aber QUELLE_URL fehlt oder unspezifisch ist (nur Hostname/IR-Landingpage),
        # markiere das im Source-Name. Halluzinierte Datumsangaben sind die
        # haeufigste Failure-Mode bei Claude-Web-Recherche.
        if _DATE_PATTERN.search(source) and not _has_specific_url(source_url):
            source = f"⚠ unverifizierte Datumsangabe ohne spezifische URL — {source}"
            logger.info("Web-Recherche %s/%s: Datums-Referenz ohne spezifische URL markiert",
                        ticker, value_key or "?")
        # source ohne Praefix zurückgeben — Caller (Web-Guidance / Research-
        # Endpoint / Auto-Fallback) entscheidet das passende Praefix.
        return value, source, source_url, user_prompt, content
    except Exception as e:
        logger.warning("Claude research failed for %s/%s: %s", ticker, value_label, e)
        return None, None, None, user_prompt, None
