"""Berichtete Nicht-US-Fundamentals: EIN Claude-Call pro Statement-Gruppe.

Nicht-US-Firmen (DAX/IFRS, kein EDGAR) haben kein Quartals-XBRL — die
berichteten Perioden kommen aus IR-Berichten (Q1-Mitteilung, H1, 9M,
Geschaeftsbericht). Statt ~19 Two-Stage-Paaren pro Refresh (Extractor +
Verifier je Key) fragt dieses Modul pro Firma+Jahr GENAU DREI Calls ab —
einen je Statement-Gruppe, weil alle Werte einer Gruppe in denselben
Dokumenten stehen und ein Call sie konsistent aus einer Quelle je
Periode ziehen kann:

  1. GuV:       revenue, net_income, eps_diluted, ebitda
                (+ adjusted-Sidecars net_income/eps, soweit berichtet)
  2. Cashflow:  operating_cash_flow, capex, sbc, dividends, buyback_volume
  3. Bilanz:    cash_and_equivalents, st_investments, st_debt, lt_debt

fcf und net_debt werden bewusst NICHT abgefragt — sie sind berechnet
(consistency.derive_missing_fcf bzw. derive_net_debt_from_components).

Prompt-Stil wie guidance_estimates: natuerliche Chat-Frage, Websuche,
temperature 0. Halbjahresberichter (viele DAX-Firmen berichten H1 statt
Q2) bekommen das Rechenprotokoll Q2 = H1 - Q1 bzw. Q3 = 9M - H1 in den
Prompt; abgeleitete Quartale markiert das Modell im Feld derived_from.

Der Prompt enthaelt nur die Quellen-/Waehrungsregeln (Originalberichte
statt Finanzportale, Berichtswaehrung) — die eigentliche Verteidigung
sind die deterministischen Code-Gates (Muster guidance_estimates):
  1. Einheiten-Check: Absolutwerte >= 1 Mio (ausser Per-Share-Keys),
  2. Vorjahresband 40-160% gegen das Vorjahres-Ist DERSELBEN Periode
     (fehlt es: uebersprungen; Sign-Flip/Turnaround erlaubt). Ausnahme
     net_income/eps_diluted: ist das Paar intern konsistent
     (NI ~ EPS x SNAPSHOT-Aktien, _internally_consistent), akzeptiert
     das Band auch grosse Spruenge — SAPs realer 2024->2025-
     Restrukturierungs-Turnaround (+134%) wurde sonst verworfen,
  3. Spur-Plausibilitaet bei Paaren: reported vs adjusted duerfen max.
     150% auseinanderliegen (Non-IFRS darf UNTER reported liegen — es
     schliesst auch Einmal-Gewinne aus, SAP-Muster; nur klare
     Spur-Verwechslungen werden verworfen). Ist die GAAP-Seite intern
     konsistent, fliegt NUR der Sidecar — ein Muell-Sidecar (non-IFRS-
     Betriebsgewinn statt NI) riss sonst den korrekten IFRS-Wert mit,
  4. qsum-Enforcement: FY + alle 4 Quartale geliefert und Summe > 1%
     daneben -> Quartale verwerfen, FY behalten, loggen,
  5. Yahoo-Cross-Check (Kundenentscheid: der Marktdaten-Feed schreibt
     fuer Nicht-US keine Werte mehr, er gated nur noch): existiert eine
     Yahoo-Referenz und weicht der Recherche-Wert >35% ab, wird er
     verworfen; ohne Referenz kein Urteil (_apply_yahoo_gate),
  6. Spalten-Gates (SAP-Abnahme-Lektion, _apply_column_gates): jedes
     Entry traegt column_label (woertliche Spaltenueberschrift) und
     period_end_date (Stichtag der Spalte). Verworfen wird: (i)
     period_end_date passt nicht zum Zielperioden-Ende (±21 Tage;
     fehlend bei Stufe-2-Dokumenten -> verwerfen, Stufe 1 lenient),
     (ii) column_label nennt eine fremde Jahreszahl (Vorjahresspalten-
     Falle: adjusted-NI-Serie 2025 war die FY2024-Spalte), (iii)
     constant-currency-Marker (cc/waehrungsbereinigt; revenue FY2025
     38.070 war der cc-Wert) bzw. non-IFRS-Marker fuer die IFRS-
     Zielspur, (iv) Kumulativ-Marker (H1/6M/9M/YTD) fuer ein
     Einzelquartal ohne derived_from (H1-Buybacks als Q2 gebucht).
     Sidecars durchlaufen dieselben Checks,
  7. Portal-Blocklist (_apply_source_gate): GuruFocus/stockanalysis/
     macrotrends/wisesheets sind keine gueltige Quelle (USD-
     Drittquellen-Leck: capex Q1-26 kam von GuruFocus in USD) —
     Werte mit Portal-URL werden verworfen, Portal-URLs sind auch
     keine Stufe-2-Dokumentkandidaten,
  8. Bilanz-Stichtags-Gates (_apply_balance_instant_gates, Siemens-
     Cash-Klasse: 31.12.-Kalenderwerte trotz FY-Ende 30.09., falsch
     etikettiertes period_end): (a) Q4- und FY-Wert derselben
     Bilanzperiode muessen identisch sein (gleicher Stichtag) — >1%
     Abweichung verwirft BEIDE ('Q4/FY-Stichtags-Widerspruch');
     (b) identischer Wert (<0,1%) in zwei Perioden mit
     UNTERSCHIEDLICHEM Zielstichtag ist ein Spaltenverrutscher — die
     spaetere Periode (weiter vom Zielstichtag entfernt) wird
     verworfen. Der Prompt nennt bei vom Kalenderjahr abweichendem FY
     explizit den Stichtag ('NICHT der 31.12.'),
  9. Attributable-Gate (_apply_attributable_gate, SAP/Siemens-Muster:
     Konzern-PAT statt attributable NI, +2,2%/+8,1% NCI): EPS ist auf
     attributable gerechnet — liefert der Lauf net_income UND
     eps_diluted derselben Periode und existiert ein SNAPSHOT
     shares_outstanding, verwirft |NI/(EPS x Aktien) - 1| > 6% das
     net_income (EPS bleibt). Toleranz 6% deckt Weighted-vs-Snapshot-
     Drift, faengt 8%-NCI.

FY-only-Backfill (periods=('FY',), Jahr N): Frische Websuche lieferte
fuer Backfill-Jahre ohne Vorjahres-Referenzen Muell (SAP FY2024: 10
von 15 Werten falsch, teils FY2023-Werte). Verlaesslichste Quelle ist
die VERGLEICHSSPALTE im Bericht des Folgejahres N+1 (Microsoft-
Comparative-Muster der US-Pipeline): die Prompts beider Stufen
verlangen den N+1-Bericht (Q4-Statement/Geschaeftsbericht) und die
Vorjahres-Vergleichsspalte; das Spalten-Gate (ii) erlaubt N+1-Labels
im Kopf, wenn period_end_date auf das N-Jahresende passt (ohne
period_end_date bleibt N+1 eine fremde Jahreszahl). Zusaetzlich
Kopie-Detektor (_apply_backfill_copy_gate): Werte, die exakt (<0,1%)
einem vorhandenen FY-Actual des Nachbarjahres (N+1/N-1) desselben
Keys entsprechen, sind Spaltenverrutscher der Vergleichsspalte und
werden verworfen.

Das H1-Q1/9M-H1/FY-9M-Rechenprotokoll gilt ausdruecklich auch fuer
eps_diluted (Naeherung — Weighted-Average-Shares differieren leicht)
und die *_adjusted-Sidecars; abgeleitete Werte (derived_from gesetzt)
passieren die Persistenz mit den Spalten-Gates.

Schreib-Invarianten wie ueberall: normalize_sign, currency_conflict,
SAVEPOINT-Slot-Muster (uq_company_values_slot). Schreibrechte: Manual-/
PDF-/XBRL-Provider-Zeilen mit Wert bleiben; not_found/two_stage_*/web_*/
calculated/statement_research und Markt-Provider-Zeilen (Bloomberg-
Label, Yahoo-Altbestand) sind ersetzbar. source_name ist
quote-first ("<quote> | <url>", beginnt nie mit https — bleibt damit
fuer den naechsten Lauf ersetzbar). Kein Beleg -> null -> not_found-
Platzhalter (rote Zelle) via stamp_attempt_and_fill_not_found.

Ratsche (first-plausible-wins unter Gleichrangigen, Kontroll-Review-
Befund: Reruns WUERFELTEN NEU statt zu konvergieren — SAP: 7 zuvor
korrekte Quartalswerte durch einen Wiederholungslauf beschaedigt):
eine bestehende statement_research-Zeile MIT Wert wird von einem neuen
Recherche-Wert NUR ersetzt, wenn sie VERDAECHTIG ist —
  (a) sie traegt consistency_flags (qsum_mismatch etc.), ODER
  (b) sie ist Quartals-Zeile eines SUMMABLE-Keys, dessen 4 Quartale
      der autoritativen FY-Zeile widersprechen (|Summe/FY - 1| > 1%,
      FY autoritativ = statement_research/Provider-Actual mit Wert).
Sonst bleibt der alte Wert; weicht der neue um > 1% ab, wird das als
Diskrepanz geloggt (INFO 'Ratsche'). Gleiches Prinzip fuer die
adjusted-Sidecars. Fremde ersetzbare Herkuenfte (two_stage_*/web_*/
calculated/not_found/Markt-Provider) bleiben ersetzbar wie bisher.
Die Bedarfspruefung (_group_needs_research/_needy_cells) zaehlt
unverdaechtige eigene Zellen entsprechend NICHT mehr als beduerftig —
sonst liefen Calls fuer nichts.

Beruehrt werden NUR berichtete Perioden (Periodenende plus
REPORTING_GRACE_DAYS abgelaufen). Unberichtete Perioden des laufenden
Jahres bekommen weder Write noch not_found-Stempel — sonst verschattet
der Actual-Platzhalter die Forecast-Zeile im selben Slot-Paar
(Detail-Seite wirkt leer).

Stufe 2 — PDF-Bruecke (Muster gaap_bridge, dort fuer US-8-K): Die
Websuche FINDET die Berichts-PDFs (url-Feld, auch bei value null),
kann sie aber nicht LESEN — Bilanz-Quartale, non-IFRS-Spalten und
aeltere Quartale bleiben leer. Sind nach Stufe 1 + Ankern noch
beduerftige berichtete Zellen der Gruppe uebrig (Actual ohne Wert),
werden die von Stufe 1 gelieferten Dokument-URLs dedupliziert, das
Dokument heruntergeladen (SSRF-Guards, Groessen-/Seiten-Limit) und als
document-Block (PDF) bzw. extrahierter Text (HTML) an EINEN weiteren
Claude-Call gegeben — exakte Tabellenwerte inkl. non-IFRS-Spalten
(SAP-Muster: IFRS und non-IFRS nebeneinander). Max. 2 Dokument-Calls
pro Gruppe/Jahr (Kosten-Deckel), Bilanz-Gruppe 3 — der Schulden-Split
steht oft erst im dritten Kandidaten. Persistenz/Gates identisch zu
Stufe 1; non-IFRS-Sidecars mit adjustments_note 'Non-IFRS
(Berichts-PDF)'. Dokument gelesen, Wert nachweislich nicht enthalten
-> not_found.

Grosse PDFs (> MAX_PDF_PAGES, z.B. 327-Seiten-Geschaeftsberichte)
werden nicht mehr uebersprungen: pypdf extrahiert seitenweise Text,
Keyword-Match pro Gruppe findet die relevanten Seiten (Statements +
Finanzverbindlichkeiten-Note — SAP-Lektion: der Schulden-Split ohne
IFRS-16-Leases steht NUR in der Note des Geschaeftsberichts), und ein
Teil-PDF (+/- 1 Nachbarseite, Deckel MAX_EXTRACT_PAGES) geht an den
Call. Kein Keyword-Treffer -> Skip wie bisher. Bilanz-Kandidaten
werden priorisiert: Halbjahres-/Geschaeftsberichte (enthalten die
Notes) vor Quartals-Statements; income/cashflow behalten die
Coverage-Reihenfolge, Geschaeftsberichte als letzte Kandidaten.
"""
import logging
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.values.currency_keys import CURRENCY_KEYS
from app.values.models import CompanyValue
from app.values.persistence import adjusted_is_protected, currency_conflict, normalize_sign

logger = logging.getLogger(__name__)

# Modell/Muster wie guidance_estimates: web_search-Tool, temperature 0.
EXTRACT_MODEL = "claude-sonnet-4-6"
WEB_SEARCH_MAX_USES = 5
# Truncation-Lektion aus guidance_estimates: 4096 fuehrte dort schon bei
# ~22 Werten zu abgeschnittenem JSON. Hier sind es bis zu 30 Werte mit
# Zitaten — grosszuegig dimensionieren.
MAX_TOKENS = 12288

_Q_TYPES = ("Q1", "Q2", "Q3", "Q4")
_PERIODS = ("FY",) + _Q_TYPES

