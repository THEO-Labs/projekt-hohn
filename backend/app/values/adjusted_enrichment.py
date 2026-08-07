"""Non-GAAP-Anreicherung aus 8-K-Earnings-Releases (EDGAR).

Der Provider-First-Anker fuellt GAAP-Werte exakt aus XBRL und ueberspringt
damit die Two-Stage-LLM-Recherche, die frueher die Adjusted-Werte
(numeric_value_adjusted) mitlieferte. Fuer US-Filer stehen die Non-GAAP-
Werte zuverlaessig in der Earnings-Pressemitteilung (8-K Exhibit 99.x mit
GAAP-zu-Non-GAAP-Reconciliation-Tabelle) auf EDGAR. Dieses Modul holt sie
dort nach: EIN Claude-Call pro Periode fuellt net_income und eps_diluted
zusammen (das Non-GAAP-Kernpaar; ebitda/fcf bewusst nicht — US-Releases
sind da uneinheitlich).

Fill-only-NULL: bestehende Adjusted-Werte (manuell oder frueher
angereichert) werden nie ueberschrieben — idempotent, begrenzt LLM-Kosten.
"""
import json
import logging
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from html import unescape

from cachetools import TTLCache

from app.values.models import CompanyValue

logger = logging.getLogger(__name__)

# Modell wie der Two-Stage-Extractor (scripts/two_stage_research.py),
# Temperatur 0. Kein Web-Search-Tool — der Release-Text kommt im Prompt.
EXTRACT_MODEL = "claude-sonnet-4-6"

ADJUSTED_KEYS = ("net_income", "eps_diluted")

# Fenster nach Periodenende, in dem die Earnings-8-K erwartet wird.
RELEASE_WINDOW_DAYS = 75

# Reconciliation-Tabellen stehen im hinteren Teil des Releases — Text auf
# ~40k Zeichen kappen, Abschnitt um 'Non-GAAP'/'reconciliation' priorisieren.
TEXT_CAP_CHARS = 40_000

_PERIOD_ORDER = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 5}

# Persistenter Negativ-Marker: Release ohne verwertbare Reconciliation
# (bzw. endgueltig gescheiterter GAAP-Cross-Check). Zeilen mit genau
# dieser Note werden bei der Kandidaten-Auswahl uebersprungen — keine
# Dauer-Retries bei jedem Refresh. Der naechste Anker-Write raeumt die
# Note mit ab (source NULL), danach EIN neuer Versuch.
NO_RECONCILIATION_NOTE = "no non-GAAP reconciliation found"

# GAAP-Cross-Check-Toleranz, gestaffelt nach Herkunft: Tabellenwerte muessen
# praktisch exakt zum XBRL-Referenzwert passen (erzwingt die ungerundeten
# Reconciliation-Tabellen-Werte, z.B. 5,803 statt '$5.8 billion'); Freitext-
# Werte duerfen gerundet sein und bekommen einen Note-Zusatz.
GAAP_TOLERANCE_TABLE = Decimal("0.005")
GAAP_TOLERANCE_TEXT = Decimal("0.02")

# Note-Zusatz fuer Freitext-Werte (gerundete '$5.8 billion'-Angaben).
TEXT_NOTE_SUFFIX = " (aus Freitext, ggf. gerundet)"

