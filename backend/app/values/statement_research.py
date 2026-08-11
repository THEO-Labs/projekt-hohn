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
  3. reported <= adjusted bei Paaren (klarer Verstoss: beide verwerfen),
  4. qsum-Enforcement: FY + alle 4 Quartale geliefert und Summe > 1%
     daneben -> Quartale verwerfen, FY behalten, loggen.

Schreib-Invarianten wie ueberall: normalize_sign, currency_conflict,
SAVEPOINT-Slot-Muster (uq_company_values_slot). Schreibrechte: Manual-/
PDF-/Provider-Zeilen mit Wert bleiben; not_found/two_stage_*/web_*/
calculated/statement_research sind ersetzbar. source_name ist
quote-first ("<quote> | <url>", beginnt nie mit https — bleibt damit
fuer den naechsten Lauf ersetzbar). Kein Beleg -> null -> not_found-
Platzhalter (rote Zelle) via stamp_attempt_and_fill_not_found.

Beruehrt werden NUR berichtete Perioden (Periodenende plus
REPORTING_GRACE_DAYS abgelaufen). Unberichtete Perioden des laufenden
Jahres bekommen weder Write noch not_found-Stempel — sonst verschattet
der Actual-Platzhalter die Forecast-Zeile im selben Slot-Paar
(Detail-Seite wirkt leer).
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
_REPORTED_ADJ_TOL = Decimal("0.01")

# Ersetzbare Herkuenfte (Muster _derivation_replaceable in consistency,
# erweitert um die eigene Signatur, damit der naechste Lauf seine
# Vorgaenger-Zeilen aktualisieren darf).
_REPLACEABLE_METHODS = ("not_found", "calculated", "statement_research")

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
        "Perioden: value null. Antworte NUR mit einem JSON-Objekt nach "
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
        "url = Quelle-URL; derived_from = Rechenweg bei abgeleiteten "
        "Quartalen, sonst null; nicht berichtet = value null.",
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
            if b_val > a_val + abs(a_val) * _REPORTED_ADJ_TOL:
                logger.warning(
                    "statement research %s/FY%s: %s/%s=%s > %s=%s + 1%% "
                    "(reported muss <= adjusted sein) — beide skip",
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


def _row_replaceable(row: CompanyValue) -> bool:
    """Schreibrechte: Manual-/PDF-/Provider-Zeilen mit Wert sind
    authoritative; leere Zeilen und LLM-/Ableitungs-Herkuenfte
    (not_found/two_stage_*/web_*/calculated/statement_research) sind
    ersetzbar (Muster consistency._derivation_replaceable)."""
    if row.manually_overridden or (row.from_ir_pdf and row.numeric_value is not None):
        return False
    if row.numeric_value is None:
        return True
    pm = row.primary_method or ""
    return (
        pm in _REPLACEABLE_METHODS
        or pm.startswith("two_stage")
        or pm.startswith("web")
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
                    info: dict, now, base_row: CompanyValue | None) -> bool:
    """Adjusted-Sidecar in numeric_value_adjusted der Basis-Zeile schreiben.
    Nur bei echten adjusted-Ausweisen; adjusted_is_protected respektieren.
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
    # quote-first ('quote | url'): beginnt nie mit https — bleibt damit
    # fuer den naechsten Lauf ersetzbar (adjusted_is_protected schuetzt
    # nur 'Manual' und reine URLs).
    text = info.get("quote") or f"Adjusted-Ausweis FY{year}"
    if info.get("derived_from"):
        text = f"Abgeleitet ({info['derived_from']}): {text}"
    src_parts = [text[:400]]
    if info.get("url"):
        src_parts.append(info["url"])
    row.numeric_value_adjusted = info["value"]
    row.adjustments_note = "Adjusted (berichtet, Statement-Recherche)"
    row.adjustments_source = " | ".join(src_parts)[:2048]
    db.flush()
    return True


def _persist_group(db, company, year: int, group: str,
                   parsed: dict[str, dict[str, dict]], now,
                   periods_reported: tuple[str, ...]) -> int:
    """Einen geparsten Gruppen-Block persistieren: berichtete Perioden +
    adjusted-Sidecars; nicht gelieferte/verworfene BERICHTETE Perioden
    bekommen not_found-Platzhalter bzw. einen Refresh-Stempel.
    Unberichtete Perioden (Karenz laeuft noch) werden nicht angefasst.
    Rueckgabe: geschriebene Zeilen."""
    from scripts.two_stage_research import stamp_attempt_and_fill_not_found

    written = 0
    written_rows: dict[tuple[str, str], CompanyValue] = {}
    base_keys = [k for k, _ in STATEMENT_GROUPS[group] if k not in _ADJUSTED_SIDECARS]
    for key in base_keys:
        periods = parsed.get(key, {})
        for pt in periods_reported:
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
        unwritten = [pt for pt in periods_reported if (key, pt) not in written_rows]
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
            )
    return written


def fetch_statement_research(db, company, year: int, cost_tracker=None,
                             groups=None) -> int:
    """Berichtete Fundamentals eines Nicht-US-Filers fuer ein Jahr holen:
    EIN Claude-Websuche-Call pro Statement-Gruppe (max. 3 Calls).

    Fuellt nur, was nach den vorgeschalteten Ankern (PDF-Locks, ESEF/
    Yahoo-Provider) ersetzbar oder leer ist — Schreibrechte siehe
    _row_replaceable. `groups` (optional) beschraenkt auf eine Teilmenge
    der Gruppen (z.B. fuer den Vorjahres-Backfill einzelner Keys).

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
    for group in group_names:
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
        _enforce_qsum(parsed, company.ticker, year)
        wrote = _persist_group(db, company, year, group, parsed, now, periods_reported)
        total += wrote
        logger.info(
            "statement research %s/FY%s %s: %d Zeilen geschrieben",
            company.ticker, year, group, wrote,
        )
    db.flush()
    return total