# Gruppen-Definition: (key, Kurzbeschreibung mit Pitfall-Hinweis).
# Die Beschreibungen sind die eingedampfte Essenz der alten
# scripts/prompts/*.md (DAX-Review-Muster: Konzern- statt Segmentzeile,
# attributable NI, diluted total EPS, Brutto-Capex, IFRS-2-Gesamtsumme).
STATEMENT_GROUPS: dict[str, tuple[tuple[str, str], ...]] = {
    "income": (
        ("revenue", "Umsatzerloese (Konzern gesamt, nicht Segment)"),
        ("net_income", "Konzernergebnis den Aktionaeren des Mutterunternehmens zurechenbar (attributable) — NICHT das Konzernergebnis inkl. Minderheiten; bei Siemens z.B. weist die GuV beide aus"),
        ("eps_diluted", "verwaessertes Ergebnis je Aktie (diluted, Gesamt-Konzern)"),
        ("ebitda", "EBITDA wie im Bericht ausgewiesen (nicht selbst rechnen; sonst null)"),
        ("net_income_adjusted", "bereinigtes Konzernergebnis (Core/adjusted/Non-IFRS) — NUR wenn im Bericht explizit ausgewiesen, sonst null"),
        ("eps_diluted_adjusted", "bereinigtes verwaessertes Ergebnis je Aktie — NUR wenn im Bericht explizit ausgewiesen, sonst null"),
    ),
    "cashflow": (
        ("operating_cash_flow", "Cashflow aus laufender Geschaeftstaetigkeit (Kapitalflussrechnung, Konzernzeile)"),
        ("capex", "Investitionen in Sachanlagen und immaterielle Vermoegenswerte (brutto, keine Netto-Capex)"),
        ("sbc", "aktienbasierte Verguetung (IFRS-2-Gesamtaufwand ueber alle Plaene)"),
        ("dividends", "gezahlte Dividenden laut Kapitalflussrechnung"),
        ("buyback_volume", "Aktienrueckkaeufe laut Kapitalflussrechnung"),
    ),
    "balance": (
        ("cash_and_equivalents", "Zahlungsmittel und Zahlungsmittelaequivalente (Bilanzstichtag)"),
        ("st_investments", "kurzfristige Wertpapiere/Finanzanlagen (Bilanzstichtag; kurzfristige finanzielle Vermoegenswerte, oft 'Other financial assets (current)')"),
        ("st_debt", "kurzfristige Finanzschulden OHNE Leasingverbindlichkeiten (Anleihen/Bankverbindlichkeiten/Commercial Paper). Viele IFRS-Bilanzen zeigen nur 'Financial liabilities' (current) inkl. IFRS-16-Leasing — die Aufgliederung (Anleihen/Bank vs. Leases) steht in der Note zu Finanzverbindlichkeiten bzw. im Liquiditaets-/Financial-Position-Abschnitt: diese Note lesen. Steht die Aufgliederung nachweislich nicht im Dokument: 'Financial liabilities' (current) MINUS separat ausgewiesener kurzfristiger Leasingverbindlichkeiten rechnen (beide Zahlen muessen im Dokument stehen, derived_from='fin_liabilities - leases'); sonst null"),
        ("lt_debt", "langfristige Finanzschulden OHNE Leasingverbindlichkeiten (Anleihen/Bankdarlehen). Viele IFRS-Bilanzen zeigen nur 'Financial liabilities' (non-current) inkl. IFRS-16-Leasing — die Aufgliederung steht in der Note zu Finanzverbindlichkeiten bzw. im Liquiditaets-/Financial-Position-Abschnitt: diese Note lesen. Steht die Aufgliederung nachweislich nicht im Dokument: 'Financial liabilities' (non-current) MINUS separat ausgewiesener langfristiger Leasingverbindlichkeiten rechnen (beide Zahlen muessen im Dokument stehen, derived_from='fin_liabilities - leases'); sonst null"),
    ),
}

_GROUP_LABELS = {
    "income": "GuV",
    "cashflow": "Kapitalflussrechnung",
    "balance": "Bilanz",
}

# Adjusted-Sidecars: landen in numeric_value_adjusted der Basis-Zeile,
# nie als eigene value_key-Zeile.
_ADJUSTED_SIDECARS = {
    "net_income_adjusted": "net_income",
    "eps_diluted_adjusted": "eps_diluted",
}

# reported <= adjusted-Gate-Paare (klarer Verstoss -> beide verwerfen).
_REPORTED_ADJ_PAIRS = (
    ("net_income", "net_income_adjusted"),
    ("eps_diluted", "eps_diluted_adjusted"),
)

# Per-Share-Keys sind vom Einheiten-Check (>= 1 Mio) ausgenommen.
_PER_SHARE_KEYS = frozenset({"eps_diluted", "eps_diluted_adjusted"})

# Basis-Keys, die dieser Pfad persistiert (ohne Sidecars).
STATEMENT_RESEARCH_KEYS = frozenset(
    key
    for specs in STATEMENT_GROUPS.values()
    for key, _ in specs
    if key not in _ADJUSTED_SIDECARS
)

# qsum-Enforcement nur fuer Flow-Keys. eps_diluted bewusst NICHT dabei:
# FY != exakt Sigma(Q) wegen Weighted-Average-Diluted-Shares (Buybacks) —
# die 1%-Toleranz wuerde legitime Reihen verwerfen. Bilanz-Keys sind
# Stichtagswerte, keine Summen.
_QSUM_ENFORCE_KEYS = frozenset({
    "revenue", "net_income", "ebitda",
    "operating_cash_flow", "capex", "sbc", "dividends", "buyback_volume",
})
_QSUM_TOL = Decimal("0.01")

_UNIT_MIN = Decimal("1000000")
# Vorjahresband 40-160%: |v/prev - 1| > 0.60 verwirft.
_PREV_DEVIATION_TOL = Decimal("0.60")
# reported <= adjusted + 1% Toleranz (Rundungsdifferenzen der Berichte).
_REPORTED_ADJ_TOL = Decimal("0.01")  # (nur noch historisch)
# 150%: echte Non-IFRS-Divergenzen koennen gross sein (SAP FY2024
# Restrukturierung: IFRS 3.3 vs non-IFRS 5.7 Mrd = 72%). Echte
# Spur-Verwechslungen (Vorjahresspalte, Kumulativ) fangen seit dem
# Spalten-Gate dessen Checks; hier bleibt nur der Absurditaets-Deckel.
_REPORTED_ADJ_BAND = Decimal("1.50")

# Ersetzbare Herkuenfte (Muster _derivation_replaceable in consistency,
# erweitert um die eigene Signatur, damit der naechste Lauf seine
# Vorgaenger-Zeilen aktualisieren darf).
_REPLACEABLE_METHODS = (
    "not_found", "not_estimated", "calculated", "statement_research",
)

# Markt-Provider-Erkennung (Migration bestehender Yahoo-Werte): der
# Marktdaten-Feed schreibt primary_method='provider' mit dem Label
# 'Bloomberg' (YahooFinanceProvider.name, auch Varianten wie 'Bloomberg
# (Close ...)'). Berichts-Provider tragen andere Labels ('ESEF ...',
# 'SEC EDGAR ...') und bleiben gesperrt. NUR die Statement-Recherche darf
# Markt-Provider-Zeilen ersetzen (Kundenentscheid: Feed ist keine
# Wertequelle) — die Ableitungs-Guards (consistency) respektieren
# 'provider' unveraendert.
# Neues Label + Alt-Bestand ("Bloomberg" war das historische,
# irrefuehrende Label des Yahoo-Feeds — Umbenennung Kundenauflage).
_MARKET_PROVIDER_LABELS = ("Marktdaten-Feed", "Bloomberg")

# Yahoo-Cross-Check-Gate: weites Band, weil der Feed selbst ungenau ist.
_YAHOO_XCHECK_TOL = Decimal("0.35")

# Ratsche: Diskrepanz-Log-Schwelle (bestehender Wert behalten, neuer
# Vorschlag weicht > 1% ab).
_RATCHET_DIFF_TOL = Decimal("0.01")

# --- Spalten-Gates (SAP-Abnahme-Fehlerklassen a+d) ---------------------------
# period_end_date muss zum Zielperioden-Ende passen (Quartalsversatz von
# Nicht-Kalender-FYs deckt die Toleranz).
_PERIOD_END_TOL_DAYS = 21
_YEAR_IN_LABEL_RE = re.compile(r"\b(?:19|20)\d{2}\b")
# Constant-Currency-Marker: Substrings + 'cc' als eigenstaendiges Token.
_CC_LABEL_MARKERS = (
    "constant currency", "constant currencies", "currency-adjusted",
    "currency adjusted", "waehrungsbereinigt", "währungsbereinigt",
)
_CC_TOKEN_RE = re.compile(r"\bcc\b")
_NON_IFRS_LABEL_RE = re.compile(r"\bnon[- ]ifrs\b")
# Kumulativ-Marker: H1/6M/9M/YTD-Spalte als Einzelquartal gebucht
# (SAP: H1-Buybacks 1.633 als Q2) — nur ohne derived_from verwerfen.
_CUMULATIVE_LABEL_RE = re.compile(
    r"\b(?:h1|hy|6m|9m|six months|nine months|ytd)\b"
)

# Portal-Blocklist (Quellen-Hygiene): Finanzportale liefern umgerechnete
# USD-Werte und veraltete Staende — keine gueltige Quelle fuer
# Fundamentals (SAP: capex Q1-26 von GuruFocus in USD).
_PORTAL_BLOCKLIST = (
    "gurufocus.com", "stockanalysis.com", "macrotrends.net", "wisesheets.io",
)

# --- Stufe 2: Dokument-Bruecke ----------------------------------------------
# Kosten-Deckel: max. 2 Dokument-Calls pro Gruppe/Jahr; Bilanz 3 — der
# Schulden-Split (Finanzverbindlichkeiten-Note) steht oft erst im
# dritten Kandidaten (Halbjahres-/Geschaeftsbericht).
MAX_DOC_CALLS = 2
MAX_DOC_CALLS_BALANCE = 3
# Download-Limits: ~20 MB; Berichts-PDFs > 100 Seiten (Geschaeftsberichte)
# gehen nicht komplett an die API (~100-Seiten-Limit) — stattdessen wird
# per Keyword-Match ein Teil-PDF der relevanten Seiten extrahiert.
MAX_DOC_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 100
# Teil-PDF-Deckel: Keyword-Seiten +/- 1 Nachbarseite, max. 30 Seiten.
MAX_EXTRACT_PAGES = 30

# Keyword-Sets pro Gruppe fuer die Seiten-Extraktion (case-insensitive).
# balance enthaelt die Finanzverbindlichkeiten-Note (SAP: st_debt/lt_debt
# ohne IFRS-16-Leases stehen nur dort).
_PAGE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "balance": (
        "financial liabilities", "finanzverbindlichkeiten",
        "financial debt", "borrowings",
        "statement of financial position", "bilanz",
    ),
    "income": (
        "income statement", "gewinn- und verlustrechnung",
        "non-ifrs", "reconciliation", "earnings per share",
    ),
    "cashflow": ("cash flow", "kapitalflussrechnung"),
}

# Kandidaten-Priorisierung (URL-Marker): Halbjahres-/Geschaeftsberichte
# enthalten die vollstaendigen Notes — fuer die Bilanz-Gruppe zuerst.
# Fuer income/cashflow bleiben Statements zuerst, Geschaeftsberichte
# als letzte Kandidaten (statt nie, seit der Seiten-Extraktion).
_BALANCE_PRIORITY_URL_MARKERS = (
    "half-year", "halbjahres", "annual", "integrated",
    "geschaeftsbericht", "jahresbericht",
)
_ANNUAL_REPORT_URL_MARKERS = (
    "annual", "integrated", "geschaeftsbericht", "jahresbericht",
)
# Fallback-Heuristik, falls pypdf das PDF nicht parsen kann.
_PDF_PAGE_BYTES_HEURISTIC = 50_000
_DOC_REDIRECT_LIMIT = 3
_DOC_TIMEOUT_SECONDS = 30
# HTML-Dokumente: extrahierter Text wird gekappt (Muster gaap_bridge).
_HTML_TEXT_CAP_CHARS = 80_000
# non-IFRS-Sidecars der Dokument-Stufe tragen diese Note.
DOC_SIDECAR_NOTE = "Non-IFRS (Berichts-PDF)"

_ENTRY_FIELDS = (
    '{"value": <number|null>, "quote": <string|null>, '
    '"url": <string|null>, "column_label": <string|null>, '
    '"period_end_date": <"YYYY-MM-DD"|null>, "derived_from": <string|null>}'
)

# Gemeinsame Feld-Erklaerung fuer beide Stufen (Spalten-Gate-Inputs).
_COLUMN_FIELDS_EXPLANATION = (
    "column_label = die EXAKT abgeschriebene Spaltenueberschrift der "
    "Tabelle, aus der der Wert stammt (z.B. 'Q2 2025', '2024', "
    "'H1 2026'); period_end_date = Stichtag/Periodenende dieser Spalte "
    "als ISO-Datum (YYYY-MM-DD)."
)


def groups_for_keys(keys) -> list[str]:
    """Statement-Gruppen, die die gegebenen value_keys abdecken.
    Berechnete Keys werden auf ihre Input-Gruppe gemappt (fcf -> Cashflow,
    net_debt -> Bilanz)."""
    keys = set(keys)
    if "fcf" in keys:
        keys.update({"operating_cash_flow", "capex"})
    if "net_debt" in keys:
        keys.add("cash_and_equivalents")
    return [
        gname
        for gname, specs in STATEMENT_GROUPS.items()
        if any(key in keys for key, _ in specs)
    ]


