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

Der Prompt ist bewusst OHNE Verbots-/Regellisten — die deterministischen
Code-Gates sind die einzige Verteidigung (Muster guidance_estimates):
  1. Einheiten-Check: Absolutwerte >= 1 Mio (ausser Per-Share-Keys),
  2. Vorjahresband 40-160% gegen das Vorjahres-Ist DERSELBEN Periode
     (fehlt es: uebersprungen; Sign-Flip/Turnaround erlaubt),
  3. Spur-Plausibilitaet bei Paaren: reported vs adjusted duerfen max.
     60% auseinanderliegen (Non-IFRS darf UNTER reported liegen — es
     schliesst auch Einmal-Gewinne aus, SAP-Muster; nur klare
     Spur-Verwechslungen werden verworfen),
  4. qsum-Enforcement: FY + alle 4 Quartale geliefert und Summe > 1%
     daneben -> Quartale verwerfen, FY behalten, loggen,
  5. Yahoo-Cross-Check (Kundenentscheid: der Marktdaten-Feed schreibt
     fuer Nicht-US keine Werte mehr, er gated nur noch): existiert eine
     Yahoo-Referenz und weicht der Recherche-Wert >35% ab, wird er
     verworfen; ohne Referenz kein Urteil (_apply_yahoo_gate).

