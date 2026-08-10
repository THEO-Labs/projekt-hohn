"""GAAP-Bruecke fuer das Release-zu-10-Q-Fenster (8-K Earnings Release).

US-Filer veroeffentlichen die Quartalszahlen zuerst als 8-K-Earnings-
Release (Exhibit 99); das 10-Q-XBRL erscheint erst Tage bis Wochen
spaeter in der EDGAR-companyfacts-API. Die strikte EDGAR-only-Regel
laesst solche Zellen bis dahin auf not_found/Estimate stehen, obwohl
offizielle Zahlen existieren (Visa Q3 FY2026). Dieses Modul ueberbrueckt
das Fenster: EIN Claude-Call pro Periode extrahiert die GAAP-Werte aus
den Financial-Statement-TABELLEN des Releases und schreibt sie als
primary_method='provider'. Der spaetere XBRL-Quartals-Anker ueberschreibt
die Zeilen automatisch (provider -> provider ok, keine Locks).

Sicherungen (kein DB-GAAP-Referenzwert vorhanden — die Zelle ist leer):
  - period_end_date aus dem Tabellenkopf MUSS zum Zielquartal passen.
  - YTD-Konsistenz wo moeglich: berichtete Vorquartale aus der DB plus
    extrahierter Q-Wert muessen zur YTD-Spalte des Releases passen.
  - qsum-Validierung greift danach im Konsistenz-Pass.
"""
import json
import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from app.values import adjusted_enrichment as release_fetch
from app.values.adjusted_enrichment import (
    EXTRACT_MODEL,
    RELEASE_WINDOW_DAYS,
    _clean_html,
    _find_exhibit_url,
    _period_end,
    _to_decimal,
)
from app.values.currency_keys import CURRENCY_KEYS
from app.values.models import CompanyValue
from app.values.persistence import currency_conflict, normalize_sign
from app.values.sign_keys import ALWAYS_POSITIVE_KEYS

logger = logging.getLogger(__name__)

# Keys, die in den Standard-Statements eines US-Earnings-Releases stehen
# (Statements of Operations + Cash Flows). capex/fcf bewusst nicht —
# capex-Zeilenbenennungen sind uneinheitlich, fcf ist non-GAAP.
BRIDGE_KEYS = (
    "revenue", "net_income", "eps_diluted",
    "operating_cash_flow", "sbc", "dividends", "buyback_volume",
)

# Anzeige-Quelle; der XBRL-Anker ersetzt die Zeile beim naechsten Refresh.
BRIDGE_SOURCE_NAME = "SEC EDGAR 8-K Earnings Release (bis 10-Q-XBRL)"

# Cash-Flow-Statements der Releases sind meist YTD — die Standalone-Q-
# Ableitung (YTD minus Vorquartale) und der YTD-Cross-Check brauchen die
# vollstaendigen Vorquartals-Actuals aus der DB.
_PRIOR_QUARTERS = {"Q1": (), "Q2": ("Q1",), "Q3": ("Q1", "Q2"), "Q4": ("Q1", "Q2", "Q3")}

# YTD-Cross-Check-Toleranz: Tabellenwerte sind ungerundet, DB-Vorquartale
# XBRL-exakt — 1% deckt Rundungsreste ab, faengt falsche Spalten.
_YTD_TOLERANCE = Decimal("0.01")

# EPS ist wegen Weighted-Average-Shares nicht additiv — von YTD-Ableitung
# und YTD-Cross-Check ausgenommen (period_end-Check + qsum-Pass greifen).
_NO_YTD_KEYS = frozenset({"eps_diluted"})

# Statements stehen im hinteren Teil des Releases; grosszuegiger kappen
# als beim Reconciliation-Fokus (mehrere Tabellen noetig).
_TEXT_CAP_CHARS = 60_000

_PERIOD_ORDER = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 5}

