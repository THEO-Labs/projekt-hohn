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
    "3. Analysten-Konsens (Bloomberg Analyst Estimates, Factset, "
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

C. **Konsistenz Output ↔ Begründung**: Wenn du in BEGRUENDUNG rechnest "EPS €X ×
   Aktien Y = Z" oder "FY[N-1] × Wachstumsrate = Z", dann MUSS dein WERT das
   Z sein (±2% Rundung erlaubt). Inkonsistenz = ungültige Antwort.

D. **Quellen-URL-Pflicht für Datumsangaben**: Wenn du in QUELLE ein konkretes
   Datum referenzierst (z.B. 'IR-Press-Release vom 04.03.2026' oder 'Ad-hoc-
   Mitteilung vom 29.01.2026'), MUSS QUELLE_URL die spezifische URL dieser
   Press-Release sein — NICHT nur die generelle IR-Seite. Wenn du keine
   spezifische URL nennen kannst, lass das konkrete Datum weg und schreib
   'lt. IR/Annual Report' allgemein. Halluzinierte Daten ohne URL = Disqualifikation.

E. **Konsens-Anchoring-Pflicht (PFLICHT bei Forward-Year/Estimate-Werten)**:
   Bei Forward-Year-Schätzungen (FY[N]e wo N >= aktuelles Jahr) MUSST du in QUELLE
   die Analyst-Konsens-Range explizit zitieren. Recherchiere aktiv und nenne:
     - Welcher Konsens-Aggregator zitiert wird: Bloomberg-Konsens, Refinitiv I/B/E/S,
       S&P Capital IQ, Visible Alpha, Reuters-Konsens, Yahoo Finance Analyst
       Estimates, oder Aggregator-Plattformen (StockAnalysis, Macrotrends,
       SimplyWallSt, Seeking-Alpha).
     - Anzahl der Analysten im Konsens (wenn auffindbar, z.B. "32 Analysten").
     - Range: Low - Median - High wenn verfügbar, sonst nur Median/Konsens.
   FORMAT in QUELLE:
     "Bloomberg-Konsens FY2026 (32 Analysten, Range $48-54B, Median $51B) +
      eigene IR-Verifikation: ..."
     ODER bei nur einem Aggregator:
     "Yahoo Finance Konsens FY2026 (15 Analysten, Mean $51.3B)"
     ODER wenn kein Konsens auffindbar:
     "KI-Einschätzung basierend auf FY[N-1]-Anker × Wachstumsrate (kein
      Analyst-Konsens öffentlich auffindbar)"
   Diese Angaben werden im UI direkt sichtbar — der User braucht sie um Mandanten
   gegenüber zu rechtfertigen warum unser Wert vom Kunden-Modell abweicht.
   Wenn du keine Konsens-Range zitierst obwohl eine existiert: ungueltige Antwort.

F. **Quartals-Aufschluesselungs-Pflicht (PFLICHT bei Forward-Year-Werten)**:
   Bei Forward-Year-Werten MUSST du IMMER die bisher veroeffentlichten Quartale
   AUSDRUECKLICH in QUELLE aufschluesseln, plus die Forecast-Quartale die zur
   FY-Summe fuehren.

   FORMAT — pflichtgemaess am Ende der QUELLE-Zeile, KOMPAKT (Werte mit Unit,
   minimaler Per-Quartal-Kontext in flachen Klammern, KEINE verschachtelten
   Parens):
     "| Quartals-Aufschluesselung: Q1 $8.0B (actual lt. 10-Q), Q2 $10.3B
      (actual lt. 10-Q), Q3e $15.5B (Konsens), Q4e $17.5B (Konsens) =
      FY-Total $51.3B"

   ⚠️ ABSOLUT KRITISCH — UNIT-PFLICHT:
   Jeder Q-Wert MUSS mit Unit angegeben werden (B / Mrd / Mio / M). Eine reine
   Zahl ohne Unit ist UNGUELTIG ("Q1 $8" ist FALSCH — schreib "Q1 $8.0B").
   Werte werden im Frontend als Tabelle dargestellt; ohne Unit wird Q1 als $8
   statt $8.000.000.000 angezeigt = visuelle Bug.

   ⚠️ KEINE VERSCHACHTELTEN KLAMMERN im Quartals-Block:
   Pro Quartal MAX. EIN Klammer-Paar. NICHT: "Q1 $8B (actual (10-Q-Page 5))"
   sondern: "Q1 $8B (actual lt. 10-Q S.5)". Verschachtelte Parens brechen das
   Frontend-Parsing.

   Bei Werten die NICHT cumulative sind (Net Debt, Shares Outstanding): statt
   Q-Aufschluesselung Quartalsstaende auflisten ("Q1-Ende $X, Q2-Ende $Y").