_SYSTEM_PROMPT = (
    "You extract Non-GAAP (adjusted) figures from an earnings press release "
    "(8-K Exhibit 99). Read the GAAP-to-Non-GAAP reconciliation table and "
    "return the Non-GAAP (adjusted) net income and Non-GAAP diluted EPS for "
    "the requested period.\n"
    "Rules:\n"
    "- Values MUST come from the GAAP-to-Non-GAAP reconciliation TABLE of "
    "this release: the exact, unrounded figures as printed in the table "
    "(e.g. 5,803 million, NOT the rounded '$5.8 billion' from the prose). "
    "Set source_kind='table'.\n"
    "- ONLY if the release contains no reconciliation table may you take "
    "the figures from the narrative text — then set source_kind='text'.\n"
    "- Values must come from THIS release for the EXACT requested period — "
    "not prior-year comparison columns, not YTD columns.\n"
    "- non_gaap_net_income in absolute base units of the reporting currency "
    "(e.g. '$1,234.5 million' -> 1234500000).\n"
    "- non_gaap_diluted_eps as the per-share value.\n"
    "- gaap_net_income and gaap_diluted_eps: the GAAP values from the SAME "
    "column/period (or the same text passage) as the Non-GAAP values. They "
    "are used as a cross-check that you read the right column — without "
    "them the Non-GAAP values will be discarded.\n"
    "- If the release contains no GAAP-to-Non-GAAP reconciliation at all, or "
    "the requested period is not covered, use null for the missing value.\n"
    "- period_end_date: the period-end date of the COLUMN you read the "
    "values from, exactly as stated in the table header (e.g. 'Three Months "
    "Ended June 30, 2026' -> '2026-06-30'). ISO format YYYY-MM-DD; null if "
    "the header states no date.\n"
    "- adjustment_items: short comma-separated list of the adjustment line "
    "items (e.g. 'SBC, restructuring, amortization of intangibles'); empty "
    "string if none.\n"
    "Answer with ONLY this JSON object, no prose, no markdown fences:\n"
    '{"non_gaap_net_income": number|null, "non_gaap_diluted_eps": number|null, '
    '"gaap_net_income": number|null, "gaap_diluted_eps": number|null, '
    '"source_kind": "table"|"text", "period_end_date": "YYYY-MM-DD"|null, '
    '"adjustment_items": string}'
)

# Negative-Cache wie im EdgarProvider: fehlgeschlagene URLs 10 Minuten
# merken, sonst laeuft jede Periode desselben Laufs erneut in die volle
# Retry-Kette.
_fail_cache: TTLCache = TTLCache(maxsize=500, ttl=600)

_edgar = None


def _get_edgar():
    """EdgarProvider-Singleton: CIK-Aufloesung + Retry-HTTP-Client
    wiederverwenden statt duplizieren."""
    global _edgar
    if _edgar is None:
        from app.providers.edgar import EdgarProvider
        _edgar = EdgarProvider()
    return _edgar


def _resolve_cik(ticker: str) -> str | None:
    """CIK via EdgarProvider (10-stellig, zero-padded). In Tests gepatcht
    (conftest: Default None — kein Live-Netz)."""
    return _get_edgar()._get_cik(ticker)


def _fetch_json(url: str) -> dict | None:
    if url in _fail_cache:
        return None
    r = _get_edgar()._retried_get(url)
    if r is None or r.status_code >= 400:
        _fail_cache[url] = True
        return None
    try:
        return r.json()
    except Exception as e:
        logger.warning("adjusted enrichment: JSON parse failed for %s: %s", url, e)
        _fail_cache[url] = True
        return None


def _fetch_text(url: str) -> str | None:
    if url in _fail_cache:
        return None
    r = _get_edgar()._retried_get(url)
    if r is None or r.status_code >= 400:
        _fail_cache[url] = True
        return None
    return r.text


def _period_end(company, period_type: str, year: int) -> date | None:
    """Periodenende aus dem FY-Ende der Stammdaten (Default Kalenderjahr).
    FY endet am FY-Ende (= Q4-Ende)."""
    m = getattr(company, "fiscal_year_end_month", None) or 12
    d = getattr(company, "fiscal_year_end_day", None) or 31
    quarter = "Q4" if period_type == "FY" else period_type
    return _get_edgar()._q_end_date(year, quarter, m, d)