def _fy_end_date(company, year: int) -> date:
    m = getattr(company, "fiscal_year_end_month", None) or 12
    d = getattr(company, "fiscal_year_end_day", None) or 31
    try:
        return date(year, m, d)
    except ValueError:
        # 29.02. in Nicht-Schaltjahr — konservativ auf den 28. runden.
        return date(year, m, 28)


def _reported_periods(company, year: int, today: date | None = None) -> tuple[str, ...]:
    """Perioden des Jahres, die nach dem Karenz-Kriterium berichtet sind:
    Periodenende (FY-Ende bzw. Quartalsende) plus REPORTING_GRACE_DAYS
    abgelaufen. Nur diese darf der Lauf schreiben oder stempeln."""
    from app.values.detail_page import REPORTING_GRACE_DAYS, quarter_end_date

    if today is None:
        today = datetime.now(timezone.utc).date()
    out: list[str] = []
    if (today - _fy_end_date(company, year)).days >= REPORTING_GRACE_DAYS:
        out.append("FY")
    for q in _Q_TYPES:
        q_end = quarter_end_date(
            year, q,
            getattr(company, "fiscal_year_end_month", None),
            getattr(company, "fiscal_year_end_day", None),
        )
        if q_end is not None and (today - q_end).days >= REPORTING_GRACE_DAYS:
            out.append(q)
    return tuple(out)


def _is_non_calendar_fy(company) -> bool:
    """FY-Ende weicht vom Kalenderjahresende (31.12.) ab (Siemens 30.09.)."""
    m = getattr(company, "fiscal_year_end_month", None) or 12
    d = getattr(company, "fiscal_year_end_day", None) or 31
    return (m, d) != (12, 31)


def _stichtag_sentence(company, year: int) -> str:
    """Prompt-Ergaenzung fuer Nicht-Kalender-FYs (Siemens-Cash-Klasse:
    das Modell lieferte 31.12.-Kalenderwerte statt 30.09.-Stichtag)."""
    if not _is_non_calendar_fy(company):
        return ""
    fy_end = _fy_end_date(company, year).isoformat()
    return (
        f"Das Geschaeftsjahr weicht vom Kalenderjahr ab: Stichtag ist "
        f"der {fy_end}, NICHT der 31.12. "
    )


def _comparative_column_sentence(year: int) -> str:
    """FY-only-Backfill: die verlaesslichste Quelle fuer FY N ist die
    Vorjahres-Vergleichsspalte im Bericht des Folgejahres N+1
    (Microsoft-Comparative-Muster der US-Pipeline)."""
    return (
        f"Nimm dafuer den Bericht des FOLGEJAHRES {year + 1} "
        f"(Q4-Statement bzw. Geschaeftsbericht {year + 1}): dort steht "
        f"das Geschaeftsjahr {year} als VORJAHRES-Vergleichsspalte "
        "direkt neben den aktuellen Zahlen — lies die Vergleichsspalte "
        "und gib deren column_label und period_end_date an. "
    )


def _build_system_prompt(company, year: int, group: str,
                         periods: tuple[str, ...] = _PERIODS) -> str:
    fy_end = _fy_end_date(company, year).isoformat()
    currency = getattr(company, "currency", None) or "EUR"
    label = _GROUP_LABELS[group]
    fy_only = tuple(periods) == ("FY",)
    if fy_only:
        # FY-only-Modus (N-2-Backfill): NUR die Jahres-Spalte — aus der
        # Vergleichsspalte des Folgejahres-Berichts, keine Quartale.
        if group == "balance":
            period_sentence = (
                "Bilanzwerte sind Stichtagswerte: FY = Stand am "
                "Geschaeftsjahresende. " + _comparative_column_sentence(year)
            )
        else:
            period_sentence = _comparative_column_sentence(year)
        scope_sentence = (
            f"der IR-Seite) die folgenden {label}-Werte NUR fuer das "
            "Gesamtjahr (FY) — keine Quartalswerte, exakte Tabellenwerte, "
        )
    elif group == "balance":
        period_sentence = (
            "Bilanzwerte sind Stichtagswerte: FY = Stand am "
            "Geschaeftsjahresende (identisch Q4), Q1-Q3 = Stand am "
            "jeweiligen Quartalsende. "
        )
        scope_sentence = (
            f"der IR-Seite) die folgenden {label}-Werte fuer das Gesamtjahr "
            "(FY) und alle verfuegbaren Quartale — exakte Tabellenwerte, "
        )
    else:
        period_sentence = (
            "Viele Firmen berichten Halbjahres- statt Q2-Zahlen: rechne "
            "dann Q2 = H1 - Q1 bzw. Q3 = 9M - H1 (und Q4 = FY - 9M) und "
            'markiere solche abgeleiteten Quartale im Feld derived_from '
            '(z.B. "H1-Q1", "9M-H1", "FY-9M"); direkt berichtete Werte '
            "haben derived_from null. Dieses Rechenprotokoll gilt "
            "ausdruecklich AUCH fuer eps_diluted (Naeherung, "
            "derived_from setzen) und die *_adjusted-Werte. "
        )
        scope_sentence = (
            f"der IR-Seite) die folgenden {label}-Werte fuer das Gesamtjahr "
            "(FY) und alle verfuegbaren Quartale — exakte Tabellenwerte, "
        )
    return (
        f"Extrahiere fuer {company.name} ({company.ticker}) "
        f"Geschaeftsjahr {year} (Ende {fy_end}) aus den OFFIZIELLEN "
        "Berichten (Geschaeftsbericht, Halbjahres-/Quartalsmitteilungen "
        + scope_sentence +
        "keine gerundeten Freitextzahlen. Werte NUR aus "
        "Originalberichten und offiziellen Pressemitteilungen der Firma "
        "(IR-Domain, Firmenmeldungen); Finanzportale (GuruFocus, "
        "stockanalysis, macrotrends, wisesheets u.ae.) sind KEINE "
        "gueltige Quelle. Pro Wert: Quelle-URL, woertliches Zitat bzw. "
        "Tabellenzeile, die exakt abgeschriebene Spaltenueberschrift "
        "(column_label) und der Stichtag der Spalte (period_end_date). "
        + period_sentence
        + _stichtag_sentence(company, year)
        + f"Absolute Betraege in {currency}-Basiseinheiten "
        "(z.B. '5,8 Mrd' -> 5800000000), EPS je Aktie — Werte in der "
        f"Berichtswaehrung der Firma ({currency}), niemals umgerechnete "
        "Werte von Drittseiten. Nicht berichtete "
        "Perioden: value null. Gib fuer JEDE Periode die URL des "
        "offiziellen Berichts-PDFs/HTML im Feld url an — auch wenn du "
        "den Wert nicht extrahieren kannst (dann value null, url "
        "trotzdem gesetzt). Antworte NUR mit einem JSON-Objekt nach "
        "dem Schema in der User-Nachricht — kein Text ausserhalb des "
        "JSON, keine Markdown-Fences."
    )


def _build_user_prompt(company, year: int, group: str,
                       periods: tuple[str, ...] = _PERIODS) -> str:
    specs = STATEMENT_GROUPS[group]
    lines = [
        f"Werte fuer {company.name} ({company.ticker}), "
        f"Geschaeftsjahr {year} ({_GROUP_LABELS[group]}):",
        "",
    ]
    for key, desc in specs:
        lines.append(f"- {key}: {desc}")
    if tuple(periods) == ("FY",):
        # FY-only-Backfill: Quelle ist die Vergleichsspalte des
        # Folgejahres-Berichts (siehe _comparative_column_sentence).
        lines += [
            "",
            f"Quelle: Bericht des Folgejahres {year + 1} — die "
            f"FY-{year}-Werte stehen dort in der Vorjahres-"
            "Vergleichsspalte (column_label und period_end_date der "
            "Vergleichsspalte angeben).",
        ]
    # Schema nennt nur die angeforderten Perioden (FY-only-Modus: nur die
    # FY-Spalte, Quartale tauchen im Prompt nicht auf).
    entry_schema = ", ".join(f'"{pt}": ENTRY' for pt in periods)
    fields = ",\n".join(
        f'  "{key}": {{{entry_schema}}}'
        for key, _ in specs
    )
    lines += [
        "",
        "Antworte mit JSON exakt nach diesem Schema:",
        "",
        "{",
        fields,
        "}",
        "",
        f"ENTRY = {_ENTRY_FIELDS}",
        "",
        "quote = woertliches Zitat/Tabellenzeile aus dem Bericht; "
        "url = Quelle-URL (URL des offiziellen Berichts auch angeben, "
        "wenn value null bleibt); " + _COLUMN_FIELDS_EXPLANATION + " "
        "derived_from = Rechenweg bei "
        "abgeleiteten Quartalen, sonst null; nicht berichtet = "
        "value null.",
    ]
    return "\n".join(lines)


def _call_claude(company, year: int, group: str, cost_tracker=None,
                 periods: tuple[str, ...] = _PERIODS) -> dict | None:
    """EIN Claude-Call mit Websuche fuer eine Statement-Gruppe. In Tests
    gemockt (conftest blockt get_client). `periods` steuert das
    Prompt-Schema (FY-only-Modus: nur die FY-Spalte)."""
    import app.llm.claude as claude_mod
    from app.llm.rate_limiter import claude_limiter
    from app.llm.json_utils import extract_json

    client = claude_mod.get_client()

    def _do_call():
        return client.messages.create(
            model=EXTRACT_MODEL,
            max_tokens=MAX_TOKENS,
            temperature=0,
            system=_build_system_prompt(company, year, group, periods=periods),
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": WEB_SEARCH_MAX_USES,
            }],
            messages=[{
                "role": "user",
                "content": _build_user_prompt(company, year, group, periods=periods),
            }],
        )

    response = claude_limiter.call(_do_call)
    if cost_tracker is not None:
        cost_tracker.add_response(response, EXTRACT_MODEL)
    parts = [getattr(block, "text", None) for block in response.content]
    raw = "\n".join(p for p in parts if p).strip()
    try:
        data = extract_json(raw)
    except ValueError as e:
        logger.warning(
            "statement research: kein JSON in Claude-Antwort (%s FY%s %s): %s",
            company.ticker, year, group, e,
        )
        return None
    return data if isinstance(data, dict) else None