G. **EBITDA GAAP-Adj-Bruecke-Pflicht (nur fuer ebitda)**:
   Bei Forward-Year-EBITDA-Werten gilt eine PRAEZISE Konvertierungsregel
   zwischen Adjusted EBITDA (Non-GAAP) und GAAP EBITDA:

   GAAP EBITDA = EBIT + D&A
   Adjusted EBITDA = EBIT + D&A + SBC + Restrukturierung + One-Time-Items

   → Diff (Adj − GAAP) = SBC + One-Time-Items (KEIN D&A-Subtract!)

   ⚠️ HAEUFIGER METHODISCHER FEHLER (NICHT MACHEN):
   "GAAP EBITDA = Adj EBITDA − SBC − VMware-Intangible-Amortization"
   → FALSCH! Akquisitions-Intangible-Amortization (VMware bei Broadcom,
   Activision bei Microsoft, Snap bei Anthropic etc.) IST D&A → wird in
   BEIDEN EBITDA-Varianten zurueckaddiert. Sie zu subtrahieren ist
   DOPPEL-ZAEHLUNG die GAAP-EBITDA um 10-20 Mrd. unterschaetzt.

   KORREKT bei Broadcom-aehnlichen Akquisitions-Cases:
     Adj EBITDA $70B
     − SBC ~$8.5B
     − Restrukturierung ~$1B
     = GAAP EBITDA ~$60.5B
   (NICHT zusaetzlich − VMware-Amort $14B; die ist in D&A drin)

   Bei US-Filern ohne dezidierten GAAP-EBITDA-Disclosure (Standard-Case):
   Konstruiere GAAP EBITDA aus 10-Q Income-Statement:
     EBIT (Operating Income) + D&A (Cash-Flow-Statement) = GAAP EBITDA
   Dann WERT = GAAP, WERT_ADJUSTED = Non-GAAP-Disclosure der Firma.

H. **Forward-Q4-Methodik (PFLICHT bei AI/Beat-and-Raise-Firmen)**:
   Bei Forward-Year-Werten gibt es zwei Methoden Q4 zu schaetzen:

   Methode A — Top-Down (FY-Konsens-Subtraktion):
     Q4 = FY-Konsens − H1-Actuals − Q3-Guidance
   Methode B — Bottom-Up (Sequential-Growth):
     Q4 = Q3-Guidance x (1 + typische Q3->Q4-Wachstumsrate)

   ⚠️ WICHTIG — Beide Methoden vergleichen:
   Wenn Methode B > Methode A um >20% UND die Firma im Beat-and-Raise-Pattern
   ist (z.B. Broadcom AI, NVDA AI, AMD AI-Acceleration, Apple Services), DANN
   ist FY-Konsens STALE (= Aggregatoren haben noch nicht nachgezogen) und
   Methode B (Sequential) ist robuster.

   Indikatoren fuer "FY-Konsens stale":
     - Q3-Guidance des Managements >FY-Konsens/4 (= Q3 schon ueber Pro-Rata)
     - Recent Q-Beats > 10% vs prior Konsens
     - Hyperscaler-/AI-Capex-Cycle mit jaehrlicher Beschleunigung

   In dem Fall: nimm Q4 = Q3-Guidance × 1.05 bis 1.15 (Q4-Bias). Begruende in
   QUELLE explizit: "FY-Konsens stale (SimplyWallSt $94.7B vs implizit
   $103B bei Q4-Sequential-Growth) — Methode B als robuster gewaehlt."

   Bei stabilen Reifegrad-Firmen (keine Beat-and-Raise-Pattern) ist Methode A
   (FY-Konsens-Anchoring) primaer.

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
QUELLE: Bloomberg-Konsens FY2026 (28 Analysten, Range $2.8B-$3.1B, Median $2.95B) + Airbus IR-Press-Release 14.04.2026 | Quartals-Aufschluesselung: Q1 0$ (no payment), Q2 $2.96B (April-Auszahlung gesamte FY-Dividende), Q3 0$, Q4 0$ = FY-Total $2.96B (einmal jaehrlich)
QUELLE_URL: https://www.airbus.com/en/investors
ZEITRAUM: FY2026e
KONFIDENZ: mittel
BEGRUENDUNG: Bloomberg-Konsens-Median liegt bei $2.95B, eigene IR-Verifikation bestaetigt: FY2025-Dividende €3,20/Aktie × 787,2 Mio Aktien × 1,1752 EUR/USD = $2.96B. Auszahlung 23.04.2026 faellt in FY2026 Cashflow.

