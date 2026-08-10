"""FY-Guidance-Estimates fuer US-Filer: EIN Claude-Call pro Firma.

Fuer US-Filer kommen berichtete Perioden aus XBRL/8-K (Provider-Anker,
GAAP-Bruecke) — die Two-Stage-Recherche schaetzte fuers laufende
Geschaeftsjahr nur noch die FY-Werte, mit ~19 Extractor+Verifier-Paaren
pro Refresh. Dieses Modul ersetzt das durch einen einzigen Websuche-Call:
aktuelle offizielle Guidance + Analysten-Konsens fuer alle FY-Schaetzwerte
auf einmal. Das offene Rest-Quartal leitet danach
consistency.derive_open_quarter_from_fy_estimate deterministisch ab
(hier bewusst nicht dupliziert).

Schreib-Invarianten wie in den uebrigen Pfaden: normalize_sign,
currency_conflict, Manual-/PDF-Zeilen bleiben unangetastet. Vor jedem
Write laufen deterministische Plausibilitaets-Gates (60%-Vorjahres-Gate,
eps x shares vs net_income, Einheiten-Check, GAAP <= Non-GAAP + 1%).

GAAP vs Non-GAAP: US-Analystenkonsens fuer EPS/Net-Income ist fast immer
NON-GAAP. Jeder Wert traegt deshalb ein basis-Feld (gaap|non_gaap|unclear);
GAAP-Slots (eps_diluted, net_income) werden nur bei explizit ausgewiesener
GAAP-Basis befuellt, sonst in die adjusted-Sidecars umgebucht. Fehlt ein
direkter NI-Konsens, wird net_income adjusted deterministisch als
eps_adj x shares_outstanding (SNAPSHOT) abgeleitet — GAAP-Slots bleiben
dann bewusst leer (keine Rueckrechnung ueber historische Spreads, das
waere verkappte Extrapolation).
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

# Modell/Muster wie der Two-Stage-Extractor (scripts/two_stage_research.py):
# web_search-Tool, temperature 0.
EXTRACT_MODEL = "claude-sonnet-4-6"
WEB_SEARCH_MAX_USES = 5
MAX_TOKENS = 4096

# FY-Schaetz-Keys, die dieser Call abdeckt — der Refresh-Key-Loop
# ueberspringt sie fuer US-Filer im laufenden FY (routes).
GUIDANCE_ESTIMATE_KEYS = frozenset({
    "revenue", "net_income", "eps_diluted", "operating_cash_flow",
    "capex", "sbc", "dividends", "buyback_volume", "ebitda",
})

# Non-GAAP-Sidecars: landen in numeric_value_adjusted der jeweiligen
# GAAP-FY-Zeile, nie als eigene Zeile.
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

_PREV_DEVIATION_TOL = Decimal("0.60")
_EPS_NI_TOL = Decimal("0.20")
_UNIT_MIN = Decimal("1000000")
# US-Standard: adjusted >= GAAP (SBC/Amortisations-Addbacks). 1% Toleranz.
_GAAP_ADJ_TOL = Decimal("0.01")

# Reihenfolge + Kurzbeschreibung fuer den Prompt.
_METRIC_SPECS = (
    ("revenue", "total revenue"),
    ("net_income", "GAAP net income (only if the source explicitly labels it GAAP/reported)"),
    ("eps_diluted", "GAAP diluted EPS (per share; only if the source explicitly labels it GAAP/reported)"),
    ("eps_diluted_non_gaap", "Non-GAAP / adjusted diluted EPS (per share)"),
    ("net_income_non_gaap", "Non-GAAP / adjusted net income"),
    ("operating_cash_flow", "operating cash flow"),
    ("capex", "capital expenditures"),
    ("sbc", "stock-based compensation expense"),
    ("dividends", "total dividends paid"),
    ("buyback_volume", "share repurchase volume"),
    ("ebitda", "EBITDA"),
)


def _fy_end_date(company, year: int) -> date:
    m = getattr(company, "fiscal_year_end_month", None) or 12
    d = getattr(company, "fiscal_year_end_day", None) or 31
    try:
        return date(year, m, d)
    except ValueError:
        # 29.02. in Nicht-Schaltjahr — konservativ auf den 28. runden.
        return date(year, m, 28)


def _build_system_prompt(company, year: int) -> str:
    fy_end = _fy_end_date(company, year).isoformat()
    return (
        f"You are an equity analyst. For {company.name} ({company.ticker}) "
        f"fiscal year {year} (ending {fy_end}), find the company's CURRENT "
        "official guidance and the analyst consensus estimates for the full "
        "fiscal year. Sources in this order: (1) guidance from the latest "
        "earnings release, (2) guidance updates published after that "
        "(ad-hoc/press releases, guidance raises/cuts), (3) analyst "
        "consensus (Zacks, Visible Alpha, stockanalysis.com, "
        "Bloomberg-cited figures). Consensus counts as a fully valid "
        "source. FORBIDDEN: your own YoY/trend/run-rate extrapolation. "
        "GAAP vs non-GAAP rules: (a) analyst consensus EPS and net income "
        "figures are by default NON-GAAP ('adjusted EPS', 'adjusted net "
        "income') and belong in the non-GAAP fields (eps_diluted_non_gaap, "
        "net_income_non_gaap); (b) fill the GAAP fields (eps_diluted, "
        "net_income) ONLY if the source EXPLICITLY labels the figure as "
        "GAAP or 'reported' (e.g. 'GAAP EPS guidance'); (c) when in doubt, "
        "leave the GAAP field null; (d) for every value set 'basis' to "
        "'gaap' or 'non_gaap' exactly as the source labels it, or "
        "'unclear' if the source does not say. "
        "Return null only if neither guidance nor consensus exists for a "
        "metric. Absolute values in absolute "
        f"{getattr(company, 'currency', None) or 'USD'} base units "
        "(e.g. '$5.8 billion' -> 5800000000); EPS metrics as per-share "
        "values. Answer with ONLY one JSON object matching the schema in "
        "the user message — no prose, no markdown fences."
    )


def _build_user_prompt(company, year: int) -> str:
    lines = [
        f"Metrics to estimate for {company.name} ({company.ticker}) fiscal year {year}:",
        "",
    ]
    for key, desc in _METRIC_SPECS:
        lines.append(f"- {key}: {desc}")
    fields = ",\n".join(
        f'  "{key}": {{"value": <number|null>, "source": "guidance"|"consensus"|null, '
        f'"basis": "gaap"|"non_gaap"|"unclear", '
        f'"quote": <string|null>, "url": <string|null>}}'
        for key, _ in _METRIC_SPECS
    )
    lines += [
        "",
        "For each metric: value from the most recent guidance (midpoint of a "
        "range) or, if no guidance exists for it, from analyst consensus. "
        "quote = one short verbatim sentence documenting the guidance/"
        "consensus basis; url = source URL.",
        "",
        "Return JSON matching exactly this schema:",
        "",
        "{",
        fields,
        "}",
    ]
    return "\n".join(lines)


def _call_claude(company, year: int, cost_tracker=None) -> dict | None:
    """EIN Claude-Call mit Websuche fuer alle FY-Schaetzwerte. In Tests
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
            system=_build_system_prompt(company, year),
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": WEB_SEARCH_MAX_USES,
            }],
            messages=[{"role": "user", "content": _build_user_prompt(company, year)}],
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