def _to_decimal(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _parse_iso_date(value) -> date | None:
    """ISO-Datum (auch mit Zeitanteil) tolerant parsen; sonst None."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _parse_entry(entry) -> dict | None:
    """Ein Perioden-Objekt normalisieren; ohne Wert -> None."""
    if not isinstance(entry, dict):
        return None
    value = _to_decimal(entry.get("value"))
    if value is None:
        return None
    quote = entry.get("quote")
    url = entry.get("url")
    derived_from = entry.get("derived_from")
    label = entry.get("column_label")
    return {
        "value": value,
        "quote": quote.strip() if isinstance(quote, str) and quote.strip() else None,
        "url": url if isinstance(url, str) and url.startswith(("http://", "https://")) else None,
        "column_label": (
            label.strip()[:120]
            if isinstance(label, str) and label.strip()
            else None
        ),
        "period_end_date": _parse_iso_date(entry.get("period_end_date")),
        "derived_from": (
            derived_from.strip()[:40]
            if isinstance(derived_from, str) and derived_from.strip()
            else None
        ),
    }


def _parse_payload(data: dict, group: str) -> dict[str, dict[str, dict]]:
    """Antwort in {key: {period: info}} normalisieren (nur bekannte Keys
    und Perioden, nur Eintraege mit Wert)."""
    parsed: dict[str, dict[str, dict]] = {}
    for key, _ in STATEMENT_GROUPS[group]:
        entry = data.get(key)
        if not isinstance(entry, dict):
            continue
        periods: dict[str, dict] = {}
        for pt in _PERIODS:
            info = _parse_entry(entry.get(pt))
            if info is not None:
                periods[pt] = info
        if periods:
            parsed[key] = periods
    return parsed


def _prev_actual(db, company_id, key: str, year: int, period_type: str) -> Decimal | None:
    """Vorjahres-Ist derselben Periode: FY vs FY, Qx vs Qx.

    Nur vertrauenswuerdige Herkunft als Band-Referenz — Altlasten der
    Two-Stage-Aera haben nachweislich Muellwerte hinterlassen (Siemens
    EBITDA Q2 '280 Mio'), die sonst korrekte neue Werte blocken."""
    row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == period_type,
            CompanyValue.period_year == year - 1,
            CompanyValue.is_forecast.is_(False),
            CompanyValue.numeric_value.isnot(None),
            CompanyValue.primary_method.in_(
                ("provider", "statement_research", "manual", "calculated")
            ),
        )
        .first()
    )
    return row.numeric_value if row else None


def _apply_gates(db, company, year: int, parsed: dict[str, dict[str, dict]]) -> None:
    """Deterministische Gates — mutiert `parsed` in place.

    1. Einheiten-Check: Absolutwerte unter 1 Mio (aber != 0) sind fast
       immer eine fehlende Skalierung (ausser Per-Share-Keys).
    2. Vorjahresband 40-160% gegen das Vorjahres-Ist derselben Periode
       (fehlt es: uebersprungen; Sign-Flip erlaubt). Ausnahme
       net_income/eps_diluted: ist das Paar intern konsistent
       (_internally_consistent), wird die Band-Verletzung als echter
       Turnaround akzeptiert (SAP 2025: +134% war korrekt). Sidecars
       laufen ueber das Paar-Gate, nicht ueber das Band.
    3. Spur-Paar-Gate (150%-Band reported vs adjusted) — Verstoss
       verwirft beide Werte der Periode; ist die GAAP-Seite intern
       konsistent, wird NUR der Sidecar verworfen.
    """
    ticker = company.ticker
    # SNAPSHOT-Aktienzahl lazy laden — nur noetig, wenn ein NI/EPS-Gate
    # den Konsistenz-Schiedsrichter befragt.
    shares_cache: list[Decimal | None] = []

    def _shares() -> Decimal | None:
        if not shares_cache:
            shares_cache.append(_shares_snapshot(db, company.id))
        return shares_cache[0]

    for key in list(parsed):
        if key in _PER_SHARE_KEYS:
            continue
        for pt in list(parsed[key]):
            v = parsed[key][pt]["value"]
            if v != 0 and abs(v) < _UNIT_MIN:
                logger.warning(
                    "statement research %s/FY%s: %s/%s=%s unter 1 Mio "
                    "(Einheiten-Verdacht) — skip", ticker, year, key, pt, v,
                )
                del parsed[key][pt]

    for key in list(parsed):
        if key in _ADJUSTED_SIDECARS:
            continue
        for pt in list(parsed[key]):
            prev = _prev_actual(db, company.id, key, year, pt)
            if prev is None or prev == 0:
                continue
            v = parsed[key][pt]["value"]
            sign_flip = (v >= 0) != (prev >= 0)
            if not sign_flip and abs(v / prev - 1) > _PREV_DEVIATION_TOL:
                # Turnaround-Ausnahme (SAP 2025): passt NI zu EPS x Aktien,
                # ist der Sprung real — beide Werte bleiben.
                if (
                    key in ("net_income", "eps_diluted")
                    and _internally_consistent(parsed, pt, _shares())
                ):
                    logger.info(
                        "statement research %s/FY%s: %s/%s=%s ausserhalb "
                        "40-160%% des Vorjahres-Ist %s — Turnaround "
                        "akzeptiert: NI/EPS intern konsistent",
                        ticker, year, key, pt, v, prev,
                    )
                    continue
                logger.warning(
                    "statement research %s/FY%s: %s/%s=%s ausserhalb "
                    "40-160%% des Vorjahres-Ist %s — skip",
                    ticker, year, key, pt, v, prev,
                )
                del parsed[key][pt]

    for base_key, adj_key in _REPORTED_ADJ_PAIRS:
        base = parsed.get(base_key)
        adj = parsed.get(adj_key)
        if not base or not adj:
            continue
        for pt in list(base):
            a = adj.get(pt)
            if a is None:
                continue
            b_val = base[pt]["value"]
            a_val = a["value"]
            # Non-IFRS darf UNTER reported liegen (schliesst auch Einmal-
            # GEWINNE aus, SAP-Muster) — keine Richtungs-Regel wie im
            # US-GAAP-Pfad. Nur klare Verwechslungen verwerfen: mehr als
            # 150% Abstand zwischen den Spuren.
            if b_val != 0 and abs(a_val - b_val) > abs(b_val) * _REPORTED_ADJ_BAND:
                # Ist die GAAP-Seite intern konsistent, ist der Sidecar
                # der Muell (SAP: non-IFRS-Betriebsgewinn 8,169 Mrd als
                # NI-Sidecar riss das korrekte IFRS-NI 3,098 Mrd mit).
                if _internally_consistent(parsed, pt, _shares()):
                    logger.warning(
                        "statement research %s/FY%s: %s/%s=%s vs %s=%s "
                        "weicht >150%% ab — Sidecar verworfen, Basis "
                        "intern konsistent",
                        ticker, year, base_key, pt, b_val, adj_key, a_val,
                    )
                    del adj[pt]
                    continue
                logger.warning(
                    "statement research %s/FY%s: %s/%s=%s vs %s=%s weicht "
                    ">150%% ab (Spur-Verwechslung?) — beide skip",
                    ticker, year, base_key, pt, b_val, adj_key, a_val,
                )
                del base[pt]
                del adj[pt]


def _period_end_target(company, year: int, pt: str) -> date | None:
    """Erwartetes Periodenende der Zielperiode (FY-Ende bzw. Quartalsende
    aus der FY-Konvention)."""
    if pt == "FY":
        return _fy_end_date(company, year)
    from app.values.detail_page import quarter_end_date

    return quarter_end_date(
        year, pt,
        getattr(company, "fiscal_year_end_month", None),
        getattr(company, "fiscal_year_end_day", None),
    )


def _column_gate_reason(company, year: int, key: str, pt: str, info: dict,
                        strict_period_end: bool,
                        fy_backfill: bool = False) -> str | None:
    """Verwerfungsgrund der Spalten-Gates oder None (Wert passiert).

    (i)   period_end_date passt nicht zum Zielperioden-Ende (±21 Tage);
          fehlend: Stufe 2 verwirft (Dokument liegt vor, der Stichtag
          steht in der Tabelle), Stufe 1 lenient.
    (ii)  column_label nennt eine fremde Jahreszahl — Vorjahresspalten-
          Falle (SAP: adjusted-NI 2025 war durchgaengig die 2024-Spalte).
          Erlaubt sind Zieljahr und das Kalenderjahr des Zielperioden-
          Endes (Nicht-Kalender-FYs). Im FY-only-Backfill (Quelle ist
          der N+1-Bericht mit der Vergleichsspalte) ist AUCH N+1
          erlaubt — aber nur, wenn period_end_date vorliegt (und damit
          Check (i) auf das N-Jahresende bestanden hat).
    (iii) constant-currency-Marker; non-IFRS-Marker nur fuer die
          IFRS-Basisspur (Sidecars SIND die non-IFRS-Spur).
    (iv)  Kumulativ-Marker (H1/6M/9M/YTD) fuer ein Einzelquartal ohne
          derived_from — kumulierter Wert als Quartal gebucht.
    """
    target_end = _period_end_target(company, year, pt)
    ped = info.get("period_end_date")
    if ped is None:
        if strict_period_end:
            return "period_end_date fehlt (Dokument-Stufe)"
    elif (
        target_end is not None
        and abs((ped - target_end).days) > _PERIOD_END_TOL_DAYS
    ):
        return (
            f"period_end_date {ped.isoformat()} passt nicht zum "
            f"Zielperioden-Ende {target_end.isoformat()}"
        )
    label = info.get("column_label")
    if not label:
        return None
    low = label.lower()
    years = {int(m) for m in _YEAR_IN_LABEL_RE.findall(label)}
    allowed = {year}
    if target_end is not None:
        allowed.add(target_end.year)
    if fy_backfill and ped is not None:
        # N+1-Bericht: Kopf-Labels nennen das Folgejahr — ok, weil das
        # gelieferte period_end_date bereits auf das N-Ende passt.
        allowed.add(year + 1)
    if years - allowed:
        return f"column_label {label!r} nennt fremde Jahreszahl (Vorjahresspalte?)"
    if any(m in low for m in _CC_LABEL_MARKERS) or _CC_TOKEN_RE.search(low):
        return f"column_label {label!r} ist eine Constant-Currency-Spalte"
    if key not in _ADJUSTED_SIDECARS and _NON_IFRS_LABEL_RE.search(low):
        return f"column_label {label!r} ist eine non-IFRS-Spalte (IFRS-Zielspur)"
    if (
        pt in _Q_TYPES
        and info.get("derived_from") is None
        and _CUMULATIVE_LABEL_RE.search(low)
    ):
        return (
            f"column_label {label!r} ist kumuliert (H1/9M/YTD), "
            "Zielperiode aber Einzelquartal ohne derived_from"
        )
    return None


def _apply_column_gates(company, year: int, parsed: dict[str, dict[str, dict]],
                        strict_period_end: bool = False,
                        fy_backfill: bool = False) -> None:
    """Spalten-Gates (Fehlerklasse a der SAP-Abnahme) — mutiert `parsed`
    in place. Sidecars durchlaufen dieselben Checks (die FY2024-adjusted-
    Serie kam ueber die Sidecars herein)."""
    ticker = company.ticker
    for key in list(parsed):
        for pt in list(parsed[key]):
            reason = _column_gate_reason(
                company, year, key, pt, parsed[key][pt], strict_period_end,
                fy_backfill=fy_backfill,
            )
            if reason is None:
                continue
            logger.warning(
                "statement research %s/FY%s: Spalten-Gate %s/%s=%s — %s — skip",
                ticker, year, key, pt, parsed[key][pt]["value"], reason,
            )
            del parsed[key][pt]
        if not parsed[key]:
            del parsed[key]


def _is_portal_url(url) -> bool:
    """URL gehoert zu einem Finanzportal der Blocklist (inkl. Subdomains)."""
    if not isinstance(url, str) or not url:
        return False
    from urllib.parse import urlsplit

    try:
        host = (urlsplit(url).hostname or "").strip(".").lower()
    except ValueError:
        return False
    return any(host == d or host.endswith("." + d) for d in _PORTAL_BLOCKLIST)


def _apply_source_gate(parsed: dict[str, dict[str, dict]], ticker: str,
                       year: int) -> None:
    """Quellen-Hygiene (Fehlerklasse d): Werte mit Portal-URL verwerfen
    (Drittquellen liefern umgerechnete USD-Werte). Mutiert `parsed`."""
    for key in list(parsed):
        for pt in list(parsed[key]):
            url = parsed[key][pt].get("url")
            if not _is_portal_url(url):
                continue
            logger.warning(
                "statement research %s/FY%s: %s/%s=%s aus Portal-Quelle "
                "%s — keine gueltige Quelle — skip",
                ticker, year, key, pt, parsed[key][pt]["value"], url,
            )
            del parsed[key][pt]
        if not parsed[key]:
            del parsed[key]


# --- Bilanz-Stichtags-Gates (Siemens-Cash-Klasse) ---------------------------
# Instant-Keys (Stichtagswerte): Q4 und FY derselben Periode haben
# denselben Stichtag — Abweichung > 1% ist ein Etikettier-Fehler.
_INSTANT_KEYS = frozenset(k for k, _ in STATEMENT_GROUPS["balance"])
_Q4_FY_CONTRADICTION_TOL = Decimal("0.01")
# Identischer Wert (<0,1%) in Perioden mit unterschiedlichem Stichtag
# bzw. exakt ein Nachbarjahres-Actual (Kopie-Detektor des Backfills).
_VALUE_COPY_TOL = Decimal("0.001")
# Attributable-Gate: EPS ist auf attributable gerechnet — 6% Toleranz
# deckt Weighted-vs-Snapshot-Drift, faengt 8%-NCI (Siemens/Healthineers).
_ATTRIBUTABLE_TOL = Decimal("0.06")


def _apply_balance_instant_gates(company, year: int,
                                 parsed: dict[str, dict[str, dict]]) -> None:
    """Deterministischer Stichtags-Haertetest fuer Instant-Keys (Bilanz;
    Siemens-Cash-Klasse: 31.12.-Kalenderwerte mit falsch etikettiertem
    period_end passierten die Spalten-Gates). Mutiert `parsed` in place.

    (a) Q4- und FY-Wert derselben Periode muessen identisch sein
        (gleicher Stichtag): > 1% Abweichung -> BEIDE verwerfen.
    (b) Bilanzwert identisch (<0,1%) mit dem Wert desselben Keys einer
        ANDEREN Periode (unterschiedlicher Zielstichtag) im selben
        Payload -> Spaltenverrutscher, die spaetere Periode (weiter vom
        Zielstichtag entfernt) wird verworfen. Q4/FY (gleicher
        Stichtag) sind ausgenommen — dort ist Gleichheit Pflicht.
    """
    ticker = company.ticker
    for key in list(parsed):
        if key not in _INSTANT_KEYS:
            continue
        periods = parsed[key]
        fy = periods.get("FY")
        q4 = periods.get("Q4")
        if fy is not None and q4 is not None:
            fv, qv = fy["value"], q4["value"]
            denom = max(abs(fv), abs(qv))
            if denom != 0 and abs(fv - qv) > denom * _Q4_FY_CONTRADICTION_TOL:
                logger.warning(
                    "statement research %s/FY%s: Q4/FY-Stichtags-Widerspruch "
                    "%s: FY=%s vs Q4=%s (>1%%) — beide skip",
                    ticker, year, key, fv, qv,
                )
                del periods["FY"]
                del periods["Q4"]
        ordered = sorted(
            (pt for pt in periods),
            key=lambda pt: (_period_end_target(company, year, pt) or date.max, pt),
        )
        drop: set[str] = set()
        for i, early in enumerate(ordered):
            if early in drop:
                continue
            for late in ordered[i + 1:]:
                if late in drop:
                    continue
                t_early = _period_end_target(company, year, early)
                t_late = _period_end_target(company, year, late)
                if t_early is None or t_late is None or t_early == t_late:
                    continue
                ev, lv = periods[early]["value"], periods[late]["value"]
                if ev == 0 or lv == 0:
                    continue
                if abs(lv / ev - 1) <= _VALUE_COPY_TOL:
                    logger.warning(
                        "statement research %s/FY%s: %s/%s=%s identisch mit "
                        "%s (Spaltenverrutscher) — spaetere Periode skip",
                        ticker, year, key, late, lv, early,
                    )
                    drop.add(late)
        for pt in drop:
            del periods[pt]
        if not periods:
            del parsed[key]


def _shares_snapshot(db, company_id) -> Decimal | None:
    """SNAPSHOT-Aktienzahl (shares_outstanding, period_year None) fuer
    das Attributable-Gate; fehlt sie, gibt es kein Urteil."""
    row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == "shares_outstanding",
            CompanyValue.period_type == "SNAPSHOT",
            CompanyValue.numeric_value.isnot(None),
        )
        .first()
    )
    return row.numeric_value if row else None


def _ni_eps_check(parsed: dict[str, dict[str, dict]], pt: str,
                  shares: Decimal | None) -> tuple[Decimal, Decimal] | None:
    """Gemeinsame NI-vs-EPS-Kreuzrechnung (Attributable-Gate und
    Konsistenz-Schiedsrichter, keine Doppelstruktur): liefert
    (|NI / (EPS x Aktien) - 1|, EPS x Aktien) fuer die Periode — None,
    wenn nicht pruefbar (net_income/eps_diluted der Periode fehlt,
    keine Aktienzahl, implied 0)."""
    if shares is None or shares == 0:
        return None
    ni = parsed.get("net_income", {}).get(pt)
    eps = parsed.get("eps_diluted", {}).get(pt)
    if ni is None or eps is None:
        return None
    implied = eps["value"] * shares
    if implied == 0:
        return None
    return abs(ni["value"] / implied - 1), implied


def _internally_consistent(parsed: dict[str, dict[str, dict]], pt: str,
                           shares: Decimal | None) -> bool:
    """Interne Konsistenz als Schiedsrichter (SAP-Korrekturlauf):
    net_income und eps_diluted derselben Periode passen ueber die
    SNAPSHOT-Aktienzahl zusammen (<= 6%, dieselbe Rechnung wie das
    Attributable-Gate). Nicht pruefbar -> False."""
    check = _ni_eps_check(parsed, pt, shares)
    return check is not None and check[0] <= _ATTRIBUTABLE_TOL


def _apply_attributable_gate(db, company, year: int,
                             parsed: dict[str, dict[str, dict]]) -> None:
    """Attributable-Gate (SAP/Siemens-Muster: Konzern-PAT statt
    attributable NI): EPS ist auf attributable gerechnet — liefert der
    Payload net_income UND eps_diluted derselben Periode und existiert
    eine SNAPSHOT-Aktienzahl, wird |NI / (EPS x Aktien) - 1| > 6% als
    NCI-Verdacht gewertet: net_income verwerfen, eps behalten. Ohne
    Aktienzahl kein Urteil. Mutiert `parsed` in place."""
    ni = parsed.get("net_income")
    eps = parsed.get("eps_diluted")
    if not ni or not eps:
        return
    shares = _shares_snapshot(db, company.id)
    if shares is None or shares == 0:
        return
    for pt in list(ni):
        check = _ni_eps_check(parsed, pt, shares)
        if check is None:
            continue
        deviation, implied = check
        if deviation > _ATTRIBUTABLE_TOL:
            logger.warning(
                "statement research %s/FY%s: attributable-Verdacht "
                "net_income/%s=%s vs eps x shares=%s (>6%%) — net_income "
                "skip, eps bleibt",
                company.ticker, year, pt, ni[pt]["value"], implied,
            )
            del ni[pt]
    if not ni:
        del parsed["net_income"]


def _neighbor_fy_actuals(db, company_id, key: str, year: int,
                         adjusted: bool) -> dict[int, Decimal]:
    """FY-Actuals der Nachbarjahre (N-1/N+1) desselben Keys als
    Kopie-Referenzen; vertrauenswuerdige Herkunft wie _prev_actual.
    `adjusted` liest die Sidecar-Spalte (numeric_value_adjusted)."""
    rows = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year.in_((year - 1, year + 1)),
            CompanyValue.is_forecast.is_(False),
            CompanyValue.primary_method.in_(
                ("provider", "statement_research", "manual", "calculated")
            ),
        )
        .all()
    )
    out: dict[int, Decimal] = {}
    for r in rows:
        v = r.numeric_value_adjusted if adjusted else r.numeric_value
        if v is not None:
            out[r.period_year] = v
    return out


def _apply_backfill_copy_gate(db, company, year: int,
                              parsed: dict[str, dict[str, dict]]) -> None:
    """Kopie-Detektor des FY-only-Backfills: ein Wert, der exakt (<0,1%)
    einem vorhandenen FY-Actual des Nachbarjahres (N+1/N-1) desselben
    Keys entspricht, ist ein Spaltenverrutscher der Vergleichsspalte
    (SAP FY2024: teils FY2023-Werte geliefert) — verwerfen. Sidecars
    werden gegen die Sidecar-Spalte der Nachbar-Zeilen geprueft.
    Mutiert `parsed` in place."""
    ticker = company.ticker
    for key in list(parsed):
        base_key = _ADJUSTED_SIDECARS.get(key)
        refs = _neighbor_fy_actuals(
            db, company.id, base_key or key, year, adjusted=base_key is not None,
        )
        if not refs:
            continue
        for pt in list(parsed[key]):
            v = parsed[key][pt]["value"]
            if v == 0:
                continue
            for ref_year, ref in refs.items():
                if ref == 0:
                    continue
                if abs(v / ref - 1) <= _VALUE_COPY_TOL:
                    logger.warning(
                        "statement research %s/FY%s: Kopie-Detektor "
                        "%s/%s=%s entspricht dem FY%s-Actual %s "
                        "(Spaltenverrutscher) — skip",
                        ticker, year, key, pt, v, ref_year, ref,
                    )
                    del parsed[key][pt]
                    break
        if not parsed[key]:
            del parsed[key]


def _enforce_qsum(parsed: dict[str, dict[str, dict]], ticker: str, year: int) -> None:
    """FY + alle 4 Quartale geliefert und Summe > 1% daneben -> Quartale
    verwerfen, FY behalten, loggen. Mutiert `parsed` in place."""
    for key in _QSUM_ENFORCE_KEYS:
        periods = parsed.get(key)
        if not periods:
            continue
        fy = periods.get("FY")
        if fy is None or fy["value"] == 0:
            continue
        if any(q not in periods for q in _Q_TYPES):
            continue
        q_sum = sum(periods[q]["value"] for q in _Q_TYPES)
        if abs(q_sum - fy["value"]) <= abs(fy["value"]) * _QSUM_TOL:
            continue
        logger.warning(
            "statement research %s/FY%s: %s Quartalssumme %s weicht > 1%% "
            "vom FY %s ab — Quartale verworfen, FY bleibt",
            ticker, year, key, q_sum, fy["value"],
        )
        for q in _Q_TYPES:
            del periods[q]


def _yahoo_reference_map(company, year: int) -> dict:
    """Yahoo-Referenzen fuer das Cross-Check-Gate: EIN Fetch pro
    fetch_statement_research-Aufruf. Fehler -> leere Map (Gate komplett
    uebersprungen), geloggt."""
    from app.values.provider_anchor import yahoo_reference_map

    try:
        return yahoo_reference_map(company, [year])
    except Exception as e:
        logger.warning(
            "statement research %s/FY%s: Yahoo-Referenzen nicht verfuegbar "
            "— Yahoo-Cross-Check uebersprungen: %s",
            company.ticker, year, e,
        )
        return {}


YAHOO_GATE_EXCLUDED_KEYS = frozenset({
    # Definitionskonflikt: unsere Capex-Konvention ist brutto inkl.
    # immaterieller Vermoegenswerte, Yahoo fuehrt reines PP&E —
    # legitime Werte wuerden systematisch verworfen.
    "capex",
})


def _apply_yahoo_gate(parsed: dict[str, dict[str, dict]], ref_map: dict,
                      ticker: str, year: int) -> None:
    """Yahoo-Cross-Check: existiert fuer eine Zelle eine Markt-Referenz
    und weicht der Recherche-Wert um mehr als 35% davon ab
    (|research/yahoo - 1| > 0.35), wird er verworfen und geloggt. Ohne
    Referenz kein Urteil. Betragsvergleich, weil Yahoo-Rohwerte (z.B.
    capex negativ) und Recherche-Werte vor normalize_sign unterschiedliche
    Vorzeichen tragen koennen. Per-Share-Keys laufen mit demselben Band;
    Sidecars nicht (Yahoo kennt keine Non-IFRS-Spur). Das Gate laeuft
    ZUSAETZLICH zum Vorjahresband, es ersetzt es nicht. Mutiert `parsed`
    in place."""
    if not ref_map:
        return
    for key in list(parsed):
        if key in _ADJUSTED_SIDECARS or key in YAHOO_GATE_EXCLUDED_KEYS:
            continue
        for pt in list(parsed[key]):
            ref = ref_map.get((key, pt, year))
            if ref is None or ref == 0:
                continue
            v = parsed[key][pt]["value"]
            if abs(abs(v) / abs(ref) - 1) > _YAHOO_XCHECK_TOL:
                logger.warning(
                    "statement research %s/FY%s: Yahoo-Cross-Check %s/%s=%s "
                    "weicht >35%% von der Referenz %s ab — verworfen",
                    ticker, year, key, pt, v, ref,
                )
                del parsed[key][pt]


def _is_market_provider_row(row: CompanyValue) -> bool:
    """Markt-Provider-Zeile (Yahoo-Feed): primary_method 'provider' mit
    Bloomberg-Label im source_name. XBRL-Provider-Zeilen (ESEF/EDGAR)
    matchen nicht — deren Labels beginnen mit 'ESEF'/'SEC EDGAR'."""
    return (
        (row.primary_method or "") == "provider"
        and (row.source_name or "").startswith(_MARKET_PROVIDER_LABELS)
    )


def _row_replaceable(row: CompanyValue) -> bool:
    """Schreibrechte: Manual-/PDF-/XBRL-Provider-Zeilen mit Wert sind
    authoritative; leere Zeilen und LLM-/Ableitungs-Herkuenfte
    (not_found/two_stage_*/web_*/calculated/statement_research) sind
    ersetzbar (Muster consistency._derivation_replaceable). Markt-
    Provider-Zeilen (Bloomberg-Label, Alt-Bestand des Yahoo-Ankers) sind
    seit dem Kundenentscheid ebenfalls ersetzbar — die Recherche aus
    offiziellen Berichten loest sie ab.

    Eigene statement_research-Zeilen mit Wert bleiben formal ersetzbar,
    unterliegen aber zusaetzlich der Ratsche (_statement_row_suspect an
    der Write-Stelle): unverdaechtig -> Wert bleibt."""
    if row.manually_overridden or (row.from_ir_pdf and row.numeric_value is not None):
        return False
    if row.numeric_value is None:
        return True
    pm = row.primary_method or ""
    return (
        pm in _REPLACEABLE_METHODS
        or pm.startswith("two_stage")
        or pm.startswith("web")
        or _is_market_provider_row(row)
    )


def _qsum_contradicts_fy(db, company_id, key: str, year: int) -> bool:
    """Ratschen-Verdacht (b): alle 4 Quartals-Actuals des Keys existieren
    und ihre Summe widerspricht der autoritativen FY-Zeile (> 1%). FY
    autoritativ = Actual mit Wert aus statement_research oder (Nicht-
    Markt-)Provider. Lokale Nachbildung der qsum-Logik aus
    consistency.validate_cross_metrics (bewusst kein Import — das
    Modul-Geflecht wuerde zirkulaer)."""
    if key not in _QSUM_ENFORCE_KEYS:
        return False
    rows = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_year == year,
            CompanyValue.period_type.in_(_PERIODS),
            CompanyValue.is_forecast.is_(False),
            CompanyValue.numeric_value.isnot(None),
        )
        .all()
    )
    by_pt: dict[str, CompanyValue] = {}
    for r in rows:
        by_pt.setdefault(r.period_type, r)
    fy = by_pt.get("FY")
    if (
        fy is None
        or fy.numeric_value == 0
        or (fy.primary_method or "") not in ("statement_research", "provider")
        or _is_market_provider_row(fy)
    ):
        return False
    if any(q not in by_pt for q in _Q_TYPES):
        return False
    q_sum = sum(by_pt[q].numeric_value for q in _Q_TYPES)
    return abs(q_sum - fy.numeric_value) > abs(fy.numeric_value) * _QSUM_TOL


def _statement_row_suspect(db, company_id, row: CompanyValue,
                           qsum_cache: dict[str, bool] | None = None) -> bool:
    """Verdachts-Kriterien der Ratsche: (a) die Zeile traegt
    consistency_flags (qsum_mismatch, eps_ni_mismatch, ...), (b) sie ist
    Quartals-Zeile eines Keys, dessen Quartale der autoritativen FY-Zeile
    widersprechen (_qsum_contradicts_fy). `qsum_cache` (pro Lauf und
    Jahr) memoisiert den qsum-Status je Key fuer die Bedarfspruefung."""
    if row.consistency_flags:
        return True
    if row.period_type not in _Q_TYPES:
        return False
    key = row.value_key
    if qsum_cache is not None and key in qsum_cache:
        return qsum_cache[key]
    suspect = _qsum_contradicts_fy(db, company_id, key, row.period_year)
    if qsum_cache is not None:
        qsum_cache[key] = suspect
    return suspect


def _ratchet_discrepancy(old: Decimal, new: Decimal) -> bool:
    """> 1%-Abweichung des neuen Vorschlags vom behaltenen Wert.
    Betragsvergleich — der neue Wert ist noch nicht durch normalize_sign
    normalisiert."""
    if old == 0:
        return new != 0
    return abs(abs(new) / abs(old) - 1) > _RATCHET_DIFF_TOL


def _compose_source_name(info: dict, year: int) -> str:
    """quote-first: das woertliche Zitat ist der sichtbare Quellentext der
    Zelle; abgeleitete Quartale tragen den Rechenweg als Praefix. Beginnt
    nie mit https — bleibt damit fuer den naechsten Lauf ersetzbar."""
    text = info.get("quote") or f"Statement-Recherche FY{year}"
    if info.get("derived_from"):
        text = f"Abgeleitet ({info['derived_from']}): {text}"
    parts = [text[:1000]]
    if info.get("url"):
        parts.append(info["url"])
    return " | ".join(parts)[:4096]


def _slot_rows(db, company_id, key: str, pt: str, year: int) -> list[CompanyValue]:
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == pt,
            CompanyValue.period_year == year,
        )
        .order_by(CompanyValue.is_forecast.asc())
        .all()
    )


def _upsert_reported(db, company, key: str, pt: str, year: int, info: dict,
                     now) -> CompanyValue | None:
    """Berichteten Wert (is_forecast=False) in den Slot schreiben.
    Rueckgabe: geschriebene Zeile oder None (Guard griff)."""
    rows = _slot_rows(db, company.id, key, pt, year)
    if any(
        r.manually_overridden or (r.from_ir_pdf and r.numeric_value is not None)
        for r in rows
    ):
        return None

    # Zielzeile: Actual-Slot bevorzugt; existiert nur ein (ersetzbarer)
    # Forecast-Slot, wird er zum Actual umgezogen (Guidance -> berichtet).
    target = next((r for r in rows if not r.is_forecast), None)
    if target is None:
        target = next(iter(rows), None)
    if target is not None and not _row_replaceable(target):
        target.last_refresh_attempt = now
        return None

    # Ratsche (first-plausible-wins unter Gleichrangigen): eine bestehende
    # UNVERDAECHTIGE statement_research-Zeile mit Wert behaelt ihren Wert
    # — Reruns wuerfelten sonst neu statt zu konvergieren (SAP: 7 korrekte
    # Quartalswerte durch Wiederholungslauf beschaedigt). Verdaechtige
    # Zeilen (consistency_flags, qsum-Widerspruch zur FY-Zeile) bleiben
    # ersetzbar.
    if (
        target is not None
        and target.numeric_value is not None
        and (target.primary_method or "") == "statement_research"
        and not _statement_row_suspect(db, company.id, target)
    ):
        if _ratchet_discrepancy(target.numeric_value, info["value"]):
            logger.info(
                "statement research %s/FY%s: Ratsche %s/%s — bestehender "
                "Wert %s behalten, neuer Vorschlag %s (Diskrepanz >1%%)",
                company.ticker, year, key, pt,
                target.numeric_value, info["value"],
            )
        target.last_refresh_attempt = now
        db.flush()
        return None

    currency = company.currency if key in CURRENCY_KEYS else None
    if target is not None and currency_conflict(key, target.currency, currency):
        logger.warning(
            "statement research currency mismatch BLOCKED %s/%s/%s FY%s: "
            "existing=%s new=%s",
            company.ticker, key, pt, year, target.currency, currency,
        )
        target.last_refresh_attempt = now
        db.flush()
        return None

    value = normalize_sign(
        key, info["value"],
        context=f"statement-research {company.ticker}/{pt} FY{year}",
    )
    # capex ist nicht in ALWAYS_POSITIVE_KEYS (Repo-Konvention), kommt aus
    # dem Cashflow-Statement aber oft als negativer Abfluss — wir speichern
    # Betraege (Muster gaap_bridge._FORCE_ABS_KEYS). Sonst stehen FY -739
    # neben Quartalen +168 und die Delta-Zeilen rechnen Unsinn.
    if key == "capex" and value is not None:
        value = abs(value)

    if target is None:
        target = CompanyValue(
            id=uuid4(), company_id=company.id, value_key=key,
            period_type=pt, period_year=year, is_forecast=False,
        )
        # SAVEPOINT: Unique-Index-Kollision (Race mit parallelem Writer)
        # -> Slot-Zeile neu laden und Guards erneut anwenden.
        try:
            with db.begin_nested():
                db.add(target)
                db.flush()
        except IntegrityError:
            target = next(iter(_slot_rows(db, company.id, key, pt, year)), None)
            if target is None or not _row_replaceable(target):
                return None
            if currency_conflict(key, target.currency, currency):
                target.last_refresh_attempt = now
                db.flush()
                return None

    target.numeric_value = value
    target.text_value = None
    target.source_name = _compose_source_name(info, year)
    target.source_link = info.get("url")
    target.primary_method = "statement_research"
    # Alte Flags beschrieben den ersetzten Wert — der Validator setzt sie
    # fuer den neuen Wert frisch (sonst bliebe die Zeile fuer die Ratsche
    # dauerhaft verdaechtig).
    target.consistency_flags = None
    target.is_forecast = False
    target.manually_overridden = False
    target.from_ir_pdf = False
    if currency:
        target.currency = currency
    target.fetched_at = now
    target.last_refresh_attempt = now
    db.flush()
    return target


def _attach_sidecar(db, company, base_key: str, pt: str, year: int,
                    info: dict, now, base_row: CompanyValue | None,
                    note: str | None = None) -> bool:
    """Adjusted-Sidecar in numeric_value_adjusted der Basis-Zeile schreiben.
    Nur bei echten adjusted-Ausweisen; adjusted_is_protected respektieren.
    `note` ueberschreibt die Default-adjustments_note (Dokument-Stufe).
    Rueckgabe: True wenn geschrieben."""
    row = base_row
    if row is None:
        rows = _slot_rows(db, company.id, base_key, pt, year)
        if any(
            r.manually_overridden or (r.from_ir_pdf and r.numeric_value is not None)
            for r in rows
        ):
            return False
        row = next((r for r in rows if not r.is_forecast), None)
        if row is None:
            # Adjusted-only-Ausweis ohne Basis-Zeile: Traeger-Zeile mit
            # leerem numeric_value anlegen (Muster guidance_estimates).
            row = CompanyValue(
                id=uuid4(), company_id=company.id, value_key=base_key,
                period_type=pt, period_year=year, is_forecast=False,
            )
            if base_key in CURRENCY_KEYS:
                row.currency = company.currency
            row.fetched_at = now
            row.last_refresh_attempt = now
            try:
                with db.begin_nested():
                    db.add(row)
                    db.flush()
            except IntegrityError:
                row = next(
                    (r for r in _slot_rows(db, company.id, base_key, pt, year)
                     if not r.is_forecast),
                    None,
                )
                if row is None:
                    return False
    if row.manually_overridden or row.from_ir_pdf:
        return False
    if adjusted_is_protected(row.adjustments_source):
        return False
    # Sidecar-Ratsche (gleiches Prinzip wie _upsert_reported): ein
    # bestehender unverdaechtiger adjusted-Wert bleibt. Wurde die
    # Traeger-Zeile in DIESEM Lauf geschrieben (base_row gesetzt), war
    # sie verdaechtig/ersetzbar — der Sidecar zieht mit ihr um.
    if (
        base_row is None
        and row.numeric_value_adjusted is not None
        and not _statement_row_suspect(db, company.id, row)
    ):
        if _ratchet_discrepancy(row.numeric_value_adjusted, info["value"]):
            logger.info(
                "statement research %s/FY%s: Ratsche Sidecar %s/%s — "
                "bestehender Wert %s behalten, neuer Vorschlag %s "
                "(Diskrepanz >1%%)",
                company.ticker, year, base_key, pt,
                row.numeric_value_adjusted, info["value"],
            )
        return False
    # reported <= adjusted auch gegen den DB-Basiswert pruefen — die
    # Dokument-Stufe liefert Sidecars oft ohne Basis-Wert im Payload
    # (Basis kam z.B. vom ESEF-Anker oder aus Stufe 1), das
    # Parsed-Paar-Gate greift dann nicht.
    base_val = row.numeric_value
    adj_val = info["value"]
    # Richtungs-frei (Non-IFRS darf unter reported liegen): nur klare
    # Spur-Verwechslung (>150% Abstand) verwerfen.
    if (
        base_val is not None and base_val != 0
        and abs(adj_val - base_val) > abs(base_val) * _REPORTED_ADJ_BAND
    ):
        logger.warning(
            "statement research %s/FY%s: Sidecar %s/%s=%s vs reported %s "
            "weicht >150%% ab (Spur-Verwechslung?) — Sidecar skip",
            company.ticker, year, base_key, pt, adj_val, base_val,
        )
        return False
    # quote-first ('quote | url'): beginnt nie mit https — bleibt damit
    # fuer den naechsten Lauf ersetzbar (adjusted_is_protected schuetzt
    # nur 'Manual' und reine URLs).
    text = info.get("quote") or f"Adjusted-Ausweis FY{year}"
    if info.get("derived_from"):
        text = f"Abgeleitet ({info['derived_from']}): {text}"
    src_parts = [text[:400]]
    if info.get("url"):
        src_parts.append(info["url"])
    row.numeric_value_adjusted = adj_val
    row.adjustments_note = note or "Adjusted (berichtet, Statement-Recherche)"
    row.adjustments_source = " | ".join(src_parts)[:2048]
    db.flush()
    return True


def _persist_group(db, company, year: int, group: str,
                   parsed: dict[str, dict[str, dict]], now,
                   periods_reported: tuple[str, ...],
                   needed_map: dict[str, tuple[str, ...]] | None = None,
                   sidecar_note: str | None = None) -> int:
    """Einen geparsten Gruppen-Block persistieren: berichtete Perioden +
    adjusted-Sidecars; nicht gelieferte/verworfene BERICHTETE Perioden
    bekommen not_found-Platzhalter bzw. einen Refresh-Stempel.
    Unberichtete Perioden (Karenz laeuft noch) werden nicht angefasst.
    `needed_map` (Dokument-Stufe) beschraenkt Writes und Stempel auf die
    dort gelisteten beduerftigen Zellen — Stufe-1-Werte anderer Zellen
    bleiben unberuehrt. Rueckgabe: geschriebene Zeilen."""
    from app.values.persistence import stamp_attempt_and_fill_not_found

    written = 0
    written_rows: dict[tuple[str, str], CompanyValue] = {}
    base_keys = [k for k, _ in STATEMENT_GROUPS[group] if k not in _ADJUSTED_SIDECARS]
    for key in base_keys:
        target_periods = (
            needed_map.get(key, ()) if needed_map is not None else periods_reported
        )
        periods = parsed.get(key, {})
        for pt in target_periods:
            info = periods.get(pt)
            if info is None:
                continue
            row = _upsert_reported(db, company, key, pt, year, info, now)
            if row is not None:
                written_rows[(key, pt)] = row
                written += 1
        # Kein stiller Zustand: berichtete Perioden ohne Write dokumentieren
        # (Stempel auf bestehende Zeilen, not_found-Platzhalter fuer
        # komplett fehlende — rote Zelle im UI).
        unwritten = [pt for pt in target_periods if (key, pt) not in written_rows]
        if unwritten:
            stamp_attempt_and_fill_not_found(
                db, company.id, key, year, unwritten,
                currency=company.currency,
            )

    for adj_key, base_key in _ADJUSTED_SIDECARS.items():
        periods = parsed.get(adj_key)
        if not periods:
            continue
        for pt, info in periods.items():
            _attach_sidecar(
                db, company, base_key, pt, year, info, now,
                written_rows.get((base_key, pt)),
                note=sidecar_note,
            )
    return written


def _group_needs_research(db, company, year: int, group: str,
                          periods_reported: tuple[str, ...]) -> bool:
    """Bedarfspruefung pro Gruppe: hat mindestens EINE berichtete Zelle
    der Gruppe keinen autoritativen Wert (leer oder ersetzbar per
    _row_replaceable), lohnt der Claude-Call. Sind alle Zellen durch
    XBRL-Provider-/Manual-/PDF-Zeilen gedeckt (ESEF-Anker), entfaellt
    er. Markt-Provider-Zellen (Bloomberg-Label) zaehlen als beduerftig
    — sonst liefe kein Call, der den Alt-Bestand ersetzt.

    Ratsche: UNVERDAECHTIGE eigene statement_research-Zellen mit Wert
    zaehlen NICHT als beduerftig (der Write wuerde ohnehin gehalten —
    Calls fuer nichts); verdaechtige (consistency_flags/qsum-Widerspruch)
    zaehlen."""
    base_keys = [k for k, _ in STATEMENT_GROUPS[group] if k not in _ADJUSTED_SIDECARS]
    qsum_cache: dict[str, bool] = {}
    for key in base_keys:
        for pt in periods_reported:
            rows = _slot_rows(db, company.id, key, pt, year)
            target = next((r for r in rows if not r.is_forecast), None)
            if target is None:
                target = next(iter(rows), None)
            if target is None:
                return True
            if not _row_replaceable(target):
                continue
            if target.numeric_value is None:
                return True
            if (target.primary_method or "") == "statement_research":
                if _statement_row_suspect(db, company.id, target, qsum_cache):
                    return True
                continue
            # two_stage_*/web_*/calculated/Markt-Provider: ersetzbar und
            # beduerftig wie bisher.
            return True
    return False


# --- Stufe 2: Dokument-Bruecke ----------------------------------------------


def _url_allowed(url: str) -> bool:
    """Basis-Hygiene fuer Dokument-URLs aus der Stufe-1-Antwort (kein
    User-Input): nur http(s), keine IP-Literale, kein localhost/interne
    Hostnamen (SSRF-Schutz). Wird auch auf jeden Redirect-Hop angewandt."""
    import ipaddress
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").strip(".").lower()
    if not host:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        ipaddress.ip_address(host)
        return False  # IP-Literale generell verwerfen (deckt private Ranges)
    except ValueError:
        pass
    return True


def _pdf_page_count(data: bytes) -> int:
    """Seitenzahl via pypdf; unparsbares PDF -> Groessen-Heuristik
    (~50 KB/Seite), damit der 100-Seiten-Deckel trotzdem greift."""
    import io

    try:
        from pypdf import PdfReader
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return max(1, len(data) // _PDF_PAGE_BYTES_HEURISTIC)


def _extract_relevant_pages(data: bytes, group: str) -> tuple[bytes, int, int] | None:
    """Teil-PDF fuer grosse Berichte (> MAX_PDF_PAGES): Seiten mit
    Gruppen-Keywords (case-insensitive) finden, +/- 1 Nachbarseite
    mitnehmen, auf MAX_EXTRACT_PAGES deckeln und als neues PDF (pypdf
    PdfWriter) zurueckgeben. Rueckgabe: (pdf_bytes, extrahierte Seiten,
    Gesamtseiten) — kein Treffer oder unparsbar -> None (Caller skippt
    das Dokument wie bisher)."""
    import io

    try:
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(io.BytesIO(data))
        total = len(reader.pages)
        keywords = _PAGE_KEYWORDS[group]
        hits: set[int] = set()
        for i, page in enumerate(reader.pages):
            try:
                text = (page.extract_text() or "").lower()
            except Exception:
                continue
            if any(kw in text for kw in keywords):
                hits.add(i)
        if not hits:
            return None
        wanted: set[int] = set()
        for i in hits:
            wanted.update((i - 1, i, i + 1))
        selected = sorted(p for p in wanted if 0 <= p < total)[:MAX_EXTRACT_PAGES]
        writer = PdfWriter()
        for i in selected:
            writer.add_page(reader.pages[i])
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue(), len(selected), total
    except Exception as e:
        logger.warning(
            "statement research doc: Seiten-Extraktion (%s) fehlgeschlagen: %s",
            group, e,
        )
        return None


def _download_document(url: str) -> tuple[bytes, str] | None:
    """Dokument-Download mit Timeout, Groessen-Limit und manueller
    Redirect-Verfolgung (jeder Hop laeuft durch _url_allowed).
    Rueckgabe: (bytes, 'pdf'|'html') oder None. In Tests gemockt
    (conftest: Default None — kein Live-Netz)."""
    import httpx

    current = url
    # Browser-Header: IR-Seiten (z.B. sap.com) blocken Default-Clients
    # mit 403 — realistische UA/Accept-Header wie ein Browser senden.
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "application/pdf,text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
        "Referer": "https://www.google.com/",
    }
    try:
        with httpx.Client(
            timeout=_DOC_TIMEOUT_SECONDS, follow_redirects=False, headers=headers,
        ) as client:
            for _ in range(_DOC_REDIRECT_LIMIT + 1):
                if not _url_allowed(current):
                    logger.warning(
                        "statement research doc: URL verworfen (Guard): %s", current,
                    )
                    return None
                with client.stream("GET", current) as r:
                    if r.status_code in (301, 302, 303, 307, 308):
                        loc = r.headers.get("location")
                        if not loc:
                            return None
                        current = str(httpx.URL(current).join(loc))
                        continue
                    if r.status_code >= 400:
                        logger.info(
                            "statement research doc: HTTP %s fuer %s",
                            r.status_code, current,
                        )
                        return None
                    length = r.headers.get("content-length")
                    if length and length.isdigit() and int(length) > MAX_DOC_BYTES:
                        logger.info(
                            "statement research doc: %s > %d Bytes — skip",
                            current, MAX_DOC_BYTES,
                        )
                        return None
                    buf = bytearray()
                    for chunk in r.iter_bytes():
                        buf += chunk
                        if len(buf) > MAX_DOC_BYTES:
                            logger.info(
                                "statement research doc: %s ueberschreitet "
                                "%d Bytes — skip", current, MAX_DOC_BYTES,
                            )
                            return None
                    ctype = (r.headers.get("content-type") or "").lower()
                data = bytes(buf)
                if data[:5] == b"%PDF-" or "pdf" in ctype:
                    return data, "pdf"
                if "html" in ctype or "text" in ctype or data[:512].lstrip()[:1] == b"<":
                    return data, "html"
                logger.info(
                    "statement research doc: unbekannter Content-Type %r fuer %s "
                    "— skip", ctype, current,
                )
                return None
    except Exception as e:
        logger.warning("statement research doc: Download failed %s: %s", url, e)
        return None
    logger.warning("statement research doc: Redirect-Limit erreicht: %s", url)
    return None


def _needy_cells(db, company, year: int, group: str,
                 periods_reported: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """Beduerftige berichtete Zellen der Gruppe nach Stufe 1 + Ankern:
    Actual-Slot ohne Wert (leer oder not_found-Platzhalter) und nicht
    durch Manual/PDF/XBRL-Provider gesperrt. Markt-Provider-Zellen
    (Bloomberg-Alt-Bestand) zaehlen trotz Wert als beduerftig — die
    Dokument-Stufe darf sie durch Berichtswerte ersetzen. Verdaechtige
    eigene statement_research-Zellen (Ratsche: consistency_flags/
    qsum-Widerspruch) zaehlen ebenfalls; unverdaechtige nicht."""
    needed: dict[str, tuple[str, ...]] = {}
    base_keys = [k for k, _ in STATEMENT_GROUPS[group] if k not in _ADJUSTED_SIDECARS]
    qsum_cache: dict[str, bool] = {}
    for key in base_keys:
        pts = []
        # FY-first (Nicht-US-Konvention: FY-Reihe traegt die H-Rendite):
        # _PERIODS-Ordnung erzwingen, damit FY im Dokument-Prompt und in
        # der Kandidaten-Deckung immer vor den Quartalen steht.
        for pt in (p for p in _PERIODS if p in periods_reported):
            rows = _slot_rows(db, company.id, key, pt, year)
            if any(
                r.manually_overridden or (r.from_ir_pdf and r.numeric_value is not None)
                for r in rows
            ):
                continue
            actual = next((r for r in rows if not r.is_forecast), None)
            if actual is not None and not _row_replaceable(actual):
                continue
            if (
                actual is None
                or actual.numeric_value is None
                or _is_market_provider_row(actual)
                or (
                    (actual.primary_method or "") == "statement_research"
                    and _statement_row_suspect(db, company.id, actual, qsum_cache)
                )
            ):
                pts.append(pt)
        if pts:
            needed[key] = tuple(pts)
    return needed


def _collect_period_urls(data: dict, group: str) -> dict[str, list[str]]:
    """Dokument-URLs aus der ROHEN Stufe-1-Antwort einsammeln — auch aus
    Eintraegen mit value null (die Websuche liefert die URL, kann den
    Wert aber nicht lesen). Rueckgabe: {period: [urls, dedupliziert]}."""
    urls: dict[str, list[str]] = {}
    for key, _ in STATEMENT_GROUPS[group]:
        entry = data.get(key)
        if not isinstance(entry, dict):
            continue
        for pt in _PERIODS:
            info = entry.get(pt)
            if not isinstance(info, dict):
                continue
            url = info.get("url")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            # Portal-URLs sind keine Berichtsdokumente (Quellen-Hygiene).
            if _is_portal_url(url):
                continue
            bucket = urls.setdefault(pt, [])
            if url not in bucket:
                bucket.append(url)
    return urls


def _candidate_docs(period_urls: dict[str, list[str]],
                    needy_periods, group: str = "income") -> list[str]:
    """URLs der beduerftigen Perioden deduplizieren und sortieren.

    Basis-Reihenfolge: Abdeckung (ein Halbjahresbericht deckt Q2, ein
    Quartalsbericht Q1 usw. — mehr beduerftige Perioden zuerst). Darueber
    die Gruppen-Priorisierung (stabile Sortierung erhaelt die
    Coverage-Reihenfolge innerhalb der Stufen):
    - balance: Halbjahres-/Geschaeftsberichte zuerst (der Schulden-Split
      steht in der Finanzverbindlichkeiten-Note, die die knappen
      Quartals-Statements nicht haben), dann Quartals-Statements.
    - income/cashflow: Statements zuerst, Geschaeftsberichte als letzte
      Kandidaten (seit der Seiten-Extraktion zugelassen statt nie).

    FY-first-Budget (zwischen Abdeckung und Gruppen-Priorisierung):
    Dokumente, die eine beduerftige FY-Zelle decken, kommen vor reinen
    Quartals-Kandidaten dran — das Call-Budget der Dokument-Stufe
    fliesst zuerst in die FY-Reihe (Nicht-US-Konvention: die H-Rendite
    rechnet auf FY-Basis, Quartale bleiben Best-Effort). Die
    Gruppen-Priorisierung bleibt dominant (Schulden-Split-Note)."""
    cover: dict[str, set] = {}
    order: list[str] = []
    for pt in needy_periods:
        for url in period_urls.get(pt, ()):
            if url not in cover:
                cover[url] = set()
                order.append(url)
            cover[url].add(pt)
    order.sort(key=lambda u: -len(cover[u]))  # stabil: Ties in Fundreihenfolge
    if "FY" in needy_periods:
        order.sort(key=lambda u: 0 if "FY" in cover[u] else 1)
    if group == "balance":
        order.sort(
            key=lambda u: 0 if any(
                m in u.lower() for m in _BALANCE_PRIORITY_URL_MARKERS
            ) else 1,
        )
    else:
        order.sort(
            key=lambda u: 1 if any(
                m in u.lower() for m in _ANNUAL_REPORT_URL_MARKERS
            ) else 0,
        )
    return order


def _build_doc_system_prompt(company, year: int, group: str,
                             fy_backfill: bool = False) -> str:
    currency = getattr(company, "currency", None) or "EUR"
    label = _GROUP_LABELS[group]
    backfill_sentence = ""
    if fy_backfill:
        backfill_sentence = (
            f"Das Dokument ist ein Bericht des Folgejahres {year + 1}: "
            f"die Werte fuer {year} stehen in der VORJAHRES-"
            "Vergleichsspalte — lies diese Vergleichsspalte und gib "
            "deren column_label und period_end_date an. "
        )
    return (
        f"Extrahiere aus dem beigefuegten offiziellen Bericht von "
        f"{company.name} ({company.ticker}) die angeforderten "
        f"{label}-Werte fuer Geschaeftsjahr {year} — AUSSCHLIESSLICH "
        "exakte Werte aus den Tabellen des Berichts (Konzernabschluss-/"
        "Kennzahlentabellen), keine gerundeten Freitextzahlen. Weist der "
        "Bericht IFRS- und non-IFRS-Spalten nebeneinander aus, nimm die "
        "IFRS-Spalte fuer die Basis-Keys und die non-IFRS-Spalte fuer "
        "die *_adjusted-Keys. Abgeleitete Quartale (z.B. Q2 = H1 - Q1) "
        "nur, wenn BEIDE Bausteine im Dokument stehen — dann "
        'derived_from setzen (z.B. "H1-Q1"); sonst value null. Das gilt '
        "ausdruecklich AUCH fuer eps_diluted (Naeherung) und die "
        "*_adjusted-Keys. Gib pro Wert die EXAKT abgeschriebene "
        "Spaltenueberschrift (column_label) und den Stichtag der Spalte "
        "(period_end_date, ISO) an — ohne period_end_date wird der Wert "
        "verworfen. "
        + backfill_sentence
        + _stichtag_sentence(company, year)
        + f"Absolute Betraege in {currency}-Basiseinheiten "
        "(z.B. '5,8 Mrd' -> 5800000000), EPS je Aktie — Werte in der "
        f"Berichtswaehrung ({currency}). Steht ein "
        "angeforderter Wert nachweislich nicht im Dokument: value null. "
        "Antworte NUR mit einem JSON-Objekt nach dem Schema in der "
        "User-Nachricht — kein Text ausserhalb des JSON, keine "
        "Markdown-Fences."
    )


def _build_doc_user_prompt(company, year: int, group: str,
                           needed: dict[str, tuple[str, ...]],
                           doc_url: str) -> str:
    specs = dict(STATEMENT_GROUPS[group])
    adj_keys = [
        adj for adj, base in _ADJUSTED_SIDECARS.items()
        if adj in specs and base in specs
    ]
    lines = [
        f"Noch fehlende Werte fuer {company.name} ({company.ticker}), "
        f"Geschaeftsjahr {year} ({_GROUP_LABELS[group]}), aus dem "
        f"Dokument {doc_url}:",
        "",
    ]
    req_keys = list(needed)
    for key in req_keys:
        lines.append(f"- {key} ({specs[key]}): Perioden {', '.join(needed[key])}")
    for adj in adj_keys:
        lines.append(
            f"- {adj} ({specs[adj]}): alle im Dokument ausgewiesenen Perioden"
        )
    fields = ",\n".join(
        f'  "{key}": {{"FY": ENTRY, "Q1": ENTRY, "Q2": ENTRY, '
        '"Q3": ENTRY, "Q4": ENTRY}'
        for key in req_keys + adj_keys
    )
    lines += [
        "",
        "Antworte mit JSON exakt nach diesem Schema:",
        "",
        "{",
        fields,
        "}",
        "",
        f"ENTRY = {_ENTRY_FIELDS}",
        "",
        "quote = woertliches Zitat/Tabellenzeile aus dem Dokument; "
        "url = Quelle-URL (null erlaubt, Dokument-URL wird ergaenzt); "
        + _COLUMN_FIELDS_EXPLANATION + " "
        "derived_from = Rechenweg bei abgeleiteten Quartalen, sonst "
        "null; nicht im Dokument = value null.",
    ]
    return "\n".join(lines)


def _call_claude_document(company, year: int, group: str,
                          needed: dict[str, tuple[str, ...]],
                          doc_bytes: bytes, kind: str, doc_url: str,
                          cost_tracker=None,
                          fy_backfill: bool = False) -> dict | None:
    """EIN Claude-Call mit dem Berichtsdokument: PDF als document-Block
    (base64), HTML als extrahierter Text im Prompt (Muster
    gaap_bridge._clean_html). In Tests gemockt."""
    import base64

    import app.llm.claude as claude_mod
    from app.llm.rate_limiter import claude_limiter
    from app.llm.json_utils import extract_json

    client = claude_mod.get_client()
    user_text = _build_doc_user_prompt(company, year, group, needed, doc_url)
    content: list[dict] = []
    if kind == "pdf":
        content.append({
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.b64encode(doc_bytes).decode("ascii"),
            },
        })
    else:
        from app.values.adjusted_enrichment import _clean_html
        text = _clean_html(
            doc_bytes.decode("utf-8", errors="replace")
        )[:_HTML_TEXT_CAP_CHARS]
        user_text = f"Berichtstext ({doc_url}):\n{text}\n\n{user_text}"
    content.append({"type": "text", "text": user_text})

    def _do_call():
        return client.messages.create(
            model=EXTRACT_MODEL,
            max_tokens=MAX_TOKENS,
            temperature=0,
            system=_build_doc_system_prompt(
                company, year, group, fy_backfill=fy_backfill,
            ),
            messages=[{"role": "user", "content": content}],
        )

    response = claude_limiter.call(_do_call)
    if cost_tracker is not None:
        cost_tracker.add_response(response, EXTRACT_MODEL)
    parts = [getattr(block, "text", None) for block in response.content]
    raw = "\n".join(p for p in parts if p).strip()
    try:
        data = extract_json(raw)
    except ValueError as e:
        logger.warning(
            "statement research doc: kein JSON in Claude-Antwort "
            "(%s FY%s %s): %s", company.ticker, year, group, e,
        )
        return None
    return data if isinstance(data, dict) else None


def _document_stage(db, company, year: int, group: str, raw_data: dict,
                    periods_reported: tuple[str, ...], now,
                    cost_tracker=None, ref_map: dict | None = None,
                    fy_backfill: bool = False) -> int:
    """Stufe 2: verbliebene beduerftige Zellen aus den Berichts-
    Dokumenten der Stufe-1-Antwort fuellen. Max. MAX_DOC_CALLS
    Dokument-Calls (Bilanz: MAX_DOC_CALLS_BALANCE); Downloads mit
    Guards, aus PDFs > MAX_PDF_PAGES wird per Keyword-Match ein
    Teil-PDF extrahiert (kein Treffer -> skip). `fy_backfill`: die
    Kandidaten sind N+1-Berichte (Vergleichsspalten-Prompt der Stufe 1
    liefert deren URLs) — Doc-Prompt und Gates laufen im
    Vergleichsspalten-Modus. Rueckgabe: geschriebene Zeilen."""
    max_doc_calls = MAX_DOC_CALLS_BALANCE if group == "balance" else MAX_DOC_CALLS
    needed = _needy_cells(db, company, year, group, periods_reported)
    if not needed:
        return 0
    needy_periods = [
        pt for pt in _PERIODS
        if any(pt in pts for pts in needed.values())
    ]
    period_urls = _collect_period_urls(raw_data, group)
    candidates = _candidate_docs(period_urls, needy_periods, group)
    if not candidates:
        logger.info(
            "statement research doc %s/FY%s %s: Restbedarf, aber keine "
            "Dokument-URLs aus Stufe 1", company.ticker, year, group,
        )
        return 0

    written = 0
    calls = 0
    for url in candidates:
        if calls >= max_doc_calls:
            logger.info(
                "statement research doc %s/FY%s %s: Kosten-Deckel "
                "(%d Dokument-Calls) erreicht",
                company.ticker, year, group, max_doc_calls,
            )
            break
        needed = _needy_cells(db, company, year, group, periods_reported)
        if not needed:
            break
        doc = _download_document(url)
        if doc is None:
            continue
        doc_bytes, kind = doc
        if kind == "pdf":
            pages = _pdf_page_count(doc_bytes)
            if pages > MAX_PDF_PAGES:
                # Geschaeftsbericht-Format: nicht mehr skippen, sondern
                # die relevanten Seiten als Teil-PDF extrahieren.
                extracted = _extract_relevant_pages(doc_bytes, group)
                if extracted is None:
                    logger.info(
                        "statement research doc %s/FY%s %s: %s hat %d "
                        "Seiten (> %d) und keine Keyword-Treffer — skip",
                        company.ticker, year, group, url, pages,
                        MAX_PDF_PAGES,
                    )
                    continue
                doc_bytes, n_extracted, n_total = extracted
                logger.info(
                    "statement research doc %s/FY%s %s: Teil-PDF %d von "
                    "%d Seiten extrahiert aus %s",
                    company.ticker, year, group, n_extracted, n_total, url,
                )
        calls += 1
        # fy_backfill nur im Backfill-Modus durchreichen — der
        # Default-Pfad bleibt call-kompatibel (Mocks/Signatur).
        doc_call_kwargs = {"cost_tracker": cost_tracker}
        if fy_backfill:
            doc_call_kwargs["fy_backfill"] = True
        try:
            data = _call_claude_document(
                company, year, group, needed, doc_bytes, kind, url,
                **doc_call_kwargs,
            )
        except Exception as e:
            logger.warning(
                "statement research doc: Claude-Call failed fuer %s FY%s "
                "%s (%s): %s", company.ticker, year, group, url, e,
            )
            continue
        if not data:
            continue
        parsed = _parse_payload(data, group)
        for key in list(parsed):
            for pt in list(parsed[key]):
                if pt not in periods_reported:
                    del parsed[key][pt]
            if not parsed[key]:
                del parsed[key]
        # Spalten-Gates: Stufe 2 strikt — das Dokument liegt vor, der
        # Stichtag steht in der Tabelle; fehlt period_end_date -> skip.
        _apply_column_gates(company, year, parsed, strict_period_end=True,
                            fy_backfill=fy_backfill)
        _apply_source_gate(parsed, company.ticker, year)
        _apply_balance_instant_gates(company, year, parsed)
        _apply_attributable_gate(db, company, year, parsed)
        if fy_backfill:
            _apply_backfill_copy_gate(db, company, year, parsed)
        # url-Fallback: Zellen ohne eigene Quelle tragen die Dokument-URL.
        for key in parsed:
            for pt in parsed[key]:
                if not parsed[key][pt].get("url"):
                    parsed[key][pt]["url"] = url
        _apply_gates(db, company, year, parsed)
        _apply_yahoo_gate(parsed, ref_map or {}, company.ticker, year)
        _enforce_qsum(parsed, company.ticker, year)
        # needed_map beschraenkt Writes UND not_found-Stempel auf die
        # beduerftigen Zellen — Dokument gelesen, Wert nicht enthalten
        # -> not_found (nur berichtete Perioden, wie Stufe 1).
        wrote = _persist_group(
            db, company, year, group, parsed, now, periods_reported,
            needed_map=needed, sidecar_note=DOC_SIDECAR_NOTE,
        )
        written += wrote
        logger.info(
            "statement research doc %s/FY%s %s: %d Zeilen aus %s",
            company.ticker, year, group, wrote, url,
        )
    return written


def fetch_statement_research(db, company, year: int, cost_tracker=None,
                             groups=None, periods=None) -> int:
    """Berichtete Fundamentals eines Nicht-US-Filers fuer ein Jahr holen:
    EIN Claude-Websuche-Call pro Statement-Gruppe (max. 3 Calls).

    Fuellt nur, was nach den vorgeschalteten Ankern (PDF-Locks, ESEF-
    Provider) ersetzbar oder leer ist — Schreibrechte siehe
    _row_replaceable (Markt-Provider-Altbestand ist ersetzbar). Der
    Yahoo-Feed schreibt keine Werte mehr, er dient nur noch als
    Cross-Check-Referenz (_apply_yahoo_gate). `groups` (optional)
    beschraenkt auf eine Teilmenge der Gruppen (z.B. fuer den
    Vorjahres-Backfill einzelner Keys).

    `periods` (optional, z.B. ('FY',)): beschraenkt Prompts, Writes und
    not_found-Stempel auf diese Perioden — FY-only-Modus fuer den
    N-2-Backfill (Nicht-US-Konvention: die H-Rendite rechnet auf
    FY-Basis, Quartalsluecken sind akzeptabel). Persistenz und Gates
    bleiben unveraendert, der Lauf ist deutlich billiger (1-3 Calls).

    Nur Nicht-US-Filer (US-Filer laufen ueber EDGAR/8-K-Bruecke) — sonst
    0. Rueckgabe: Anzahl geschriebener Zeilen.
    """
    from app.calculations.lock import is_us_company
    from app.config import settings

    if is_us_company(company):
        return 0
    if not settings.anthropic_api_key:
        return 0

    # Karenz-Gate: nur berichtete Perioden werden geschrieben/gestempelt.
    # Ist noch keine Periode des Jahres berichtet, gibt es nichts zu
    # recherchieren — kein Claude-Call.
    periods_reported = _reported_periods(company, year)
    prompt_periods: tuple[str, ...] | None = None
    if periods is not None:
        prompt_periods = tuple(pt for pt in _PERIODS if pt in set(periods))
        periods_reported = tuple(
            pt for pt in periods_reported if pt in prompt_periods
        )
    # FY-only-Backfill: Quelle ist die Vergleichsspalte des N+1-Berichts
    # (Prompts + Gate-Jahresmenge + Kopie-Detektor, siehe Docstring).
    fy_backfill = prompt_periods == ("FY",)
    if not periods_reported:
        logger.info(
            "statement research %s/FY%s: keine berichtete Periode (Karenz) — skip",
            company.ticker, year,
        )
        return 0

    group_names = [g for g in STATEMENT_GROUPS if groups is None or g in groups]
    now = datetime.now(timezone.utc)
    total = 0
    # Yahoo-Referenzen fuer das Cross-Check-Gate: einmal pro Aufruf, lazy
    # (erst wenn eine Gruppe wirklich recherchiert wird).
    ref_map: dict | None = None
    for group in group_names:
        # Bedarfspruefung: Gruppe ohne ersetzbare/leere berichtete Zelle
        # (alles vom Provider-Anker gedeckt) -> kein Claude-Call.
        if not _group_needs_research(db, company, year, group, periods_reported):
            logger.info(
                "statement research %s/FY%s %s: alle berichteten Zellen "
                "gedeckt — kein Claude-Call",
                company.ticker, year, group,
            )
            continue
        if ref_map is None:
            ref_map = _yahoo_reference_map(company, year)
        # periods nur im Restriktions-Modus durchreichen — der Default-Pfad
        # bleibt call-kompatibel (Mocks/Signatur unveraendert).
        call_kwargs = {"cost_tracker": cost_tracker}
        if prompt_periods is not None:
            call_kwargs["periods"] = prompt_periods
        try:
            data = _call_claude(company, year, group, **call_kwargs)
        except Exception as e:
            logger.warning(
                "statement research: Claude-Call failed fuer %s FY%s %s: %s",
                company.ticker, year, group, e,
            )
            continue
        if not data:
            continue
        parsed = _parse_payload(data, group)
        # Gelieferte Werte unberichteter Perioden verwerfen — als haette
        # das Modell sie nie geliefert (kein Write, kein Stempel).
        for key in list(parsed):
            for pt in list(parsed[key]):
                if pt not in periods_reported:
                    del parsed[key][pt]
            if not parsed[key]:
                del parsed[key]
        # Spalten-Gates: Stufe 1 lenient bei fehlendem period_end_date
        # (Websuche-Snippets nennen den Stichtag oft nicht).
        _apply_column_gates(company, year, parsed, strict_period_end=False,
                            fy_backfill=fy_backfill)
        _apply_source_gate(parsed, company.ticker, year)
        _apply_balance_instant_gates(company, year, parsed)
        _apply_attributable_gate(db, company, year, parsed)
        if fy_backfill:
            _apply_backfill_copy_gate(db, company, year, parsed)
        _apply_gates(db, company, year, parsed)
        _apply_yahoo_gate(parsed, ref_map, company.ticker, year)
        _enforce_qsum(parsed, company.ticker, year)
        wrote = _persist_group(db, company, year, group, parsed, now, periods_reported)
        total += wrote
        logger.info(
            "statement research %s/FY%s %s: %d Zeilen geschrieben",
            company.ticker, year, group, wrote,
        )
        # Stufe 2 (Dokument-Bruecke): nur wenn nach Stufe 1 + Ankern noch
        # beduerftige berichtete Zellen uebrig sind — Trigger/Kosten-Deckel
        # in _document_stage.
        total += _document_stage(
            db, company, year, group, data, periods_reported, now,
            cost_tracker=cost_tracker, ref_map=ref_map,
            fy_backfill=fy_backfill,
        )
    db.flush()
    return total