def _clean_html(html: str) -> str:
    """Tags grob strippen, Zeilenstruktur der Tabellen erhalten."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?is)<br\s*/?>|</(p|tr|div|table|h[1-6])>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _focus_text(text: str, limit: int = TEXT_CAP_CHARS) -> str:
    """Auf `limit` Zeichen kappen, Fenster um die Reconciliation-Sektion
    legen (steht im hinteren Teil des Releases)."""
    if len(text) <= limit:
        return text
    pos = None
    for pattern in (r"reconciliation", r"non-gaap"):
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        if matches:
            pos = matches[-1].start()
            break
    if pos is None:
        return text[-limit:]
    start = max(0, min(pos - limit // 2, len(text) - limit))
    return text[start:start + limit]


def _find_exhibit_url(cik: str, period_end: date, subs: dict) -> str | None:
    """Juengste Earnings-8-K im Fenster [Periodenende, +75 Tage] finden und
    deren Exhibit-99-HTML-URL liefern. 8-Ks mit Item 2.02 (Results of
    Operations) werden bevorzugt; Fallback ist das Primary-Doc. `subs` ist
    das Submissions-JSON der Firma (ein Fetch pro Aufruf, vom Caller
    gecacht)."""
    recent = subs.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    items_list = recent.get("items", [])
    window_end = period_end + timedelta(days=RELEASE_WINDOW_DAYS)

    candidates: list[tuple[date, bool, str, str]] = []
    for i, form in enumerate(forms):
        if form not in ("8-K", "8-K/A"):
            continue
        try:
            filed = date.fromisoformat(filing_dates[i])
        except (ValueError, IndexError):
            continue
        if not (period_end <= filed <= window_end):
            continue
        accn = accessions[i] if i < len(accessions) else ""
        primary = primary_docs[i] if i < len(primary_docs) else ""
        items = items_list[i] if i < len(items_list) else ""
        is_earnings = "2.02" in (items or "")
        candidates.append((filed, is_earnings, accn, primary))
    if not candidates:
        return None
    # Earnings-8-Ks (Item 2.02) vor sonstigen, jeweils juengste zuerst
    # (zwei stabile Sorts).
    candidates.sort(key=lambda c: c[0], reverse=True)
    candidates.sort(key=lambda c: not c[1])

    cik_int = int(cik)
    for _, _, accn, primary in candidates[:3]:
        if not accn:
            continue
        accn_clean = accn.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_clean}"
        index = _fetch_json(f"{base}/index.json")
        names = [
            item.get("name", "")
            for item in (index or {}).get("directory", {}).get("item", [])
        ]
        html_names = [
            n for n in names
            if n.lower().endswith((".htm", ".html")) and "index" not in n.lower()
        ]
        ex99 = [n for n in html_names if re.search(r"ex[-_]?99", n, re.IGNORECASE)]
        if ex99:
            return f"{base}/{sorted(ex99)[0]}"
        # Viele Filer benennen das Release-Exhibit ohne "ex99"
        # (Visa: q32026earningsrelease.htm) — Namensmuster als zweite Stufe,
        # Primary-Doc (8-K-Deckblatt ohne Tabellen) nur als letzter Fallback.
        named = [
            n for n in html_names
            if n != primary and re.search(r"(earnings|release|press)", n, re.IGNORECASE)
        ]
        if named:
            return f"{base}/{sorted(named)[0]}"
        if primary and primary.lower().endswith((".htm", ".html")):
            return f"{base}/{primary}"
    return None


def _extract_via_claude(
    text: str,
    company_name: str,
    ticker: str,
    period_label: str,
    period_end: date,
    currency: str | None,
    cost_tracker=None,
) -> dict | None:
    """Ein Claude-Call: Non-GAAP NI + Diluted EPS der Periode aus dem
    Release-Text. Kein Web-Search-Tool. Returns dict oder None."""
    import app.llm.claude as claude_mod
    client = claude_mod.get_client()
    user_content = (
        f"Company: {company_name} ({ticker})\n"
        f"Requested period: {period_label} (period ending {period_end.isoformat()})\n"
        f"Reporting currency: {currency or 'USD'}\n\n"
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
        logger.warning("adjusted enrichment: kein JSON in Claude-Antwort (%s %s)",
                       ticker, period_label)
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        logger.warning("adjusted enrichment: JSON parse failed (%s %s): %s",
                       ticker, period_label, e)
        return None
    return data if isinstance(data, dict) else None


# Toleranz fuer die Tabellenkopf-Datumspruefung: 52/53-Wochen-Kalender
# enden nicht exakt am Monatsletzten ('quarter ended June 28'). Ein
# falsches Quartal liegt ~90 Tage daneben und wird sicher erkannt.
PERIOD_END_TOLERANCE_DAYS = 21


def _period_end_matches(claimed, expected: date) -> bool:
    """Tabellenkopf-Datum gegen das Zielquartalsende pruefen. Fehlende oder
    unparsebare Angabe ist lenient True (aeltere Releases ohne klares
    Header-Datum); ein angegebenes Datum muss innerhalb der Toleranz am
    erwarteten Periodenende liegen — sonst hat das Modell die falsche
    Spalte gelesen (Q1 bekommt Q4-Wert), was der GAAP-Cross-Check bei nahe
    beieinanderliegenden Quartalswerten nicht fangen kann."""
    if not isinstance(claimed, str) or not claimed.strip():
        return True
    try:
        claimed_date = date.fromisoformat(claimed.strip())
    except ValueError:
        return True
    return abs((claimed_date - expected).days) <= PERIOD_END_TOLERANCE_DAYS


def _to_decimal(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _matches_gaap(claimed: Decimal | None, reference: Decimal,
                  tolerance: Decimal) -> bool:
    """GAAP-Cross-Check: das vom LLM gelesene GAAP-Pendant muss den DB-Wert
    derselben Periode innerhalb der Toleranz treffen (table: 0.5%, text: 2%).
    Ersetzt das alte 0.5x-2x-Ratio-Gate, das genau die wichtigen Faelle
    blockierte (GAAP-Verlust mit Non-GAAP-Gewinn, Impairment-Quartale)."""
    if claimed is None:
        return False
    if reference == 0:
        return claimed == 0
    return abs(claimed - reference) <= abs(reference) * tolerance


_ROUND_100M = Decimal("100000000")


def _is_round_100m(value: Decimal | None) -> bool:
    """True wenn der Wert glatt auf 100 Mio endet (Rundungs-Indiz: '$5.8
    billion' aus dem Freitext statt 5,803 aus der Tabelle)."""
    if value is None or value == 0:
        return False
    try:
        return value % _ROUND_100M == 0
    except InvalidOperation:
        return False


def _gaap_reference(db, company_id, key: str, ptype: str, year: int, period_rows) -> Decimal | None:
    """GAAP-Referenzwert der Periode fuer den Cross-Check: bevorzugt aus den
    Kandidaten-Zeilen, sonst aus der DB (z.B. wenn die NI-Zeile schon
    angereichert und damit kein Kandidat mehr ist)."""
    for r in period_rows:
        if r.value_key == key:
            return r.numeric_value
    row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == ptype,
            CompanyValue.period_year == year,
            CompanyValue.is_forecast.is_(False),
            CompanyValue.numeric_value.isnot(None),
        )
        .first()
    )
    return row.numeric_value if row else None