KORREKTES FORMAT bei Forward-Year ohne expliziten Konsens-Aggregator:
WERT: 51279000000
EINHEIT: USD
QUELLE: Wall-Street-Konsens FY2026 FCF (Refinitiv I/B/E/S 30+ Analysten, Range $46-56B, Median $51B) — eigene Validation: H1 2026 actual $18.3B × Run-Rate-Annualisierung mit Q4-Bias | Quartals-Aufschluesselung: Q1 $8.0B (actual lt. 10-Q), Q2 $10.3B (actual lt. 10-Q), Q3e $15.5B (Konsens, +50% sequential durch Q4-Bias), Q4e $17.5B (Konsens) = FY-Total $51.3B
QUELLE_URL: https://www.broadcom.com/company/news/financial-releases
ZEITRAUM: FY2026e
KONFIDENZ: mittel

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

⚠️ ABSOLUT KRITISCHE FALLE — EPS×Aktien-Konversion bei net_income:
Wenn du einen EPS-Konsens-Wert findest (Yahoo Finance Analyst Estimates,
MarketBeat, SimplyWallSt, Seeking-Alpha "Earnings Estimates") und EPS x Aktien
rechnest um NI zu approximieren, PRUEFE ZWINGEND ob der zitierte EPS GAAP
oder Non-GAAP ist. Aggregatoren publizieren PRIMAER Non-GAAP-EPS (Adjusted/
Diluted Pro-Forma):
  - Yahoo Finance "Earnings Estimate": Non-GAAP-EPS
  - MarketBeat "Next year EPS": Non-GAAP-EPS
  - StockAnalysis "Forward EPS": meist Non-GAAP
  - GAAP-EPS gibt es nur direkt in 10-Q/10-K oder 8-K-Press-Release

REGEL:
  Non-GAAP-EPS x Shares = ADJUSTED NI -> gehoert in WERT_ADJUSTED, NICHT WERT.
  Nutze KEINEN Non-GAAP-EPS x Shares fuer WERT (= Reported/GAAP-NI), das ist
  KONZEPT-FEHLER und produziert eine total falsche Zahl (typisch 30-50%
  ueberhoehte NI weil SBC + Amortisation Add-Backs reingerechnet werden).