Schreib-Invarianten wie ueberall: normalize_sign, currency_conflict,
SAVEPOINT-Slot-Muster (uq_company_values_slot). Schreibrechte: Manual-/
PDF-/XBRL-Provider-Zeilen mit Wert bleiben; not_found/two_stage_*/web_*/
calculated/statement_research und Markt-Provider-Zeilen (Bloomberg-
Label, Yahoo-Altbestand) sind ersetzbar. source_name ist
quote-first ("<quote> | <url>", beginnt nie mit https — bleibt damit
fuer den naechsten Lauf ersetzbar). Kein Beleg -> null -> not_found-
Platzhalter (rote Zelle) via stamp_attempt_and_fill_not_found.

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
pro Gruppe/Jahr (Kosten-Deckel). Persistenz/Gates identisch zu Stufe 1;
non-IFRS-Sidecars mit adjustments_note 'Non-IFRS (Berichts-PDF)'.
Dokument gelesen, Wert nachweislich nicht enthalten -> not_found.
"""
import logging
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
        ("net_income", "Konzernergebnis den Aktionaeren des Mutterunternehmens zurechenbar (attributable)"),
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
        ("st_investments", "kurzfristige Wertpapiere/Finanzanlagen (Bilanzstichtag)"),
        ("st_debt", "kurzfristige Finanzschulden (Bilanzstichtag)"),
        ("lt_debt", "langfristige Finanzschulden (Bilanzstichtag)"),
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
_REPORTED_ADJ_BAND = Decimal("0.60")

# Ersetzbare Herkuenfte (Muster _derivation_replaceable in consistency,
# erweitert um die eigene Signatur, damit der naechste Lauf seine
# Vorgaenger-Zeilen aktualisieren darf).
_REPLACEABLE_METHODS = ("not_found", "calculated", "statement_research")

# Markt-Provider-Erkennung (Migration bestehender Yahoo-Werte): der
# Marktdaten-Feed schreibt primary_method='provider' mit dem Label
# 'Bloomberg' (YahooFinanceProvider.name, auch Varianten wie 'Bloomberg
# (Close ...)'). Berichts-Provider tragen andere Labels ('ESEF ...',
# 'SEC EDGAR ...') und bleiben gesperrt. NUR die Statement-Recherche darf
# Markt-Provider-Zeilen ersetzen (Kundenentscheid: Feed ist keine
# Wertequelle) — die Ableitungs-Guards (consistency) respektieren
# 'provider' unveraendert.
_MARKET_PROVIDER_LABEL = "Bloomberg"

# Yahoo-Cross-Check-Gate: weites Band, weil der Feed selbst ungenau ist.
_YAHOO_XCHECK_TOL = Decimal("0.35")

# --- Stufe 2: Dokument-Bruecke ----------------------------------------------
# Kosten-Deckel: max. 2 Dokument-Calls pro Gruppe/Jahr.
MAX_DOC_CALLS = 2
# Download-Limits: ~20 MB, Berichts-PDFs > 100 Seiten (Geschaeftsberichte)
# werden uebersprungen — die API akzeptiert nur ~100 Seiten sinnvoll,
# Quartalsmitteilungen/Halbjahresberichte sind kurz genug.
MAX_DOC_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 100
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
    '"url": <string|null>, "derived_from": <string|null>}'
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


def _build_system_prompt(company, year: int, group: str) -> str:
    fy_end = _fy_end_date(company, year).isoformat()
    currency = getattr(company, "currency", None) or "EUR"
    label = _GROUP_LABELS[group]
    if group == "balance":
        period_sentence = (
            "Bilanzwerte sind Stichtagswerte: FY = Stand am "
            "Geschaeftsjahresende (identisch Q4), Q1-Q3 = Stand am "
            "jeweiligen Quartalsende. "
        )
    else:
        period_sentence = (
            "Viele Firmen berichten Halbjahres- statt Q2-Zahlen: rechne "
            "dann Q2 = H1 - Q1 bzw. Q3 = 9M - H1 (und Q4 = FY - 9M) und "
            'markiere solche abgeleiteten Quartale im Feld derived_from '
            '(z.B. "H1-Q1", "9M-H1", "FY-9M"); direkt berichtete Werte '
            "haben derived_from null. "
        )
    return (
        f"Extrahiere fuer {company.name} ({company.ticker}) "
        f"Geschaeftsjahr {year} (Ende {fy_end}) aus den OFFIZIELLEN "
        "Berichten (Geschaeftsbericht, Halbjahres-/Quartalsmitteilungen "
        f"der IR-Seite) die folgenden {label}-Werte fuer das Gesamtjahr "
        "(FY) und alle verfuegbaren Quartale — exakte Tabellenwerte, "
        "keine gerundeten Freitextzahlen. Pro Wert: Quelle-URL und "
        "woertliches Zitat bzw. Tabellenzeile. "
        + period_sentence
        + f"Absolute Betraege in {currency}-Basiseinheiten "
        "(z.B. '5,8 Mrd' -> 5800000000), EPS je Aktie. Nicht berichtete "
        "Perioden: value null. Gib fuer JEDE Periode die URL des "
        "offiziellen Berichts-PDFs/HTML im Feld url an — auch wenn du "
        "den Wert nicht extrahieren kannst (dann value null, url "
        "trotzdem gesetzt). Antworte NUR mit einem JSON-Objekt nach "
        "dem Schema in der User-Nachricht — kein Text ausserhalb des "
        "JSON, keine Markdown-Fences."
    )


def _build_user_prompt(company, year: int, group: str) -> str:
    specs = STATEMENT_GROUPS[group]
    lines = [
        f"Werte fuer {company.name} ({company.ticker}), "
        f"Geschaeftsjahr {year} ({_GROUP_LABELS[group]}):",
        "",
    ]
    for key, desc in specs:
        lines.append(f"- {key}: {desc}")
    fields = ",\n".join(
        f'  "{key}": {{"FY": ENTRY, "Q1": ENTRY, "Q2": ENTRY, '
        '"Q3": ENTRY, "Q4": ENTRY}'
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
        "wenn value null bleibt); derived_from = Rechenweg bei "
        "abgeleiteten Quartalen, sonst null; nicht berichtet = "
        "value null.",
    ]
    return "\n".join(lines)


def _call_claude(company, year: int, group: str, cost_tracker=None) -> dict | None:
    """EIN Claude-Call mit Websuche fuer eine Statement-Gruppe. In Tests
    gemockt (conftest blockt get_client)."""
    import app.llm.claude as claude_mod
    from app.llm.rate_limiter import claude_limiter
    from scripts.two_stage_research import _extract_json

    client = claude_mod.get_client()

    def _do_call():
        return client.messages.create(
            model=EXTRACT_MODEL,
            max_tokens=MAX_TOKENS,
            temperature=0,
            system=_build_system_prompt(company, year, group),
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": WEB_SEARCH_MAX_USES,
            }],
            messages=[{
                "role": "user",
                "content": _build_user_prompt(company, year, group),
            }],
        )

    response = claude_limiter.call(_do_call)
    if cost_tracker is not None:
        cost_tracker.add_response(response, EXTRACT_MODEL)
    parts = [getattr(block, "text", None) for block in response.content]
    raw = "\n".join(p for p in parts if p).strip()
    try:
        data = _extract_json(raw)
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
    return {
        "value": value,
        "quote": quote.strip() if isinstance(quote, str) and quote.strip() else None,
        "url": url if isinstance(url, str) and url.startswith(("http://", "https://")) else None,
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
    """Vorjahres-Ist derselben Periode: FY vs FY, Qx vs Qx."""
    row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == period_type,
            CompanyValue.period_year == year - 1,
            CompanyValue.is_forecast.is_(False),
            CompanyValue.numeric_value.isnot(None),
        )
        .first()
    )
    return row.numeric_value if row else None


def _apply_gates(db, company, year: int, parsed: dict[str, dict[str, dict]]) -> None:
    """Deterministische Gates — mutiert `parsed` in place.

    1. Einheiten-Check: Absolutwerte unter 1 Mio (aber != 0) sind fast
       immer eine fehlende Skalierung (ausser Per-Share-Keys).
    2. Vorjahresband 40-160% gegen das Vorjahres-Ist derselben Periode
       (fehlt es: uebersprungen; Sign-Flip erlaubt). Sidecars laufen
       ueber das Paar-Gate, nicht ueber das Band.
    3. reported <= adjusted + 1% bei Paaren — Verstoss verwirft beide
       Werte der Periode.
    """
    ticker = company.ticker
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
            # 60% Abstand zwischen den Spuren.
            if b_val != 0 and abs(a_val - b_val) > abs(b_val) * _REPORTED_ADJ_BAND:
                logger.warning(
                    "statement research %s/FY%s: %s/%s=%s vs %s=%s weicht "
                    ">60%% ab (Spur-Verwechslung?) — beide skip",
                    ticker, year, base_key, pt, b_val, adj_key, a_val,
                )
                del base[pt]
                del adj[pt]


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
        if key in _ADJUSTED_SIDECARS:
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
        and (row.source_name or "").startswith(_MARKET_PROVIDER_LABEL)
    )


def _row_replaceable(row: CompanyValue) -> bool:
    """Schreibrechte: Manual-/PDF-/XBRL-Provider-Zeilen mit Wert sind
    authoritative; leere Zeilen und LLM-/Ableitungs-Herkuenfte
    (not_found/two_stage_*/web_*/calculated/statement_research) sind
    ersetzbar (Muster consistency._derivation_replaceable). Markt-
    Provider-Zeilen (Bloomberg-Label, Alt-Bestand des Yahoo-Ankers) sind
    seit dem Kundenentscheid ebenfalls ersetzbar — die Recherche aus
    offiziellen Berichten loest sie ab."""
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
    # Forecast-Slot, wird er zum Actual umgezogen (Guidance -> berichtet,
    # Muster apply_to_db).
    target = next((r for r in rows if not r.is_forecast), None)
    if target is None:
        target = next(iter(rows), None)
    if target is not None and not _row_replaceable(target):
        target.last_refresh_attempt = now
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
    # reported <= adjusted auch gegen den DB-Basiswert pruefen — die
    # Dokument-Stufe liefert Sidecars oft ohne Basis-Wert im Payload
    # (Basis kam z.B. vom ESEF-Anker oder aus Stufe 1), das
    # Parsed-Paar-Gate greift dann nicht.
    base_val = row.numeric_value
    adj_val = info["value"]
    # Richtungs-frei (Non-IFRS darf unter reported liegen): nur klare
    # Spur-Verwechslung (>60% Abstand) verwerfen.
    if (
        base_val is not None and base_val != 0
        and abs(adj_val - base_val) > abs(base_val) * _REPORTED_ADJ_BAND
    ):
        logger.warning(
            "statement research %s/FY%s: Sidecar %s/%s=%s vs reported %s "
            "weicht >60%% ab (Spur-Verwechslung?) — Sidecar skip",
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
    from scripts.two_stage_research import stamp_attempt_and_fill_not_found

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
    — sonst liefe kein Call, der den Alt-Bestand ersetzt."""
    base_keys = [k for k, _ in STATEMENT_GROUPS[group] if k not in _ADJUSTED_SIDECARS]
    for key in base_keys:
        for pt in periods_reported:
            rows = _slot_rows(db, company.id, key, pt, year)
            target = next((r for r in rows if not r.is_forecast), None)
            if target is None:
                target = next(iter(rows), None)
            if target is None or _row_replaceable(target):
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
    Dokument-Stufe darf sie durch Berichtswerte ersetzen."""
    needed: dict[str, tuple[str, ...]] = {}
    base_keys = [k for k, _ in STATEMENT_GROUPS[group] if k not in _ADJUSTED_SIDECARS]
    for key in base_keys:
        pts = []
        for pt in periods_reported:
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
            bucket = urls.setdefault(pt, [])
            if url not in bucket:
                bucket.append(url)
    return urls


def _candidate_docs(period_urls: dict[str, list[str]],
                    needy_periods) -> list[str]:
    """URLs der beduerftigen Perioden deduplizieren und nach Abdeckung
    sortieren (ein Halbjahresbericht deckt Q2, ein Quartalsbericht Q1
    usw. — Dokumente, die mehr beduerftige Perioden abdecken, zuerst)."""
    cover: dict[str, set] = {}
    order: list[str] = []
    for pt in needy_periods:
        for url in period_urls.get(pt, ()):
            if url not in cover:
                cover[url] = set()
                order.append(url)
            cover[url].add(pt)
    order.sort(key=lambda u: -len(cover[u]))  # stabil: Ties in Fundreihenfolge
    return order


def _build_doc_system_prompt(company, year: int, group: str) -> str:
    currency = getattr(company, "currency", None) or "EUR"
    label = _GROUP_LABELS[group]
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
        'derived_from setzen (z.B. "H1-Q1"); sonst value null. '
        f"Absolute Betraege in {currency}-Basiseinheiten "
        "(z.B. '5,8 Mrd' -> 5800000000), EPS je Aktie. Steht ein "
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
        "derived_from = Rechenweg bei abgeleiteten Quartalen, sonst "
        "null; nicht im Dokument = value null.",
    ]
    return "\n".join(lines)


def _call_claude_document(company, year: int, group: str,
                          needed: dict[str, tuple[str, ...]],
                          doc_bytes: bytes, kind: str, doc_url: str,
                          cost_tracker=None) -> dict | None:
    """EIN Claude-Call mit dem Berichtsdokument: PDF als document-Block
    (base64), HTML als extrahierter Text im Prompt (Muster
    gaap_bridge._clean_html). In Tests gemockt."""
    import base64

    import app.llm.claude as claude_mod
    from app.llm.rate_limiter import claude_limiter
    from scripts.two_stage_research import _extract_json

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
            system=_build_doc_system_prompt(company, year, group),
            messages=[{"role": "user", "content": content}],
        )

    response = claude_limiter.call(_do_call)
    if cost_tracker is not None:
        cost_tracker.add_response(response, EXTRACT_MODEL)
    parts = [getattr(block, "text", None) for block in response.content]
    raw = "\n".join(p for p in parts if p).strip()
    try:
        data = _extract_json(raw)
    except ValueError as e:
        logger.warning(
            "statement research doc: kein JSON in Claude-Antwort "
            "(%s FY%s %s): %s", company.ticker, year, group, e,
        )
        return None
    return data if isinstance(data, dict) else None


def _document_stage(db, company, year: int, group: str, raw_data: dict,
                    periods_reported: tuple[str, ...], now,
                    cost_tracker=None, ref_map: dict | None = None) -> int:
    """Stufe 2: verbliebene beduerftige Zellen aus den Berichts-
    Dokumenten der Stufe-1-Antwort fuellen. Max. MAX_DOC_CALLS
    Dokument-Calls; Downloads mit Guards, PDFs > MAX_PDF_PAGES werden
    uebersprungen. Rueckgabe: geschriebene Zeilen."""
    needed = _needy_cells(db, company, year, group, periods_reported)
    if not needed:
        return 0
    needy_periods = [
        pt for pt in _PERIODS
        if any(pt in pts for pts in needed.values())
    ]
    period_urls = _collect_period_urls(raw_data, group)
    candidates = _candidate_docs(period_urls, needy_periods)
    if not candidates:
        logger.info(
            "statement research doc %s/FY%s %s: Restbedarf, aber keine "
            "Dokument-URLs aus Stufe 1", company.ticker, year, group,
        )
        return 0

    written = 0
    calls = 0
    for url in candidates:
        if calls >= MAX_DOC_CALLS:
            logger.info(
                "statement research doc %s/FY%s %s: Kosten-Deckel "
                "(%d Dokument-Calls) erreicht",
                company.ticker, year, group, MAX_DOC_CALLS,
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
                logger.info(
                    "statement research doc %s/FY%s %s: %s hat %d Seiten "
                    "(> %d, vermutlich Geschaeftsbericht) — skip",
                    company.ticker, year, group, url, pages, MAX_PDF_PAGES,
                )
                continue
        calls += 1
        try:
            data = _call_claude_document(
                company, year, group, needed, doc_bytes, kind, url,
                cost_tracker=cost_tracker,
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
                             groups=None) -> int:
    """Berichtete Fundamentals eines Nicht-US-Filers fuer ein Jahr holen:
    EIN Claude-Websuche-Call pro Statement-Gruppe (max. 3 Calls).

    Fuellt nur, was nach den vorgeschalteten Ankern (PDF-Locks, ESEF-
    Provider) ersetzbar oder leer ist — Schreibrechte siehe
    _row_replaceable (Markt-Provider-Altbestand ist ersetzbar). Der
    Yahoo-Feed schreibt keine Werte mehr, er dient nur noch als
    Cross-Check-Referenz (_apply_yahoo_gate). `groups` (optional)
    beschraenkt auf eine Teilmenge der Gruppen (z.B. fuer den
    Vorjahres-Backfill einzelner Keys).

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
        try:
            data = _call_claude(company, year, group, cost_tracker=cost_tracker)
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
        )
    db.flush()
    return total
