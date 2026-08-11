"""FY- und Open-Quarter-Guidance-Estimates: EIN Claude-Call pro Firma.

Seit dem DE-Umbau fuer ALLE Filer (US und Nicht-US): fuer IFRS-Firmen
liest sich die gaap/non_gaap-Basis als reported IFRS / adjusted (Core,
bereinigt, Non-IFRS).

Fuer US-Filer kommen berichtete Perioden aus XBRL/8-K (Provider-Anker,
GAAP-Bruecke) — die Two-Stage-Recherche schaetzte fuers laufende
Geschaeftsjahr nur noch die FY-Werte, mit ~19 Extractor+Verifier-Paaren
pro Refresh. Dieses Modul ersetzt das durch einen einzigen Websuche-Call:
aktuelle offizielle Guidance + Analysten-Konsens fuer alle FY-Schaetzwerte
auf einmal. Uebergibt der Aufrufer zusaetzlich das offene Quartal
(open_quarter, Berichtet-Kriterium wie in consistency — hier bewusst
nicht dupliziert), fragt derselbe Call das Modell DIREKT nach den
Schaetzwerten dieses Quartals, statt sie als FY-Residuum zu basteln
(Margen-Bastelei des Modells lieferte unplausible Q-Restwerte).

Schreib-Invarianten wie in den uebrigen Pfaden: normalize_sign,
currency_conflict, Manual-/PDF-Zeilen bleiben unangetastet.

Der Prompt ist eine natuerliche Chat-Frage OHNE Verbots- und Regellisten
(User-Entscheid) — die deterministischen Code-Gates sind damit die
EINZIGE Verteidigung und entsprechend wichtig:
- source-Gate in der Payload-Normalisierung (nur guidance|consensus),
- drei Plausibilitaets-Gates vor jedem Write, fuer FY- UND Q-Werte:
  1. Einheiten-Check: Absolutwerte >= 1 Mio (ausser Per-Share),
  2. Vorjahresband 40-160%: FY gegen FY-Vorjahres-Ist, Q gegen dasselbe
     Quartal des Vorjahres-Ist (fehlt der Vorjahreswert: uebersprungen;
     Sign-Flip/Turnaround erlaubt),
  3. GAAP <= Non-GAAP + 1% (Paare, beide verwerfen bei Verstoss).
Pro Wert liefert das Modell ein 1-2-Satz-reasoning; das ist der sichtbare
Quellentext der Zelle (source_name/adjustments_source, reasoning-first,
Fallback quote).

GAAP vs Non-GAAP (Regel-Lockerung, User-Entscheid): Das Modell DARF
GAAP-Schaetzungen selbst herleiten (z.B. Non-GAAP-Konsens minus typischem
Abstand). Das basis-Feld (gaap|non_gaap|unclear) bleibt informativ und
fuers Gate; _rebook_by_basis korrigiert nur noch OFFENSICHTLICHE
Fehlbelegung (basis='non_gaap' im GAAP-Slot), 'unclear' ist kein
Umbuchungsgrund mehr. Fehlt ein direkter NI-adj-Konsens, wird net_income
adjusted weiterhin deterministisch als eps_adj x shares_outstanding
(SNAPSHOT) abgeleitet (Fallback wenn das Modell null liefert).

FCF wird NICHT mehr abgefragt: fcf ist berechnet (OCF - |Capex|),
consistency.derive_missing_fcf liefert das deterministisch. OCF wird
direkt abgefragt (FY und offenes Quartal). fcf bleibt trotzdem in
GUIDANCE_ESTIMATE_KEYS, damit der Refresh-Key-Loop (routes) den Key fuer
US-Filer weiter am Two-Stage-Pfad vorbeifuehrt — die Ableitung deckt ihn.
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

# Recherche-Muster: web_search-Tool, temperature 0.
EXTRACT_MODEL = "claude-sonnet-4-6"
WEB_SEARCH_MAX_USES = 5
# Mit open_quarter verdoppelt sich das Antwort-JSON (fy- + Q-Block je
# Metric, reasoning-Strings): 4096 fuehrte zu abgeschnittenem JSON.
MAX_TOKENS = 12288

_Q_TYPES = ("Q1", "Q2", "Q3", "Q4")

# Schaetz-Keys, die dieser Pfad abdeckt — der Refresh-Key-Loop
# ueberspringt sie fuer US-Filer im laufenden FY (routes). fcf wird NICHT
# mehr abgefragt (siehe _METRIC_SPECS), bleibt aber hier: die Ableitung
# fcf = OCF - |Capex| (consistency.derive_missing_fcf) deckt den Key.
GUIDANCE_ESTIMATE_KEYS = frozenset({
    "revenue", "net_income", "eps_diluted", "operating_cash_flow",
    "capex", "fcf", "sbc", "dividends", "buyback_volume", "ebitda",
})

# Non-GAAP-Sidecars: landen in numeric_value_adjusted der jeweiligen
# GAAP-Zeile (FY oder Quartal), nie als eigene Zeile.
_NON_GAAP_SIDECARS = {
    "net_income_non_gaap": "net_income",
    "eps_diluted_non_gaap": "eps_diluted",
}

# GAAP-Slot -> zugehoeriger Non-GAAP-Sidecar-Key (fuer basis-Umbuchung
# und das GAAP<=Non-GAAP-Gate).
_GAAP_ADJ_PAIRS = (
    ("eps_diluted", "eps_diluted_non_gaap"),
    ("net_income", "net_income_non_gaap"),
)

# Per-Share-Keys sind vom Einheiten-Check (> 1 Mio) ausgenommen.
_PER_SHARE_KEYS = frozenset({"eps_diluted", "eps_diluted_non_gaap"})

# Vorjahresband 40-160%: |v/prev - 1| > 0.60 verwirft.
_PREV_DEVIATION_TOL = Decimal("0.60")
_UNIT_MIN = Decimal("1000000")
# US-Standard: adjusted >= GAAP (SBC/Amortisations-Addbacks). 1% Toleranz.
_GAAP_ADJ_TOL = Decimal("0.01")

# Reihenfolge + Kurzbeschreibung fuer den Prompt (je 2-4 Worte).
# fcf fehlt bewusst: berechnet aus OCF - |Capex| (derive_missing_fcf).
_METRIC_SPECS = (
    ("revenue", "total revenue"),
    ("net_income", "GAAP net income"),
    ("eps_diluted", "GAAP diluted EPS"),
    ("eps_diluted_non_gaap", "adjusted diluted EPS"),
    ("net_income_non_gaap", "adjusted net income"),
    ("operating_cash_flow", "operating cash flow"),
    ("capex", "capital expenditures"),
    ("sbc", "stock-based compensation"),
    ("dividends", "total dividends paid"),
    ("buyback_volume", "share repurchase volume"),
    ("ebitda", "EBITDA"),
)

# Ein Estimate-Objekt im JSON-Schema (FY-Block und open_quarter-Block
# nutzen dieselben Felder).
_EST_FIELDS = (
    '{"value": <number|null>, "source": "guidance"|"consensus"|null, '
    '"basis": "gaap"|"non_gaap"|"unclear", '
    '"reasoning": <string|null>, "url": <string|null>}'
)


def _fy_end_date(company, year: int) -> date:
    m = getattr(company, "fiscal_year_end_month", None) or 12
    d = getattr(company, "fiscal_year_end_day", None) or 31
    try:
        return date(year, m, d)
    except ValueError:
        # 29.02. in Nicht-Schaltjahr — konservativ auf den 28. runden.
        return date(year, m, 28)


def _build_system_prompt(company, year: int, open_quarter: str | None = None) -> str:
    fy_end = _fy_end_date(company, year).isoformat()
    currency = getattr(company, "currency", None) or "USD"
    quarter_sentence = ""
    if open_quarter:
        quarter_sentence = (
            f"Also give the estimates for the open quarter {open_quarter} "
            f"FY{year} (the quarter not yet reported). "
        )
    return (
        f"What are the current fiscal year {year} (ending {fy_end}) "
        f"estimates for {company.name} ({company.ticker})? Base them on "
        "the company's guidance and analyst consensus. Estimates must be "
        f"forward-looking full-year FY{year} figures, not "
        "trailing-twelve-month actuals. "
        + quarter_sentence
        + "For each value, "
        "give a one-to-two-sentence reasoning (which guidance/consensus "
        "figure it rests on). Report absolute amounts in "
        f"{currency} base units (e.g. '$5.8 billion' -> 5800000000) and "
        "EPS per share. Answer with ONLY one JSON object matching the "
        "schema in the user message — no prose outside the JSON, no "
        "markdown fences."
    )


def _build_user_prompt(company, year: int, open_quarter: str | None = None) -> str:
    lines = [
        f"Metrics to estimate for {company.name} ({company.ticker}) fiscal year {year}:",
        "",
    ]
    for key, desc in _METRIC_SPECS:
        lines.append(f"- {key}: {desc}")
    if open_quarter:
        fields = ",\n".join(
            f'  "{key}": {{"fy": <estimate>, "open_quarter": <estimate>}}'
            for key, _ in _METRIC_SPECS
        )
    else:
        fields = ",\n".join(
            f'  "{key}": {_EST_FIELDS}'
            for key, _ in _METRIC_SPECS
        )
    lines += [
        "",
        "value = latest guidance (midpoint of a range) or analyst "
        "consensus; reasoning = one to two sentences on which guidance/"
        "consensus figure the value rests; url = source URL.",
    ]
    if open_quarter:
        lines += [
            "",
            f'"fy" = full fiscal year {year}, "open_quarter" = '
            f"{open_quarter} FY{year} (the quarter not yet reported).",
        ]
    lines += [
        "",
        "Return JSON matching exactly this schema:",
        "",
        "{",
        fields,
        "}",
    ]
    if open_quarter:
        lines += [
            "",
            f"where <estimate> = {_EST_FIELDS}",
        ]
    return "\n".join(lines)


def _call_claude(company, year: int, cost_tracker=None,
                 open_quarter: str | None = None) -> dict | None:
    """EIN Claude-Call mit Websuche fuer alle FY- (und optional Q-)
    Schaetzwerte. In Tests gemockt (conftest blockt get_client)."""
    import app.llm.claude as claude_mod
    from app.llm.rate_limiter import claude_limiter
    from app.llm.json_utils import extract_json

    client = claude_mod.get_client()

    def _do_call():
        return client.messages.create(
            model=EXTRACT_MODEL,
            max_tokens=MAX_TOKENS,
            temperature=0,
            system=_build_system_prompt(company, year, open_quarter),
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": WEB_SEARCH_MAX_USES,
            }],
            messages=[{
                "role": "user",
                "content": _build_user_prompt(company, year, open_quarter),
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
            "guidance estimates: kein JSON in Claude-Antwort (%s FY%s): %s",
            company.ticker, year, e,
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


def _parse_entry(entry, key: str, ticker: str, period_label: str) -> dict | None:
    """Ein Estimate-Objekt normalisieren. Nur guidance/consensus sind
    gueltige Quellen — alles andere verwerfen."""
    if not isinstance(entry, dict):
        return None
    value = _to_decimal(entry.get("value"))
    if value is None:
        return None
    source = entry.get("source")
    if source not in ("guidance", "consensus"):
        logger.warning(
            "guidance estimates %s/%s: %s ohne guidance/consensus-Quelle "
            "(%r) — verworfen", ticker, period_label, key, source,
        )
        return None
    quote = entry.get("quote")
    reasoning = entry.get("reasoning")
    url = entry.get("url")
    basis = entry.get("basis")
    return {
        "value": value,
        "source": source,
        # Fehlend/unbekannt als "unclear" behandeln (nur informativ).
        "basis": basis if basis in ("gaap", "non_gaap") else "unclear",
        "reasoning": reasoning.strip() if isinstance(reasoning, str) and reasoning.strip() else None,
        "quote": quote.strip() if isinstance(quote, str) else None,
        "url": url if isinstance(url, str) and url.startswith(("http://", "https://")) else None,
    }


def _parse_payload(data: dict, ticker: str, year: int,
                   open_quarter: str | None = None) -> tuple[dict[str, dict], dict[str, dict]]:
    """Antwort in ({key: info} fuer FY, {key: info} fuers offene Quartal)
    normalisieren. Ohne open_quarter (oder wenn das Modell flach
    antwortet) ist der Q-Block leer und der Eintrag zaehlt als FY."""
    fy: dict[str, dict] = {}
    q: dict[str, dict] = {}
    for key, _ in _METRIC_SPECS:
        entry = data.get(key)
        if not isinstance(entry, dict):
            continue
        if open_quarter is not None and ("fy" in entry or "open_quarter" in entry):
            info = _parse_entry(entry.get("fy"), key, ticker, f"FY{year}")
            if info is not None:
                fy[key] = info
            q_info = _parse_entry(
                entry.get("open_quarter"), key, ticker,
                f"{open_quarter} FY{year}",
            )
            if q_info is not None:
                q[key] = q_info
        else:
            info = _parse_entry(entry, key, ticker, f"FY{year}")
            if info is not None:
                fy[key] = info
    return fy, q


def _rebook_by_basis(parsed: dict[str, dict], ticker: str, year: int) -> None:
    """Nur OFFENSICHTLICHE Fehlbelegung korrigieren (Regel-Lockerung).

    basis="non_gaap" im GAAP-Slot -> in den adjusted-Sidecar umbuchen;
    ist der Sidecar schon belegt, wird der GAAP-Slot-Wert verworfen.
    basis="unclear" ist KEIN Umbuchungsgrund mehr — das Modell darf
    GAAP selbst herleiten; unplausible Paare (GAAP > Non-GAAP + 1%)
    verwirft das nachgelagerte Gate. Fuer Keys ohne GAAP/Non-GAAP-Paar
    (revenue, capex, ...) ist basis informativ.
    """
    for gaap_key, adj_key in _GAAP_ADJ_PAIRS:
        info = parsed.get(gaap_key)
        if info is None or info["basis"] != "non_gaap":
            continue
        del parsed[gaap_key]
        if adj_key in parsed:
            logger.warning(
                "guidance estimates %s/FY%s: %s ohne explizite GAAP-Basis "
                "(basis=%s), %s bereits belegt — verworfen",
                ticker, year, gaap_key, info["basis"], adj_key,
            )
            continue
        logger.info(
            "guidance estimates %s/FY%s: %s basis=%s — in %s umgebucht",
            ticker, year, gaap_key, info["basis"], adj_key,
        )
        parsed[adj_key] = info


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


def _snapshot_shares(db, company_id) -> Decimal | None:
    row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == "shares_outstanding",
            CompanyValue.period_type == "SNAPSHOT",
        )
        .first()
    )
    return row.numeric_value if row else None


def _apply_gates(db, company, year: int, parsed: dict[str, dict],
                 period_type: str = "FY") -> None:
    """Drei deterministische Plausibilitaets-Gates — mutiert `parsed` in
    place, gilt fuer FY- und Q-Bloecke gleichermassen.

    1. Einheiten-Check: Absolutwerte unter 1 Mio (aber != 0) sind fast
       immer eine fehlende Skalierung (ausser Per-Share-Keys).
    2. Vorjahresband 40-160%: Schaetzung vs Vorjahres-Actual DERSELBEN
       Periode (FY vs FY, Qx vs Qx). Fehlt der Vorjahreswert, wird das
       Gate uebersprungen; Vorzeichenwechsel (Turnaround) ist erlaubt.
    3. GAAP <= Non-GAAP + 1% wenn beide fuer denselben Key geliefert
       (US-Standard: adjusted >= GAAP durch SBC/Amortisations-Addbacks) —
       sonst beide verwerfen.
    """
    ticker = company.ticker
    label = f"{period_type}/FY{year}" if period_type != "FY" else f"FY{year}"
    for key in list(parsed):
        if key in _PER_SHARE_KEYS:
            continue
        v = parsed[key]["value"]
        if v != 0 and abs(v) < _UNIT_MIN:
            logger.warning(
                "guidance estimates %s/%s: %s=%s unter 1 Mio "
                "(Einheiten-Verdacht) — skip", ticker, label, key, v,
            )
            del parsed[key]

    for key in list(parsed):
        if key in _NON_GAAP_SIDECARS:
            continue
        prev = _prev_actual(db, company.id, key, year, period_type)
        if prev is None or prev == 0:
            continue
        v = parsed[key]["value"]
        sign_flip = (v >= 0) != (prev >= 0)
        if not sign_flip and abs(v / prev - 1) > _PREV_DEVIATION_TOL:
            logger.warning(
                "guidance estimates %s/%s: %s=%s ausserhalb 40-160%% des "
                "Vorjahres-Actual %s — skip", ticker, label, key, v, prev,
            )
            del parsed[key]

    for gaap_key, adj_key in _GAAP_ADJ_PAIRS:
        g = parsed.get(gaap_key)
        a = parsed.get(adj_key)
        if g is None or a is None:
            continue
        if g["value"] > a["value"] + abs(a["value"]) * _GAAP_ADJ_TOL:
            logger.warning(
                "guidance estimates %s/%s: %s=%s > %s=%s + 1%% "
                "(adjusted muss >= GAAP sein) — beide skip",
                ticker, label, gaap_key, g["value"], adj_key, a["value"],
            )
            del parsed[gaap_key]
            del parsed[adj_key]


def _derive_adjusted_net_income(db, company, year: int, parsed: dict[str, dict],
                                period_type: str = "FY") -> None:
    """Fehlt ein direkter NI-adj-Konsens, aber es gibt Non-GAAP-EPS:
    net_income adjusted deterministisch als eps_adj x diluted shares
    (SNAPSHOT-Stammdaten) ableiten. Kein Modell-Schaetzwert — reine
    Multiplikation, als solche in der Quelle ausgewiesen. Gilt pro Block
    (FY und offenes Quartal)."""
    if "net_income_non_gaap" in parsed:
        return
    eps = parsed.get("eps_diluted_non_gaap")
    if eps is None:
        return
    shares = _snapshot_shares(db, company.id)
    if not shares:
        return
    parsed["net_income_non_gaap"] = {
        "value": eps["value"] * shares,
        "source": eps["source"],
        "basis": "non_gaap",
        "reasoning": "Abgeleitet: EPS-Konsens x Aktienzahl",
        "quote": None,
        "url": eps.get("url"),
        # Schwaechste Quelle: darf nur leere adjusted-Slots fuellen,
        # nie eine vorhandene Quartalssumme oder direkten Konsens ersetzen.
        "derived": True,
    }
    logger.info(
        "guidance estimates %s/%s FY%s: net_income adjusted abgeleitet "
        "(eps_adj %s x shares %s)",
        company.ticker, period_type, year, eps["value"], shares,
    )


def _compose_source_name(info: dict, period_label: str) -> str:
    """reasoning-first: das 1-2-Satz-Reasoning des Modells ist der
    sichtbare Quellentext der Zelle (menschlich lesbar); Fallback quote,
    dann generisches Label. URL wird angehaengt."""
    text = (
        info.get("reasoning")
        or info.get("quote")
        or f"{period_label}-Guidance ({info['source']})"
    )
    parts = [text[:1000]]
    if info.get("url"):
        parts.append(info["url"])
    return " | ".join(parts)[:4096]


def _upsert_forecast(db, company, key: str, year: int, info: dict, now,
                     period_type: str) -> CompanyValue | None:
    """Forecast-Slot (FY oder Quartal) schreiben. Manual-/PDF-Zeilen sind
    authoritative, Currency-Konflikte blocken. Rueckgabe: geschriebene
    Zeile oder None."""
    rows = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == period_type,
            CompanyValue.period_year == year,
        )
        .order_by(CompanyValue.is_forecast.asc())
        .all()
    )
    if any(
        r.manually_overridden or (r.from_ir_pdf and r.numeric_value is not None)
        for r in rows
    ):
        return None

    currency = company.currency if key in CURRENCY_KEYS else None
    target = next((r for r in rows if r.is_forecast), None)
    if target is not None and currency_conflict(key, target.currency, currency):
        logger.warning(
            "guidance estimates currency mismatch BLOCKED %s/%s/%s FY%s: existing=%s new=%s",
            company.ticker, key, period_type, year, target.currency, currency,
        )
        target.last_refresh_attempt = now
        db.flush()
        return None

    value = normalize_sign(
        key, info["value"],
        context=f"guidance-estimate {company.ticker}/{period_type} FY{year}",
    )

    if target is None:
        target = CompanyValue(
            id=uuid4(), company_id=company.id, value_key=key,
            period_type=period_type, period_year=year, is_forecast=True,
        )
        # SAVEPOINT: Unique-Index-Kollision (Race mit parallelem Writer)
        # -> Slot-Zeile neu laden und Guards erneut anwenden.
        try:
            with db.begin_nested():
                db.add(target)
                db.flush()
        except IntegrityError:
            target = _forecast_row(db, company.id, key, year, period_type)
            if target is None or target.manually_overridden or target.from_ir_pdf:
                return None
            if currency_conflict(key, target.currency, currency):
                target.last_refresh_attempt = now
                db.flush()
                return None

    target.numeric_value = value
    target.text_value = None
    target.source_name = info.get("source_name") or _compose_source_name(info, period_type)
    target.source_link = info.get("url")
    target.primary_method = info.get("method") or "web_guidance"
    target.is_forecast = True
    target.manually_overridden = False
    target.from_ir_pdf = False
    if currency:
        target.currency = currency
    target.fetched_at = now
    target.last_refresh_attempt = now
    db.flush()
    return target


def _forecast_row(db, company_id, key: str, year: int, period_type: str) -> CompanyValue | None:
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == period_type,
            CompanyValue.period_year == year,
            CompanyValue.is_forecast.is_(True),
        )
        .first()
    )


def _ensure_sidecar_slot(db, company, key: str, year: int, now,
                         period_type: str) -> CompanyValue | None:
    """Traeger-Zeile fuer einen adjusted-Sidecar sicherstellen.

    Existiert keine GAAP-Quelle, bleibt der GAAP-Slot (numeric_value)
    bewusst leer — die Zeile wird trotzdem angelegt, damit die
    adjusted-Spur die Schaetzung tragen kann. Manual-/PDF-Zeilen sind
    authoritative -> None."""
    rows = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == period_type,
            CompanyValue.period_year == year,
        )
        .order_by(CompanyValue.is_forecast.asc())
        .all()
    )
    if any(
        r.manually_overridden or (r.from_ir_pdf and r.numeric_value is not None)
        for r in rows
    ):
        return None
    target = next((r for r in rows if r.is_forecast), None)
    if target is not None:
        return target

    target = CompanyValue(
        id=uuid4(), company_id=company.id, value_key=key,
        period_type=period_type, period_year=year, is_forecast=True,
    )
    if key in CURRENCY_KEYS:
        target.currency = company.currency
    target.fetched_at = now
    target.last_refresh_attempt = now
    # SAVEPOINT: Race mit parallelem Writer wie in _upsert_forecast.
    try:
        with db.begin_nested():
            db.add(target)
            db.flush()
    except IntegrityError:
        target = _forecast_row(db, company.id, key, year, period_type)
        if target is None or target.manually_overridden or target.from_ir_pdf:
            return None
    return target


def _persist_block(db, company, year: int, parsed: dict[str, dict], now,
                   period_type: str) -> int:
    """Einen geparsten Block (FY oder offenes Quartal) persistieren:
    Forecast-Slots + Non-GAAP-Sidecars. Rueckgabe: geschriebene Zeilen."""
    written = 0
    slot_rows: dict[str, CompanyValue] = {}
    for key in sorted(GUIDANCE_ESTIMATE_KEYS):
        info = parsed.get(key)
        if info is None:
            continue
        row = _upsert_forecast(db, company, key, year, info, now, period_type)
        if row is not None:
            slot_rows[key] = row
            written += 1

    # Non-GAAP-Sidecars in numeric_value_adjusted der GAAP-Zeile.
    # 'Manual'-Schutzlogik bewusst NICHT setzen — Quelle ist die
    # Konsens-/Guidance-Angabe (quote | url, ueberschreibbar).
    for ngaap_key, base_key in _NON_GAAP_SIDECARS.items():
        info = parsed.get(ngaap_key)
        if info is None:
            continue
        # Ohne GAAP-Quelle existiert kein GAAP-Write — Traeger-Zeile mit
        # leerem numeric_value anlegen, die adjusted-Spur traegt den Wert.
        row = slot_rows.get(base_key) or _ensure_sidecar_slot(
            db, company, base_key, year, now, period_type,
        )
        if row is None:
            continue
        if row.manually_overridden or row.from_ir_pdf:
            continue
        if adjusted_is_protected(row.adjustments_source):
            continue
        if info.get("derived") and row.numeric_value_adjusted is not None:
            continue
        if (
            base_key not in parsed
            and row.primary_method == "web_guidance"
            and row.numeric_value is not None
        ):
            # Dieser Lauf hat keinen GAAP-Konsens geliefert: den eigenen
            # alten GAAP-Wert raeumen (mutmasslich Non-GAAP-kontaminiert,
            # Stand vor der basis-Trennung) — adjusted traegt die Schaetzung.
            row.numeric_value = None
        # Reasoning-first ('reasoning | url', Fallback quote): beginnt nie
        # mit https — bleibt damit fuer den naechsten Guidance-Lauf
        # ueberschreibbar (adjusted_is_protected schuetzt nur 'Manual'
        # und reine URLs).
        text = info.get("reasoning") or info.get("quote") or info["source"]
        src_parts = [text[:400]]
        if info.get("url"):
            src_parts.append(info["url"])
        row.numeric_value_adjusted = info["value"]
        row.adjustments_note = f"Non-GAAP {period_type}-Estimate ({info['source']})"
        row.adjustments_source = " | ".join(src_parts)
    return written


def fetch_guidance_estimates(db, company, year: int, cost_tracker=None,
                             open_quarter: str | None = None) -> int:
    """Alle Schaetzwerte des laufenden Geschaeftsjahres mit EINEM
    Claude-Websuche-Call holen und als Forecasts persistieren.

    open_quarter ("Q1".."Q4", optional): Der AUFRUFER soll das offene
    (noch nicht berichtete) Quartal uebergeben — ermittelt ueber dasselbe
    Berichtet-Kriterium wie in consistency (dort nicht importierbar ohne
    Zirkularitaet, deshalb Parameter). Ist es gesetzt, fragt der Call das
    Modell zusaetzlich DIREKT nach den Schaetzwerten dieses Quartals und
    schreibt sie in die Quartals-Forecast-Slots (period_type=open_quarter,
    is_forecast=True, primary_method='web_guidance', Sidecars analog FY).
    Default None = wie bisher nur FY.

    Die FY-Werte werden unveraendert geschrieben; die FY-Zeile wird
    spaeter von der Quartalssummen-Aggregation ueberstimmt, sobald alle
    vier Quartale vorliegen.

    Nur fuers laufende (nicht abgeschlossene) FY — sonst 0. Gilt fuer
    US- UND Nicht-US-Filer (DE-Umbau): die gaap/non_gaap-Basis liest sich
    fuer IFRS-Firmen als reported IFRS / adjusted (Core, bereinigt,
    Non-IFRS); Currency und FY-Ende kommen aus den Company-Stammdaten.
    Rueckgabe: Anzahl geschriebener Forecast-Zeilen (FY + Quartal).
    """
    from app.config import settings
    from app.values.provider_anchor import _fy_is_closed

    if _fy_is_closed(company, year):
        return 0
    if not settings.anthropic_api_key:
        return 0
    if open_quarter is not None and open_quarter not in _Q_TYPES:
        logger.warning(
            "guidance estimates %s/FY%s: ungueltiges open_quarter=%r — "
            "FY-only", company.ticker, year, open_quarter,
        )
        open_quarter = None

    try:
        data = _call_claude(
            company, year, cost_tracker=cost_tracker, open_quarter=open_quarter,
        )
    except Exception as e:
        logger.warning(
            "guidance estimates: Claude-Call failed fuer %s FY%s: %s",
            company.ticker, year, e,
        )
        return 0
    if not data:
        return 0

    fy_parsed, q_parsed = _parse_payload(data, company.ticker, year, open_quarter)
    # Quartals-Antworten, bei denen das Modell nachweislich streut, werden
    # verworfen — dort uebernimmt die Arithmetik im Konsistenz-Pass:
    # operating_cash_flow (Runrate/FY-Residuum; Modell lieferte z.B. 9.4 Mrd
    # bei 5-6.5 plausibel) und der GAAP-Slot von eps_diluted (Spread-
    # Ableitung aus dem Non-GAAP-Konsens; der Non-GAAP-Sidecar bleibt).
    q_parsed.pop("operating_cash_flow", None)
    if "eps_diluted" in q_parsed:
        q_parsed.pop("eps_diluted")
    blocks: list[tuple[str, dict[str, dict]]] = [("FY", fy_parsed)]
    if open_quarter is not None:
        blocks.append((open_quarter, q_parsed))
    for period_type, parsed in blocks:
        _rebook_by_basis(parsed, company.ticker, year)
        _apply_gates(db, company, year, parsed, period_type)
        _derive_adjusted_net_income(db, company, year, parsed, period_type)
    if not fy_parsed and not q_parsed:
        return 0

    now = datetime.now(timezone.utc)
    written = 0
    for period_type, parsed in blocks:
        if parsed:
            written += _persist_block(db, company, year, parsed, now, period_type)

    db.flush()
    logger.info(
        "guidance estimates %s/FY%s: %d Forecasts geschrieben "
        "(FY: %d Werte, %s: %d Werte)",
        company.ticker, year, written, len(fy_parsed),
        open_quarter or "kein Q", len(q_parsed),
    )
    return written