def _parse_payload(data: dict, ticker: str, year: int) -> dict[str, dict]:
    """Antwort in {key: {value, source, quote, url}} normalisieren.
    Nur guidance/consensus sind gueltige Quellen — alles andere verwerfen."""
    parsed: dict[str, dict] = {}
    for key, _ in _METRIC_SPECS:
        entry = data.get(key)
        if not isinstance(entry, dict):
            continue
        value = _to_decimal(entry.get("value"))
        if value is None:
            continue
        source = entry.get("source")
        if source not in ("guidance", "consensus"):
            logger.warning(
                "guidance estimates %s/FY%s: %s ohne guidance/consensus-Quelle "
                "(%r) — verworfen", ticker, year, key, source,
            )
            continue
        quote = entry.get("quote")
        url = entry.get("url")
        basis = entry.get("basis")
        parsed[key] = {
            "value": value,
            "source": source,
            # Fehlend/unbekannt konservativ als "unclear" behandeln.
            "basis": basis if basis in ("gaap", "non_gaap") else "unclear",
            "quote": quote.strip() if isinstance(quote, str) else None,
            "url": url if isinstance(url, str) and url.startswith(("http://", "https://")) else None,
        }
    return parsed


def _rebook_by_basis(parsed: dict[str, dict], ticker: str, year: int) -> None:
    """GAAP-Slots nur bei explizit ausgewiesener GAAP-Basis zulassen.

    basis != "gaap" (non_gaap oder unclear) im GAAP-Slot -> in den
    adjusted-Sidecar umbuchen statt verwerfen; ist der Sidecar schon
    belegt, wird der GAAP-Slot-Wert verworfen. Fuer Keys ohne
    GAAP/Non-GAAP-Paar (revenue, capex, ...) ist basis informativ.
    """
    for gaap_key, adj_key in _GAAP_ADJ_PAIRS:
        info = parsed.get(gaap_key)
        if info is None or info["basis"] == "gaap":
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