_SYSTEM_PROMPT = (
    "You extract GAAP figures from the FINANCIAL STATEMENT TABLES of an "
    "earnings press release (8-K Exhibit 99): the condensed consolidated "
    "statements of operations and statements of cash flows.\n"
    "Rules:\n"
    "- Values MUST come from the financial statement tables: the exact, "
    "unrounded figures as printed (e.g. 5,803 million -> 5803000000). "
    "NEVER use rounded prose numbers ('$5.8 billion').\n"
    "- Use the column of the EXACT requested period — not prior-year "
    "comparison columns.\n"
    "- period_end_date: the period-end date of that column as stated in "
    "the table header (e.g. 'Three Months Ended June 30, 2026' -> "
    "'2026-06-30'), ISO YYYY-MM-DD. REQUIRED whenever the header states "
    "a date.\n"
    "- Requested keys (absolute base units of the reporting currency; "
    "eps_diluted as per-share value):\n"
    "    revenue: net revenues / total revenues\n"
    "    net_income: GAAP net income\n"
    "    eps_diluted: GAAP diluted earnings per share\n"
    "    operating_cash_flow: net cash provided by operating activities\n"
    "    sbc: share-based compensation (cash flow statement)\n"
    "    dividends: dividends paid (financing activities)\n"
    "    buyback_volume: repurchases of common stock (financing activities)\n"
    "- Cash-flow tables in releases are often YEAR-TO-DATE ('Six/Nine "
    "Months Ended'): report such figures under ytd_values and set the "
    "same key in values to null UNLESS the table also shows a standalone "
    "single-quarter column.\n"
    "- ytd_values: fiscal-year-to-date figures for the same keys when the "
    "release shows a YTD column; null otherwise. For a Q1 release the "
    "three-month figure counts as values, not ytd_values.\n"
    "- Use null for anything not printed in the tables. Do not estimate.\n"
    "Answer with ONLY this JSON object, no prose, no markdown fences:\n"
    '{"period_end_date": "YYYY-MM-DD"|null, '
    '"values": {"revenue": number|null, "net_income": number|null, '
    '"eps_diluted": number|null, "operating_cash_flow": number|null, '
    '"sbc": number|null, "dividends": number|null, '
    '"buyback_volume": number|null}, '
    '"ytd_values": {"revenue": number|null, "net_income": number|null, '
    '"eps_diluted": number|null, "operating_cash_flow": number|null, '
    '"sbc": number|null, "dividends": number|null, '
    '"buyback_volume": number|null}}'
)


def _focus_statements(text: str, limit: int = _TEXT_CAP_CHARS) -> str:
    """Fenster um die Financial-Statement-Tabellen legen (analog
    adjusted_enrichment._focus_text, anderer Anker)."""
    import re
    if len(text) <= limit:
        return text
    pos = None
    for pattern in (r"statements? of operations", r"income statements?",
                    r"statements? of cash flows"):
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            pos = m.start()
            break
    if pos is None:
        return text[-limit:]
    start = max(0, min(pos - limit // 4, len(text) - limit))
    return text[start:start + limit]


def has_item_202_8k(subs: dict, period_end: date) -> bool:
    """True wenn im Fenster [Periodenende, +RELEASE_WINDOW_DAYS] ein 8-K
    mit Item 2.02 (Results of Operations) gefiled wurde."""
    recent = subs.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    items_list = recent.get("items", [])
    window_end = period_end + timedelta(days=RELEASE_WINDOW_DAYS)
    for i, form in enumerate(forms):
        if form not in ("8-K", "8-K/A"):
            continue
        try:
            filed = date.fromisoformat(filing_dates[i])
        except (ValueError, IndexError):
            continue
        if not (period_end <= filed <= window_end):
            continue
        items = items_list[i] if i < len(items_list) else ""
        if "2.02" in (items or ""):
            return True
    return False


def has_reported_8k(ticker: str, period_end: date, cache: dict) -> bool:
    """Gemeinsames Berichtet-Kriterium innerhalb der Karenzfrist: existiert
    ein Item-2.02-8-K nach dem Periodenende? Das Submissions-JSON wird pro
    Ticker genau EINMAL in den uebergebenen Cache geholt (Caller haelt den
    Cache pro Aufruf). Fetch-Fehler liefern False — der Caller faellt damit
    konservativ auf die reine Karenz-Regel zurueck."""
    if ticker not in cache:
        subs = None
        cik = release_fetch._resolve_cik(ticker)
        if cik is not None:
            subs = release_fetch._fetch_json(
                f"https://data.sec.gov/submissions/CIK{cik}.json"
            )
        cache[ticker] = subs or None
    subs = cache[ticker]
    if not subs:
        return False
    return has_item_202_8k(subs, period_end)


def _cell_rows(db, company_id, key: str, ptype: str, year: int) -> list[CompanyValue]:
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == ptype,
            CompanyValue.period_year == year,
        )
        .order_by(CompanyValue.is_forecast.asc())
        .all()
    )