KORREKT bei net_income wenn nur Non-GAAP-EPS-Konsens auffindbar:
  Option A: WERT = approximierter GAAP-NI (= Non-GAAP-NI x 0.7-0.8) mit
            BEGRUENDUNG die das erklaert.
  Option B: WERT = letzter Q-Run-Rate-Annualisierung aus GAAP-Quartalswerten
            (Q1+Q2 actual + Q3e+Q4e mit historischer Margin).
  WERT_ADJUSTED = Non-GAAP-EPS x Shares (saubere Konversion).

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
    """Sucht WERT_ADJUSTED + QUELLE_ADJUSTED + ADJUSTMENTS aus Claude Response.
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
    """Self-Consistency-Check deaktiviert (Kunden-Anforderung).
    Funktion bleibt als Pass-Through erhalten, damit Caller-Signatur unveraendert ist."""
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
    """Sanity-Checks deaktiviert (Kunden-Anforderung): keine Range/Unit/YoY-Cap/
    Currency-Cross-Rejects mehr. Funktion bleibt als Pass-Through erhalten,
    damit Caller-Signatur unveraendert ist."""
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
    q_actuals: dict[str, Decimal] | None = None,
) -> tuple[Decimal | None, str | None, str | None, str | None, str | None]:
    """Web-Recherche für eine einzelne Kennzahl.
    Returns (value, source_name, source_url, user_prompt, assistant_response).

    prev_fy_val (optional): FY[N-1]-Anker fuer Currency-Cross-Check.

    q_actuals (optional): bereits in DB gespeicherte Q-Actuals (aus 10-Q-PDFs)
    fuer das target FY. Werden als PFLICHT-Anker in den Prompt injiziert
    damit Claude die Q-Aufschluesselung nicht falsch herleitet.
    """
    is_forward = _is_forward_year(period_year)
    is_quarter = period_type in ("Q1", "Q2", "Q3", "Q4")
    # Wenn q_actuals fuer andere Q vorliegen, sind wir in der Per-Q-Aggregation:
    # dann ist STANDALONE-Q strikt Pflicht (sonst wuerde FY-Sum doppelt zaehlen).
    is_per_q_aggregation = bool(q_actuals) and is_quarter
    if period_type == "FY" and period_year:
        marker = "e" if is_forward else ""
        period_str = f"Geschäftsjahr {period_year}{marker} (FY{period_year}{marker})"
    elif is_quarter and period_year:
        if is_per_q_aggregation:
            period_str = (
                f"{period_type} {period_year} STANDALONE (NUR das einzelne Quartal — "
                f"KEIN YTD-Kumulativ, KEIN FY-Total. Wenn die Quelle 9M-YTD oder FY-Total "
                f"ausweist, RECHNE den Standalone-Q-Wert aus durch Subtraktion der anderen Q)"
            )
        else:
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
        if is_per_q_aggregation:
            historical_constraint = (
                f" Ziel ist STRIKT der STANDALONE-{period_type}-Wert (nur das einzelne "
                f"Quartal). KEIN kumulativer Wert, KEIN FY-Total."
            )
            not_found_clause = (
                "PFLICHT: Liefere den STANDALONE-Q-Wert (NUR das einzelne Quartal). Reihenfolge:\n"
                f"  1. Konsens-Schaetzung NUR fuer {period_type} {period_year} (Visible Alpha, "
                f"StreetAccount, MarketScreener Q-Range).\n"
                f"  2. Wenn Quelle YTD-kumulativ ausweist: RECHNE Q-Wert = YTD - sum(andere Q "
                f"actuals aus PFLICHT-ANKER). Begruende den Rechenschritt.\n"
                f"  3. Sequentielles Wachstum vom letzten verfuegbaren Actual-Q "
                f"(Q3-Actual × (1 + sequentielle Wachstumsrate)).\n"
                f"WERT: NUR die {period_type}-Standalone-Zahl. Wenn du FY-Total oder "
                f"YTD-Kumulativ lieferst, ist die Antwort FALSCH. Plausibilitaets-Check: "
                f"dein Q-Wert sollte in der Groessenordnung der anderen Q-Actuals liegen, "
                f"nicht 4x so gross."
            )
        else:
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

    # YoY-Korridor-Hint im Prompt entfernt (Sanity-Checks deaktiviert).
    anchor_block = ""

    # Q-Actuals-Anker-Block: bereits in DB gespeicherte Q-Actuals aus 10-Q-PDFs
    # werden als PFLICHT-Anker injiziert. Verhindert dass Claude die Q-Aufschluesselung
    # selbst aus Web-Recherche herleitet und dabei widersprechende Werte liefert.
    # Greift sowohl fuer FY-Forecasts als auch fuer Per-Q-Calls (= Estimate
    # eines einzelnen fehlenden Quartals mit Kontext der anderen 3).
    q_actuals_block = ""
    if q_actuals and is_forward and period_type in ("FY", "Q1", "Q2", "Q3", "Q4"):
        actuals_lines = []
        for q in ("Q1", "Q2", "Q3", "Q4"):
            v = q_actuals.get(q)
            if v is not None:
                actuals_lines.append(f"  {q} FY{period_year} actual = {float(v):,.0f} {currency}")
        if actuals_lines:
            if period_type == "FY":
                instruction = (
                    "Nutze sie EXAKT in der Quartals-Aufschluesselung. Leite die Werte "
                    "NICHT aus Web-Recherche oder Arithmetik ab — die Web-Werte oder "
                    "deine Ableitungen wuerden den verifizierten 10-Q-Werten widersprechen. "
                    "Schaetze NUR das/die fehlende(n) Quartal(e).\n"
                    "FY-Total = Summe (Q1 + Q2 + Q3 + Q4). Konsistenz-Pflicht: WERT = FY-Total."
                )
            else:
                instruction = (
                    f"Du schaetzt JETZT NUR {period_type} FY{period_year} STANDALONE — "
                    f"NUR das einzelne Quartal, KEIN FY-Total, KEIN YTD-Kumulativ. "
                    "Die anderen Quartale oben sind VERIFIZIERTE Actuals und liefern "
                    "Run-Rate-Trend/Margin/Saisonalitaet fuer deine Schaetzung.\n"
                    f"REGEL: Dein WERT muss in derselben Groessenordnung liegen wie die "
                    f"Q-Actuals oben (typisch 0.7x bis 1.5x). Wenn dein Wert > 2x das "
                    f"groesste der oben gelisteten Q-Actuals, hast du wahrscheinlich "
                    f"FY-Total oder YTD-Kumulativ geliefert — KORRIGIERE.\n"
                    "Begruende deine Zahl konkret aus dem Q-Trend (z.B. 'Q1->Q2->Q3 "
                    f"zeigt +X% sequentielles Wachstum, daher {period_type}e ≈ Q3 × (1+X%)')."
                )
            q_actuals_block = (
                f"\n\nPFLICHT-ANKER QUARTALS-ACTUALS (aus unserer 10-Q-Extraktion, autoritativ):\n"
                + "\n".join(actuals_lines)
                + "\n\n" + instruction
            )

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
        f"{q_actuals_block}"
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