def _mark_no_reconciliation(row: CompanyValue) -> None:
    """Negativ-Marker setzen: adjusted bleibt NULL, adjustments_source
    bleibt NULL — nur die Note traegt den Skip-Marker."""
    row.adjustments_note = NO_RECONCILIATION_NOTE


def enrich_adjusted_from_earnings_releases(
    db, company, years: list[int], max_llm_calls: int = 8, cost_tracker=None,
) -> int:
    """Fuellt numeric_value_adjusted fuer net_income/eps_diluted aus den
    8-K-Earnings-Releases (Fill-only-NULL). Ein Claude-Call pro Periode
    deckt beide Keys. Rueckgabe: Anzahl angereicherter Perioden."""
    from app.calculations.lock import is_us_company
    if not is_us_company(company):
        return 0

    from sqlalchemy import or_

    rows = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company.id,
            CompanyValue.value_key.in_(ADJUSTED_KEYS),
            CompanyValue.period_year.in_(years),
            CompanyValue.period_type.in_(("FY", "Q1", "Q2", "Q3", "Q4")),
            CompanyValue.is_forecast.is_(False),
            CompanyValue.numeric_value.isnot(None),
            CompanyValue.numeric_value_adjusted.is_(None),
            # Negativ-Marker: bereits als "keine Reconciliation" markierte
            # Zeilen nicht erneut versuchen (keine Dauer-Retries).
            or_(
                CompanyValue.adjustments_note.is_(None),
                CompanyValue.adjustments_note != NO_RECONCILIATION_NOTE,
            ),
        )
        .all()
    )
    by_period: dict[tuple[int, str], list[CompanyValue]] = {}
    for row in rows:
        by_period.setdefault((row.period_year, row.period_type), []).append(row)
    if not by_period:
        return 0

    today = date.today()
    cik: str | None = None
    subs: dict | None = None
    llm_calls = 0
    enriched = 0
    # Absteigend nach Periode (juengste zuerst): der max_llm_calls-Deckel
    # soll die relevantesten Perioden zuerst nehmen.
    for (year, ptype), period_rows in sorted(
        by_period.items(),
        key=lambda kv: (kv[0][0], _PERIOD_ORDER.get(kv[0][1], 9)),
        reverse=True,
    ):
        if llm_calls >= max_llm_calls:
            logger.info(
                "%s adjusted enrichment: max_llm_calls=%d erreicht — Rest uebersprungen",
                company.ticker, max_llm_calls,
            )
            break
        period_end = _period_end(company, ptype, year)
        if period_end is None or period_end >= today:
            continue
        if cik is None:
            cik = _resolve_cik(company.ticker)
            if cik is None:
                logger.info("%s adjusted enrichment: keine CIK — skip", company.ticker)
                break
        if subs is None:
            # Submissions-JSON EINMAL pro Aufruf holen (statt pro Periode).
            subs = _fetch_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
            if not subs:
                logger.info(
                    "%s adjusted enrichment: keine Submissions — skip", company.ticker,
                )
                break
        exhibit_url = _find_exhibit_url(cik, period_end, subs)
        if exhibit_url is None:
            logger.info(
                "%s adjusted enrichment: kein 8-K im Fenster fuer %s FY%s — skip",
                company.ticker, ptype, year,
            )
            continue
        html = _fetch_text(exhibit_url)
        if not html:
            continue
        text = _focus_text(_clean_html(html))
        if not text:
            continue

        period_label = f"FY{year}" if ptype == "FY" else f"{ptype} FY{year}"
        currency = next((r.currency for r in period_rows if r.currency), None) \
            or getattr(company, "currency", None)
        llm_calls += 1
        try:
            data = _extract_via_claude(
                text, company.name, company.ticker, period_label, period_end,
                currency, cost_tracker=cost_tracker,
            )
        except Exception as e:
            logger.warning(
                "%s adjusted enrichment: Claude-Call failed fuer %s: %s",
                company.ticker, period_label, e,
            )
            continue
        if data is None:
            continue

        adj_values = {
            "net_income": _to_decimal(data.get("non_gaap_net_income")),
            "eps_diluted": _to_decimal(data.get("non_gaap_diluted_eps")),
        }
        if all(v is None for v in adj_values.values()):
            # Bewusstes null (keine Reconciliation im Release): Werte
            # verwerfen, Negativ-Marker persistieren.
            for row in period_rows:
                _mark_no_reconciliation(row)
            db.flush()
            continue

        # source_kind ist Pflicht: ohne die Herkunftsangabe (Tabelle vs
        # Freitext) ist keine Cross-Check-Toleranz zuordenbar — verwerfen.
        source_kind = data.get("source_kind")
        if source_kind not in ("table", "text"):
            logger.warning(
                "%s adjusted enrichment: source_kind fehlt/ungueltig (%r) "
                "fuer %s — verworfen",
                company.ticker, source_kind, period_label,
            )
            for row in period_rows:
                _mark_no_reconciliation(row)
            db.flush()
            continue

        # Perioden-Verifikation aus dem Tabellenkopf: falsche Spalte gelesen
        # -> verwerfen (Negativ-Marker wie beim Cross-Check-Fail).
        if not _period_end_matches(data.get("period_end_date"), period_end):
            logger.warning(
                "%s adjusted enrichment: period_end_date %r passt nicht zu %s "
                "(erwartet %s) — verworfen",
                company.ticker, data.get("period_end_date"), period_label,
                period_end.isoformat(),
            )
            for row in period_rows:
                _mark_no_reconciliation(row)
            db.flush()
            continue

        gaap_ni_claimed = _to_decimal(data.get("gaap_net_income"))
        gaap_eps_claimed = _to_decimal(data.get("gaap_diluted_eps"))
        ni_ref = _gaap_reference(db, company.id, "net_income", ptype, year, period_rows)
        eps_ref = _gaap_reference(db, company.id, "eps_diluted", ptype, year, period_rows)

        # Rundungs-Detektor: behauptete Tabellenwerte, die glatt auf 100 Mio
        # enden, waehrend unsere XBRL-Referenz ungerundet ist, stammen mit
        # hoher Wahrscheinlichkeit doch aus dem Freitext ('$5.8 billion') —
        # als text behandeln (2%-Toleranz + Freitext-Note).
        if (
            source_kind == "table"
            and _is_round_100m(adj_values["net_income"])
            and _is_round_100m(gaap_ni_claimed)
            and ni_ref is not None
            and not _is_round_100m(ni_ref)
        ):
            logger.warning(
                "%s adjusted enrichment: Tabellen-Claim mit glatt gerundeten "
                "Werten (non_gaap=%s, gaap=%s vs ref=%s) fuer %s — als "
                "Freitext behandelt",
                company.ticker, adj_values["net_income"], gaap_ni_claimed,
                ni_ref, period_label,
            )
            source_kind = "text"

        # GAAP-Cross-Check: NI-Check gegen den DB-GAAP-Wert derselben
        # Periode ist das primaere Gate fuer BEIDE Keys; EPS zusaetzlich
        # gegen die GAAP-EPS-Zeile, falls vorhanden. Ohne bestandenen
        # Cross-Check wird nie geschrieben. Toleranz gestaffelt: Tabellen-
        # werte muessen praktisch exakt passen, Freitext darf runden.
        tolerance = GAAP_TOLERANCE_TABLE if source_kind == "table" \
            else GAAP_TOLERANCE_TEXT
        ni_ok = _matches_gaap(gaap_ni_claimed, ni_ref, tolerance) \
            if ni_ref is not None else None
        eps_ok = _matches_gaap(gaap_eps_claimed, eps_ref, tolerance) \
            if eps_ref is not None else None
        gate_ok = ni_ok if ni_ok is not None else eps_ok

        if not gate_ok:
            # Gescheiterter Cross-Check (falsche Spalte/Periode gelesen oder
            # gerundeter Tabellen-Claim): Werte verwerfen, Negativ-Marker.
            logger.warning(
                "%s adjusted enrichment: GAAP-Cross-Check fehlgeschlagen fuer %s "
                "(source_kind=%s, gaap_ni=%s vs ref=%s, gaap_eps=%s vs ref=%s) — verworfen",
                company.ticker, period_label, source_kind,
                data.get("gaap_net_income"), ni_ref,
                data.get("gaap_diluted_eps"), eps_ref,
            )
            for row in period_rows:
                _mark_no_reconciliation(row)
            db.flush()
            continue

        items = data.get("adjustment_items") or ""
        note = f"Non-GAAP (Reconciliation 8-K): {items}" if items \
            else "Non-GAAP (Reconciliation 8-K)"
        if source_kind == "text":
            note += TEXT_NOTE_SUFFIX
        wrote_any = False
        for row in period_rows:
            # EPS nur schreiben, wenn der EPS-Check (falls pruefbar) auch
            # bestanden ist — sonst Marker gegen Dauer-Retries.
            if row.value_key == "eps_diluted" and eps_ok is False:
                _mark_no_reconciliation(row)
                continue
            adjusted = adj_values.get(row.value_key)
            if adjusted is None:
                # Reconciliation vorhanden, aber dieser Key fehlt im
                # Release — Marker, sonst wird die Periode ewig neu probiert.
                _mark_no_reconciliation(row)
                continue
            # Nur Adjusted-Felder schreiben — numeric_value/Methoden/Locks
            # bleiben unangetastet.
            row.numeric_value_adjusted = adjusted
            row.adjustments_note = note
            row.adjustments_source = exhibit_url
            wrote_any = True
        db.flush()
        if wrote_any:
            enriched += 1

    logger.info(
        "%s adjusted enrichment: %d Perioden angereichert, %d LLM-Calls",
        company.ticker, enriched, llm_calls,
    )
    return enriched