def _prev_fy_actual(db, company_id, key: str, year: int) -> Decimal | None:
    row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == "FY",
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


def _apply_gates(db, company, year: int, parsed: dict[str, dict]) -> None:
    """Deterministische Plausibilitaets-Gates — mutiert `parsed` in place.

    1. Einheiten-Check: Absolutwerte unter 1 Mio (aber != 0) sind fast
       immer eine fehlende Skalierung.
    2. 60%-Vorjahres-Gate: FY-Schaetzung vs Vorjahres-Actual aus der DB.
       Vorzeichenwechsel ist erlaubt (Turnaround-Jahre).
    3. eps x shares_outstanding vs net_income (20%) wenn beide geliefert —
       welcher der beiden falsch ist, ist nicht entscheidbar: beide skippen.
    4. GAAP <= Non-GAAP + 1% wenn beide fuer denselben Key geliefert
       (US-Standard: adjusted >= GAAP durch SBC/Amortisations-Addbacks) —
       sonst beide verwerfen.
    """
    ticker = company.ticker
    for key in list(parsed):
        if key in _PER_SHARE_KEYS:
            continue
        v = parsed[key]["value"]
        if v != 0 and abs(v) < _UNIT_MIN:
            logger.warning(
                "guidance estimates %s/FY%s: %s=%s unter 1 Mio "
                "(Einheiten-Verdacht) — skip", ticker, year, key, v,
            )
            del parsed[key]

    for key in list(parsed):
        if key in _NON_GAAP_SIDECARS:
            continue
        prev = _prev_fy_actual(db, company.id, key, year)
        if prev is None or prev == 0:
            continue
        v = parsed[key]["value"]
        sign_flip = (v >= 0) != (prev >= 0)
        if not sign_flip and abs(v / prev - 1) > _PREV_DEVIATION_TOL:
            logger.warning(
                "guidance estimates %s/FY%s: %s=%s weicht >60%% vom "
                "Vorjahres-Actual %s ab — skip", ticker, year, key, v, prev,
            )
            del parsed[key]

    eps = parsed.get("eps_diluted")
    ni = parsed.get("net_income")
    if eps and ni and ni["value"] != 0:
        shares = _snapshot_shares(db, company.id)
        if shares:
            implied = eps["value"] * shares
            denom = max(abs(implied), abs(ni["value"]))
            if denom != 0 and abs(implied - ni["value"]) / denom > _EPS_NI_TOL:
                logger.warning(
                    "guidance estimates %s/FY%s: eps %s x shares %s = %s "
                    "passt nicht zu net_income %s (>20%%) — beide skip",
                    ticker, year, eps["value"], shares, implied, ni["value"],
                )
                del parsed["eps_diluted"]
                del parsed["net_income"]

    for gaap_key, adj_key in _GAAP_ADJ_PAIRS:
        g = parsed.get(gaap_key)
        a = parsed.get(adj_key)
        if g is None or a is None:
            continue
        if g["value"] > a["value"] + abs(a["value"]) * _GAAP_ADJ_TOL:
            logger.warning(
                "guidance estimates %s/FY%s: %s=%s > %s=%s + 1%% "
                "(adjusted muss >= GAAP sein) — beide skip",
                ticker, year, gaap_key, g["value"], adj_key, a["value"],
            )
            del parsed[gaap_key]
            del parsed[adj_key]