def _cell_needs_bridge(rows: list[CompanyValue]) -> bool:
    """True wenn die Zelle not_found/leer, nur eine Schaetzung oder ein
    Freitext-LLM-Actual (two_stage_*) ist — tabellenstrikte 8-K-Werte
    schlagen Freitext-LLM-Werte. Manuelle ACTUAL-Zeilen, gefuellte
    PDF-Zeilen und Provider-Actuals (XBRL-Anker) sind authoritative —
    kein Bridge-Write. Manuelle FORECAST-Zeilen sperren nur Schaetz-
    Writer: berichtete 8-K-Zahlen duerfen sie ersetzen."""
    for r in rows:
        if (r.manually_overridden and not r.is_forecast) or (
            r.from_ir_pdf and r.numeric_value is not None
        ):
            return False
    actual = next((r for r in rows if not r.is_forecast), None)
    if actual is not None and actual.numeric_value is not None:
        return (actual.primary_method or "").startswith("two_stage")
    return True


def _prior_quarter_sum(db, company_id, key: str, year: int, ptype: str) -> Decimal | None:
    """Summe der berichteten Vorquartals-Actuals; None wenn unvollstaendig.
    Fuer Q1 ist die Summe 0 (keine Vorquartale)."""
    priors = _PRIOR_QUARTERS.get(ptype)
    if priors is None:
        return None
    total = Decimal("0")
    for q in priors:
        row = (
            db.query(CompanyValue)
            .filter(
                CompanyValue.company_id == company_id,
                CompanyValue.value_key == key,
                CompanyValue.period_type == q,
                CompanyValue.period_year == year,
                CompanyValue.is_forecast.is_(False),
                CompanyValue.numeric_value.isnot(None),
            )
            .first()
        )
        if row is None:
            return None
        total += row.numeric_value
    return total