def _derive_adjusted_net_income(db, company, year: int, parsed: dict[str, dict]) -> None:
    """Fehlt ein direkter NI-adj-Konsens, aber es gibt Non-GAAP-EPS:
    net_income adjusted deterministisch als eps_adj x diluted shares
    (SNAPSHOT-Stammdaten) ableiten. Kein Modell-Schaetzwert — reine
    Multiplikation, als solche in der Quelle ausgewiesen."""
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
        "quote": "Abgeleitet: EPS-Konsens x Aktienzahl",
        "url": eps.get("url"),
        # Schwaechste Quelle: darf nur leere adjusted-Slots fuellen,
        # nie eine vorhandene Quartalssumme oder direkten Konsens ersetzen.
        "derived": True,
    }
    logger.info(
        "guidance estimates %s/FY%s: net_income adjusted abgeleitet "
        "(eps_adj %s x shares %s)", company.ticker, year, eps["value"], shares,
    )


def _compose_source_name(info: dict) -> str:
    parts = [f"FY-Guidance ({info['source']})"]
    if info.get("quote"):
        parts.append(f"quote={info['quote'][:400]}")
    return " | ".join(parts)[:4096]


def _upsert_fy_forecast(db, company, key: str, year: int, info: dict, now) -> CompanyValue | None:
    """FY-Forecast-Slot schreiben. Manual-/PDF-Zeilen sind authoritative,
    Currency-Konflikte blocken. Rueckgabe: geschriebene Zeile oder None."""
    rows = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == "FY",
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
            "guidance estimates currency mismatch BLOCKED %s/%s/FY%s: existing=%s new=%s",
            company.ticker, key, year, target.currency, currency,
        )
        target.last_refresh_attempt = now
        db.flush()
        return None

    value = normalize_sign(
        key, info["value"],
        context=f"guidance-estimate {company.ticker}/FY{year}",
    )

    if target is None:
        target = CompanyValue(
            id=uuid4(), company_id=company.id, value_key=key,
            period_type="FY", period_year=year, is_forecast=True,
        )
        # SAVEPOINT: Unique-Index-Kollision (Race mit parallelem Writer)
        # -> Slot-Zeile neu laden und Guards erneut anwenden.
        try:
            with db.begin_nested():
                db.add(target)
                db.flush()
        except IntegrityError:
            target = (
                db.query(CompanyValue)
                .filter(
                    CompanyValue.company_id == company.id,
                    CompanyValue.value_key == key,
                    CompanyValue.period_type == "FY",
                    CompanyValue.period_year == year,
                    CompanyValue.is_forecast.is_(True),
                )
                .first()
            )
            if target is None or target.manually_overridden or target.from_ir_pdf:
                return None
            if currency_conflict(key, target.currency, currency):
                target.last_refresh_attempt = now
                db.flush()
                return None

    target.numeric_value = value
    target.text_value = None
    target.source_name = _compose_source_name(info)
    target.source_link = info.get("url")
    target.primary_method = "web_guidance"
    target.is_forecast = True
    target.manually_overridden = False
    target.from_ir_pdf = False
    if currency:
        target.currency = currency
    target.fetched_at = now
    target.last_refresh_attempt = now
    db.flush()
    return target


def _forecast_row(db, company_id, key: str, year: int) -> CompanyValue | None:
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == year,
            CompanyValue.is_forecast.is_(True),
        )
        .first()
    )


def _ensure_sidecar_slot(db, company, key: str, year: int, now) -> CompanyValue | None:
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
            CompanyValue.period_type == "FY",
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
        period_type="FY", period_year=year, is_forecast=True,
    )
    if key in CURRENCY_KEYS:
        target.currency = company.currency
    target.fetched_at = now
    target.last_refresh_attempt = now
    # SAVEPOINT: Race mit parallelem Writer wie in _upsert_fy_forecast.
    try:
        with db.begin_nested():
            db.add(target)
            db.flush()
    except IntegrityError:
        target = _forecast_row(db, company.id, key, year)
        if target is None or target.manually_overridden or target.from_ir_pdf:
            return None
    return target


def fetch_guidance_estimates(db, company, year: int, cost_tracker=None) -> int:
    """Alle FY-Schaetzwerte des laufenden Geschaeftsjahres mit EINEM
    Claude-Websuche-Call holen und als FY-Forecasts persistieren.

    Nur US-Filer, nur fuers laufende (nicht abgeschlossene) FY — sonst 0.
    Rueckgabe: Anzahl geschriebener FY-Forecast-Zeilen.
    """
    from app.calculations.lock import is_us_company
    from app.config import settings
    from app.values.provider_anchor import _fy_is_closed

    if not is_us_company(company):
        return 0
    if _fy_is_closed(company, year):
        return 0
    if not settings.anthropic_api_key:
        return 0

    try:
        data = _call_claude(company, year, cost_tracker=cost_tracker)
    except Exception as e:
        logger.warning(
            "guidance estimates: Claude-Call failed fuer %s FY%s: %s",
            company.ticker, year, e,
        )
        return 0
    if not data:
        return 0

    parsed = _parse_payload(data, company.ticker, year)
    _rebook_by_basis(parsed, company.ticker, year)
    _apply_gates(db, company, year, parsed)
    _derive_adjusted_net_income(db, company, year, parsed)
    if not parsed:
        return 0

    now = datetime.now(timezone.utc)
    written = 0
    fy_rows: dict[str, CompanyValue] = {}
    for key in sorted(GUIDANCE_ESTIMATE_KEYS):
        info = parsed.get(key)
        if info is None:
            continue
        row = _upsert_fy_forecast(db, company, key, year, info, now)
        if row is not None:
            fy_rows[key] = row
            written += 1

    # Non-GAAP-Sidecars in numeric_value_adjusted der GAAP-FY-Zeile.
    # 'Manual'-Schutzlogik bewusst NICHT setzen — Quelle ist die
    # Konsens-/Guidance-Angabe (quote | url, ueberschreibbar).
    for ngaap_key, base_key in _NON_GAAP_SIDECARS.items():
        info = parsed.get(ngaap_key)
        if info is None:
            continue
        # Ohne GAAP-Quelle existiert kein GAAP-Write — Traeger-Zeile mit
        # leerem numeric_value anlegen, die adjusted-Spur traegt den Wert.
        row = fy_rows.get(base_key) or _ensure_sidecar_slot(db, company, base_key, year, now)
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
        # Quote-first-Format ('quote | url'): beginnt nie mit https —
        # bleibt damit fuer den naechsten Guidance-Lauf ueberschreibbar
        # (adjusted_is_protected schuetzt nur 'Manual' und reine URLs).
        src_parts = [info["quote"][:400] if info.get("quote") else info["source"]]
        if info.get("url"):
            src_parts.append(info["url"])
        row.numeric_value_adjusted = info["value"]
        row.adjustments_note = f"Non-GAAP FY-Estimate ({info['source']})"
        row.adjustments_source = " | ".join(src_parts)

    db.flush()
    logger.info(
        "guidance estimates %s/FY%s: %d FY-Forecasts geschrieben (%d Werte geliefert)",
        company.ticker, year, written, len(parsed),
    )
    return written