def _extract_via_claude(
    text: str,
    company_name: str,
    ticker: str,
    period_label: str,
    period_end: date,
    currency: str | None,
    keys: list[str],
    cost_tracker=None,
) -> dict | None:
    """Ein Claude-Call pro Periode: alle fehlenden Keys gleichzeitig aus
    den Statement-Tabellen. Kein Web-Search-Tool."""
    import app.llm.claude as claude_mod
    client = claude_mod.get_client()
    user_content = (
        f"Company: {company_name} ({ticker})\n"
        f"Requested period: {period_label} (period ending {period_end.isoformat()})\n"
        f"Reporting currency: {currency or 'USD'}\n"
        f"Keys needed: {', '.join(keys)}\n\n"
        f"Earnings release text:\n{text}"
    )
    response = client.messages.create(
        model=EXTRACT_MODEL,
        max_tokens=1024,
        temperature=0,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    if cost_tracker is not None:
        cost_tracker.add_response(response, EXTRACT_MODEL)
    parts = [getattr(block, "text", None) for block in response.content]
    raw = "\n".join(p for p in parts if p).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        logger.warning("gaap bridge: kein JSON in Claude-Antwort (%s %s)",
                       ticker, period_label)
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        logger.warning("gaap bridge: JSON parse failed (%s %s): %s",
                       ticker, period_label, e)
        return None
    return data if isinstance(data, dict) else None


def _period_end_ok(claimed, expected: date) -> bool:
    """Pflicht-Verifikation der Periodenspalte: das Tabellenkopf-Datum MUSS
    vorhanden sein und zum Zielquartal passen — ohne DB-GAAP-Referenzwert
    (die Zelle ist leer) ist das der primaere Falsche-Spalte-Schutz.
    Toleranz fuer 52/53-Wochen-Kalender wie in adjusted_enrichment."""
    if not isinstance(claimed, str) or not claimed.strip():
        return False
    try:
        claimed_date = date.fromisoformat(claimed.strip())
    except ValueError:
        return False
    tol = release_fetch.PERIOD_END_TOLERANCE_DAYS
    return abs((claimed_date - expected).days) <= tol


def _write_bridge_value(
    db, company, key: str, ptype: str, year: int,
    value: Decimal, exhibit_url: str, note: str | None = None,
) -> bool:
    """Zelle mit dem Release-Wert beschreiben (Muster _anchor_one_quarter_cell):
    actual-Slot bevorzugt, sonst Forecast-Slot auf Actual umziehen."""
    rows = _cell_rows(db, company.id, key, ptype, year)
    currency = company.currency if key in CURRENCY_KEYS else None
    row = next((r for r in rows if not r.is_forecast), None)
    if row is None:
        row = next(iter(rows), None)
    # Vor der Mutation festhalten: ersetzt dieser Write eine manuelle
    # Forecast-Zeile? Dann werden unten auch Manual-Adjusted-Felder geleert.
    replacing_manual_forecast = bool(
        row is not None and row.manually_overridden and row.is_forecast
    )
    if row is not None and currency_conflict(key, row.currency, currency):
        logger.warning(
            "gaap bridge currency mismatch BLOCKED %s/%s/%s FY%s: existing=%s new=%s",
            company.ticker, key, ptype, year, row.currency, currency,
        )
        return False

    from datetime import datetime, timezone
    from uuid import uuid4
    from sqlalchemy.exc import IntegrityError
    from app.values.persistence import adjusted_is_protected

    value = normalize_sign(
        key, value, context=f"gaap-bridge {company.ticker}/{ptype} FY{year}",
    )
    now = datetime.now(timezone.utc)
    source_name = BRIDGE_SOURCE_NAME + (f" | {note}" if note else "")

    if row is None:
        row = CompanyValue(
            id=uuid4(), company_id=company.id, value_key=key,
            period_type=ptype, period_year=year, is_forecast=False,
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            logger.warning(
                "gaap bridge insert race %s/%s/%s FY%s — skip",
                company.ticker, key, ptype, year,
            )
            return False
    try:
        with db.begin_nested():
            row.numeric_value = value
            row.text_value = None
            if currency is not None:
                row.currency = currency
            row.source_name = source_name[:4000]
            row.source_link = exhibit_url
            row.primary_method = "provider"
            row.is_forecast = False
            row.from_ir_pdf = False
            row.manually_overridden = False
            # Stale LLM-Adjusted-Werte abraeumen wie der XBRL-Anker; das
            # 8-K-Enrichment fuellt danach nach. Geschuetzte (Manual/URL-
            # belegte) Adjusted bleiben. Ausnahme: beim Ersetzen einer
            # manuellen Forecast-Zeile faellt auch Manual-Adjusted
            # (Schaetz-Ableger, Enrichment fuellt neu).
            if replacing_manual_forecast or not adjusted_is_protected(
                row.adjustments_source
            ):
                row.numeric_value_adjusted = None
                row.adjustments_note = None
                row.adjustments_source = None
            row.consistency_flags = None
            row.fetched_at = now
            row.last_refresh_attempt = now
            db.flush()
    except IntegrityError:
        logger.warning(
            "gaap bridge slot race %s/%s/%s FY%s — skip",
            company.ticker, key, ptype, year,
        )
        return False
    return True


def bridge_gaap_from_earnings_releases(
    db, company, years: list[int], max_llm_calls: int = 8, cost_tracker=None,
) -> int:
    """Fuellt not_found/Estimate-Zellen berichteter Perioden mit den
    GAAP-Werten aus dem 8-K-Earnings-Release und ueberschreibt Freitext-
    LLM-Actuals (two_stage_*). Nur US-Filer. Ein Claude-Call pro Periode
    deckt alle fehlenden Keys. Rueckgabe: Anzahl geschriebener Zellen."""
    from app.calculations.lock import is_us_company
    from app.values.detail_page import REPORTING_GRACE_DAYS
    if not is_us_company(company):
        return 0

    today = date.today()
    cik: str | None = None
    subs: dict | None = None
    llm_calls = 0
    written = 0

    # Kandidaten sammeln: (year, ptype) -> fehlende Keys. Juengste Periode
    # zuerst, damit der max_llm_calls-Deckel die relevanteste nimmt.
    candidates: list[tuple[int, str, date, list[str]]] = []
    for year in sorted(set(years), reverse=True):
        for ptype in ("FY", "Q4", "Q3", "Q2", "Q1"):
            period_end = _period_end(company, ptype, year)
            if period_end is None or period_end >= today:
                continue
            missing = [
                key for key in BRIDGE_KEYS
                if _cell_needs_bridge(_cell_rows(db, company.id, key, ptype, year))
            ]
            if missing:
                candidates.append((year, ptype, period_end, missing))
    if not candidates:
        return 0
    candidates.sort(
        key=lambda c: (c[0], _PERIOD_ORDER.get(c[1], 9)), reverse=True,
    )

    for year, ptype, period_end, missing in candidates:
        if llm_calls >= max_llm_calls:
            logger.info(
                "%s gaap bridge: max_llm_calls=%d erreicht — Rest uebersprungen",
                company.ticker, max_llm_calls,
            )
            break
        if cik is None:
            cik = release_fetch._resolve_cik(company.ticker)
            if cik is None:
                logger.info("%s gaap bridge: keine CIK — skip", company.ticker)
                return written
        if subs is None:
            subs = release_fetch._fetch_json(
                f"https://data.sec.gov/submissions/CIK{cik}.json"
            )
            if not subs:
                logger.info("%s gaap bridge: keine Submissions — skip", company.ticker)
                return written
        # Berichtet-Gate: Grace-Frist vorbei ODER ein Item-2.02-8-K nach
        # Periodenende existiert (Release da, 10-Q-XBRL noch nicht).
        grace_passed = (today - period_end).days >= REPORTING_GRACE_DAYS
        if not grace_passed and not has_item_202_8k(subs, period_end):
            continue
        exhibit_url = _find_exhibit_url(cik, period_end, subs)
        if exhibit_url is None:
            logger.info(
                "%s gaap bridge: kein 8-K im Fenster fuer %s FY%s — skip",
                company.ticker, ptype, year,
            )
            continue
        html = release_fetch._fetch_text(exhibit_url)
        if not html:
            continue
        text = _focus_statements(_clean_html(html))
        if not text:
            continue

        period_label = f"FY{year}" if ptype == "FY" else f"{ptype} FY{year}"
        llm_calls += 1
        try:
            data = _extract_via_claude(
                text, company.name, company.ticker, period_label, period_end,
                company.currency, missing, cost_tracker=cost_tracker,
            )
        except Exception as e:
            logger.warning(
                "%s gaap bridge: Claude-Call failed fuer %s: %s",
                company.ticker, period_label, e,
            )
            continue
        if data is None:
            continue
        # Pflicht: Periodenspalte muss verifiziert zum Zielquartal passen.
        if not _period_end_ok(data.get("period_end_date"), period_end):
            logger.warning(
                "%s gaap bridge: period_end_date %r passt nicht zu %s "
                "(erwartet %s) — verworfen",
                company.ticker, data.get("period_end_date"), period_label,
                period_end.isoformat(),
            )
            continue

        values = data.get("values") or {}
        ytd_values = data.get("ytd_values") or {}
        for key in missing:
            v = _to_decimal(values.get(key))
            ytd = _to_decimal(ytd_values.get(key))
            # Outflow-Keys frueh auf die DB-Konvention (positiv) bringen,
            # damit Cross-Check und Ableitung vorzeichenkonsistent rechnen.
            if key in ALWAYS_POSITIVE_KEYS:
                v = abs(v) if v is not None else None
                ytd = abs(ytd) if ytd is not None else None
            note = None
            if ptype != "FY" and key not in _NO_YTD_KEYS:
                prior_sum = _prior_quarter_sum(db, company.id, key, year, ptype)
                if v is not None and ytd is not None and prior_sum is not None:
                    # YTD-Konsistenz als Cross-Check-Ersatz: Vorquartale
                    # (DB-Actuals) + extrahierter Q-Wert muessen die YTD-
                    # Angabe des Releases treffen — sonst falsche Spalte.
                    if ytd != 0 and abs((prior_sum + v) - ytd) > abs(ytd) * _YTD_TOLERANCE:
                        logger.warning(
                            "%s gaap bridge: YTD-Konsistenz verletzt %s/%s "
                            "(prior %s + q %s != ytd %s) — Key verworfen",
                            company.ticker, key, period_label, prior_sum, v, ytd,
                        )
                        continue
                elif v is None and ytd is not None and prior_sum is not None:
                    # Standalone-Q aus YTD minus berichteten Vorquartalen
                    # ableiten (Cash-Flow-Statements sind meist YTD-only).
                    v = ytd - prior_sum
                    note = (
                        f"{ptype} = YTD {ytd} minus Summe berichteter "
                        f"Vorquartale {prior_sum}"
                    )
            if v is None:
                continue
            try:
                if _write_bridge_value(db, company, key, ptype, year, v,
                                       exhibit_url, note=note):
                    written += 1
            except InvalidOperation:
                continue
        db.flush()

    logger.info(
        "%s gaap bridge: %d Zellen geschrieben, %d LLM-Calls",
        company.ticker, written, llm_calls,
    )
    return written
