"""XBRL-Provider-Anker fuer abgeschlossene Geschaeftsjahre.

Der FY-Refresh laeuft ueber die Two-Stage-LLM-Pipeline (Websuche). Fuer
bereits abgeschlossene Jahre sind die strukturierten XBRL-Provider
(EDGAR fuer US, ESEF fuer EU) die verlaesslichere Quelle: maschinen-
lesbare Filings schlagen LLM-Recherche. Dieses Modul ueberschreibt nach
dem Two-Stage-Apply die FY-Zeile mit dem Provider-Wert, sofern die
Provider-Kette einen liefert.

Invarianten wie im Refresh-Schreibpfad (routes._process_one_key):
Sign-Normalisierung via persistence.normalize_sign, Currency-Konflikt
blockt den Write (nur last_refresh_attempt wird gestempelt). Lock-
Kontrakt: manuelle ACTUAL-Zeilen und from_ir_pdf bleiben unangetastet;
manuelle FORECAST-Zeilen (Schaetz-Override) duerfen durch berichtete
XBRL-Zahlen ersetzt werden.
"""
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.providers.registry import get_providers
from app.values.models import CompanyValue
from app.values.persistence import adjusted_is_protected, currency_conflict, normalize_sign

logger = logging.getLogger(__name__)

# Quartals-Anker: st_debt bewusst ausgenommen — liefert quartalsweise nur
# Teilkomponenten (kein Sum-Handling wie im FY-Pfad, siehe edgar.py
# BALANCE_KEYS); Quartale kommen nur ueber die 8-K-Bruecke. lt_debt ist
# seit dem strikten Instant-Pfad (nur LongTermDebtNoncurrent, edgar.py
# BALANCE_KEYS) wieder zugelassen — der alte unsichere Weg (Fallback auf
# LongTermDebt inkl. Current Maturities) existiert nicht mehr.
# shares_outstanding ist ein Stammdaten-Key ohne Quartalspfad.
QUARTER_ANCHOR_EXCLUDED_KEYS = frozenset({"st_debt", "shares_outstanding"})


def _fy_is_closed(company, year: int) -> bool:
    """True wenn das Geschaeftsjahr `year` bereits beendet ist.

    Mit FY-Ende aus den Stammdaten wenn vorhanden (z.B. Apple: Ende
    September), sonst Kalenderjahr-Heuristik (year < aktuelles Jahr).
    """
    today = date.today()
    m = getattr(company, "fiscal_year_end_month", None)
    d = getattr(company, "fiscal_year_end_day", None)
    if m and d:
        try:
            fy_end = date(year, m, d)
        except ValueError:
            # 29.02. in Nicht-Schaltjahr — konservativ auf den 28. runden
            fy_end = date(year, m, 28)
        return fy_end < today
    return year < today.year


def _fetch_from_chain(company, key: str, year: int):
    """Provider-Kette wie routes._try_providers: kaskadierende kwargs fuer
    die unterschiedlichen fetch-Signaturen (ESEF braucht isin, EDGAR das
    FY-Ende, Yahoo nur die Minimal-Signatur).

    Kundenentscheid: fuer Nicht-US-Firmen schreibt der Marktdaten-Feed
    (provider_kind='market', Yahoo) keine Fundamental-Werte mehr — nur
    offizielle Quellen (ESEF-XBRL) bleiben als Anker-Wertequelle. Er
    dient dort nur noch als Plausibilitaets-Referenz (yahoo_reference_map
    -> statement_research). US-Filer sind nicht betroffen."""
    from app.calculations.lock import is_us_company

    skip_market = not is_us_company(company)
    fy_end_month = getattr(company, "fiscal_year_end_month", None)
    fy_end_day = getattr(company, "fiscal_year_end_day", None)
    isin = getattr(company, "isin", None)
    for provider in get_providers(key):
        if skip_market and getattr(provider, "provider_kind", None) == "market":
            continue
        try:
            result = None
            for kwargs in (
                {"fy_end_month": fy_end_month, "fy_end_day": fy_end_day, "isin": isin},
                {"fy_end_month": fy_end_month, "fy_end_day": fy_end_day},
                {},
            ):
                try:
                    result = provider.fetch(company.ticker, key, "FY", year, **kwargs)
                    break
                except TypeError:
                    continue
            if result is not None:
                return result
        except Exception as e:
            logger.warning(
                "Provider-Anker fetch failed for %s/%s/FY%s via %s: %s",
                company.ticker, key, year, getattr(provider, "name", provider), e,
            )
    return None


def anchor_fy_with_provider(db, company, key: str, year: int) -> bool:
    """Ueberschreibt die FY-Zeile in company_values mit dem strukturierten
    Provider-Wert (XBRL schlaegt LLM als FY-Anker).

    Nur fuer abgeschlossene Geschaeftsjahre; laufende Jahre gehoeren der
    Estimate-/Two-Stage-Pipeline. Rueckgabe True wenn geschrieben wurde.
    """
    if not _fy_is_closed(company, year):
        return False

    rows = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == year,
        )
        .all()
    )
    # Actual-Zeile bevorzugen falls parallel noch eine Alt-Forecast-Zeile
    # aus der Zeit existiert, als das Jahr noch lief.
    row = next((r for r in rows if not r.is_forecast), rows[0] if rows else None)

    # Lock-Kontrakt: manuelle ACTUAL-Zeilen und PDF-Werte sind authoritative —
    # nicht anfassen, auch keinen Provider-Call verschwenden. Eine manuelle
    # FORECAST-Zeile war nur ein Schaetz-Override: liegen jetzt berichtete
    # XBRL-Zahlen vor, darf der Anker sie ersetzen.
    if row is not None and (
        (row.manually_overridden and not row.is_forecast) or row.from_ir_pdf
    ):
        return False
    # Vor der Mutation festhalten: ersetzt dieser Write eine manuelle
    # Forecast-Zeile? Dann werden unten auch Manual-Adjusted-Felder geleert.
    replacing_manual_forecast = bool(
        row is not None and row.manually_overridden and row.is_forecast
    )

    result = _fetch_from_chain(company, key, year)
    if result is None or not isinstance(result.value, Decimal):
        return False

    now = datetime.now(timezone.utc)
    if row is not None and currency_conflict(key, row.currency, result.currency):
        # Harter Reject wie im Refresh-Pfad: alter Wert bleibt, Versuch
        # wird gestempelt damit das Frontend Stale anzeigen kann.
        logger.warning(
            "Provider-Anker currency mismatch BLOCKED %s/%s/FY%s: existing=%s new=%s (source=%s)",
            company.ticker, key, year, row.currency, result.currency, result.source_name,
        )
        row.last_refresh_attempt = now
        db.flush()
        return False

    if row is None and currency_conflict(
        key, getattr(company, "currency", None), result.currency
    ):
        # Neuanlage: keine bestehende Zeile als Referenz, aber die Company-
        # Waehrung. Weicht der Provider davon ab, wird NICHT geschrieben —
        # sonst landet z.B. ein USD-Wert kommentarlos in einer EUR-Firma.
        logger.warning(
            "Provider-Anker currency mismatch on create BLOCKED %s/%s/FY%s: company=%s new=%s (source=%s)",
            company.ticker, key, year, company.currency, result.currency, result.source_name,
        )
        return False

    value = normalize_sign(
        key, result.value,
        context=f"provider-anchor {company.ticker}/FY{year} source={result.source_name}",
    )

    if row is None:
        row = CompanyValue(
            id=uuid4(), company_id=company.id, value_key=key,
            period_type="FY", period_year=year, is_forecast=False,
        )
        # SAVEPOINT: Unique-Index-Kollision (Race mit parallelem Writer) ->
        # Slot-Zeile neu laden und mit denselben Guards wie oben behandeln.
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            row = (
                db.query(CompanyValue)
                .filter(
                    CompanyValue.company_id == company.id,
                    CompanyValue.value_key == key,
                    CompanyValue.period_type == "FY",
                    CompanyValue.period_year == year,
                    CompanyValue.is_forecast.is_(False),
                )
                .first()
            )
            if row is None:
                logger.warning(
                    "Provider-Anker insert race %s/%s/FY%s: IntegrityError, "
                    "aber keine Zeile gefunden — skip",
                    company.ticker, key, year,
                )
                return False
            # Guards wie im Normalpfad; die neu geladene Zeile ist per
            # Filter der actual-Slot — Manual dort ist Hard-Lock.
            if (row.manually_overridden and not row.is_forecast) or row.from_ir_pdf:
                return False
            if currency_conflict(key, row.currency, result.currency):
                logger.warning(
                    "Provider-Anker currency mismatch BLOCKED (race) %s/%s/FY%s: existing=%s new=%s",
                    company.ticker, key, year, row.currency, result.currency,
                )
                row.last_refresh_attempt = now
                db.flush()
                return False

    row.numeric_value = value
    row.text_value = None
    # Currency nur setzen wenn der Provider eine liefert — sonst bestehendes
    # Label (z.B. aus dem Two-Stage-Write) erhalten.
    if result.currency is not None:
        row.currency = result.currency
    row.source_name = result.source_name
    row.source_link = result.source_link
    row.primary_method = "provider"
    row.is_forecast = False
    row.from_ir_pdf = False
    row.manually_overridden = False
    # Stale LLM-Adjusted-Werte abraeumen: der apply_to_db-Guard fasst
    # provider-Actuals nie wieder an, sonst friert ein alter Adjusted-Wert
    # neben dem frischen GAAP-Wert ein. Geschuetzt sind nur Manual-Eintraege
    # und 8-K-Enrichment (SEC-URL) — Two-Stage-Sources werden abgeraeumt,
    # das Enrichment fuellt danach billiger/besser nach. Ausnahme: beim
    # Ersetzen einer manuellen Forecast-Zeile wird auch Manual-Adjusted
    # geleert — er war nur ein Schaetz-Ableger, das Enrichment fuellt neu.
    if replacing_manual_forecast or not adjusted_is_protected(row.adjustments_source):
        row.numeric_value_adjusted = None
        row.adjustments_note = None
        row.adjustments_source = None
    row.consistency_flags = None
    row.fetched_at = now
    row.last_refresh_attempt = now
    db.flush()
    logger.info(
        "Provider-Anker wrote %s/%s/FY%s = %s (source=%s)",
        company.ticker, key, year, value, result.source_name,
    )
    return True


def _quarterly_provider(key: str):
    """Erster Provider in der Kette, der Quartale kann (aktuell nur EDGAR).
    Laeuft ueber get_providers, damit der conftest-Netzblock (get_providers
    -> []) auch den Quartals-Anker hermetisch haelt."""
    for provider in get_providers(key):
        if hasattr(provider, "fetch_quarterly"):
            return provider
    return None


def _quarter_cell_locked(rows) -> bool:
    """Locks: manuelle ACTUAL-Zeilen und gefuellte PDF-Zeilen sind
    authoritative. Manuelle FORECAST-Zeilen sperren nur Schaetz-Writer —
    berichtete Provider-Zahlen duerfen sie ersetzen."""
    return any(
        (r.manually_overridden and not r.is_forecast)
        or (r.from_ir_pdf and r.numeric_value is not None)
        for r in rows
    )


def _quarter_slot_rows(db, company, key: str, year: int, quarter: str):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == quarter,
            CompanyValue.period_year == year,
        )
        .all()
    )


def _anchor_one_quarter_cell(db, company, provider, key: str, year: int, quarter: str) -> bool:
    """Eine Q-Zelle mit dem Provider-Wert verankern. True wenn geschrieben."""
    # Lock-Vorpruefung vor dem Fetch — keinen Provider-Call verschwenden.
    if _quarter_cell_locked(_quarter_slot_rows(db, company, key, year, quarter)):
        return False

    fy_end_month = getattr(company, "fiscal_year_end_month", None)
    fy_end_day = getattr(company, "fiscal_year_end_day", None)
    try:
        result = provider.fetch_quarterly(
            company.ticker, key, year, quarter,
            fy_end_month=fy_end_month, fy_end_day=fy_end_day,
        )
    except Exception as e:
        logger.warning(
            "Quarter-Anker fetch failed for %s/%s/%s FY%s via %s: %s",
            company.ticker, key, quarter, year, getattr(provider, "name", provider), e,
        )
        return False
    # None = Quartal (noch) nicht gefilt — Schaetzung bleibt stehen.
    if result is None or not isinstance(result.value, Decimal):
        return False
    # EBIT-only-Approximation (kein D&A-Concept in XBRL) nicht ankern:
    # sie wuerde durch den apply_to_db-Guard unkorrigierbar und ist
    # schlechter als ein LLM-recherchiertes echtes EBITDA.
    if key == "ebitda" and result.source_name and "EBITDA ~ EBIT" in result.source_name:
        return False
    return _write_quarter_result(db, company, key, year, quarter, result)


def _write_quarter_result(db, company, key: str, year: int, quarter: str, result) -> bool:
    """Provider-Quartalsergebnis in den Slot schreiben (Schreibpfad des
    EDGAR-Quartals-Ankers). True wenn geschrieben."""
    rows = _quarter_slot_rows(db, company, key, year, quarter)
    if _quarter_cell_locked(rows):
        return False

    now = datetime.now(timezone.utc)
    # Zielzeile: actual-Slot bevorzugen, sonst forecast-Slot umziehen.
    row = next((r for r in rows if not r.is_forecast), None)
    if row is None:
        row = next(iter(rows), None)
    # Vor der Mutation festhalten: ersetzt dieser Write eine manuelle
    # Forecast-Zeile? Dann werden unten auch Manual-Adjusted-Felder geleert.
    replacing_manual_forecast = bool(
        row is not None and row.manually_overridden and row.is_forecast
    )

    if row is not None and currency_conflict(key, row.currency, result.currency):
        logger.warning(
            "Quarter-Anker currency mismatch BLOCKED %s/%s/%s FY%s: existing=%s new=%s (source=%s)",
            company.ticker, key, quarter, year, row.currency, result.currency, result.source_name,
        )
        row.last_refresh_attempt = now
        db.flush()
        return False
    if row is None and currency_conflict(
        key, getattr(company, "currency", None), result.currency
    ):
        # Neuanlage: wie im FY-Anker gegen die Company-Waehrung pruefen.
        logger.warning(
            "Quarter-Anker currency mismatch on create BLOCKED %s/%s/%s FY%s: company=%s new=%s (source=%s)",
            company.ticker, key, quarter, year,
            getattr(company, "currency", None), result.currency, result.source_name,
        )
        return False

    value = normalize_sign(
        key, result.value,
        context=f"quarter-anchor {company.ticker}/{quarter} FY{year} source={result.source_name}",
    )

    if row is None:
        row = CompanyValue(
            id=uuid4(), company_id=company.id, value_key=key,
            period_type=quarter, period_year=year, is_forecast=False,
        )
        # SAVEPOINT wie im FY-Anker: Unique-Index-Kollision (Race) -> skip.
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            logger.warning(
                "Quarter-Anker insert race %s/%s/%s FY%s: IntegrityError — skip",
                company.ticker, key, quarter, year,
            )
            return False

    # SAVEPOINT auch fuer den Update-/Forecast-Umzugs-Pfad: der Flip auf
    # is_forecast=False kann bei einem Race mit parallel entstehendem
    # actual-Slot kollidieren — ohne Nested waere die Session danach
    # vergiftet und alle Folgezellen des Laufs wuerden scheitern.
    try:
        with db.begin_nested():
            row.numeric_value = value
            row.text_value = None
            if result.currency is not None:
                row.currency = result.currency
            row.source_name = result.source_name
            row.source_link = result.source_link
            row.primary_method = "provider"
            # Unique-Index (company, key, period_type, year, is_forecast): den
            # forecast-Slot nur umziehen wenn kein actual-Slot existiert — durch
            # die Zielzeilen-Wahl oben garantiert (actual wird bevorzugt).
            row.is_forecast = False
            row.from_ir_pdf = False
            row.manually_overridden = False
            # Stale LLM-Adjusted-Werte abraeumen (siehe FY-Anker): nur
            # Manual/8-K-Enrichment (SEC-URL) sind geschuetzt. Ausnahme:
            # beim Ersetzen einer manuellen Forecast-Zeile faellt auch
            # Manual-Adjusted (Schaetz-Ableger, Enrichment fuellt neu).
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
            "Quarter-Anker slot race %s/%s/%s FY%s: IntegrityError — skip",
            company.ticker, key, quarter, year,
        )
        return False
    return True


def anchor_key_periods_with_provider(db, company, key: str, year: int) -> set[str]:
    """Provider-First-Anker fuer EINEN Key und EIN Jahr (vor der LLM-
    Recherche): FY via der bestehenden FY-Anker-Logik (Abgeschlossen-Gate
    liegt in anchor_fy_with_provider selbst), Q1-Q4 via der Quartals-
    Anker-Zelle. Alle Locks/Guards der bestehenden Anker gelten
    unveraendert.

    Rueckgabe: Menge der Perioden ("FY", "Q1", ...), die danach als
    provider-Actual mit Wert im Slot stehen — auch wenn sie schon vor dem
    Aufruf provider waren.
    """
    try:
        anchor_fy_with_provider(db, company, key, year)
    except Exception as e:
        logger.warning(
            "Key-Anker FY failed %s/%s/FY%s: %s", company.ticker, key, year, e,
        )
    if key not in QUARTER_ANCHOR_EXCLUDED_KEYS:
        provider = _quarterly_provider(key)
        if provider is not None:
            for quarter in ("Q1", "Q2", "Q3", "Q4"):
                try:
                    _anchor_one_quarter_cell(db, company, provider, key, year, quarter)
                except Exception as e:
                    logger.warning(
                        "Key-Anker cell failed %s/%s/%s FY%s: %s",
                        company.ticker, key, quarter, year, e,
                    )
    rows = (
        db.query(CompanyValue.period_type)
        .filter(
            CompanyValue.company_id == company.id,
            CompanyValue.value_key == key,
            CompanyValue.period_year == year,
            CompanyValue.period_type.in_(("FY", "Q1", "Q2", "Q3", "Q4")),
            CompanyValue.is_forecast.is_(False),
            CompanyValue.primary_method == "provider",
            CompanyValue.numeric_value.isnot(None),
        )
        .all()
    )
    return {r.period_type for r in rows}


def anchor_quarters_with_provider(db, company, years: list[int]) -> int:
    """Ueberschreibt Q1-Q4-Zellen mit exakten XBRL-Quartalswerten (EDGAR).

    Gegenstueck zu anchor_fy_with_provider: Im Volllauf schreibt die
    Two-Stage-Recherche die Quartale zuerst — dieser Pass zieht danach die
    strukturierten 10-Q/10-K-Werte drueber (primary_method='provider').
    Nicht gefilte Quartale (fetch_quarterly -> None) bleiben Schaetzungen.
    Rueckgabe: Anzahl geschriebener Zellen.
    """
    from app.calculations.lock import is_us_company
    # US-Filer-Gate: EDGAR ist die einzige Quartalsquelle — non-US skip.
    if not is_us_company(company):
        return 0

    written = 0
    from app.providers.edgar import EdgarProvider
    edgar_keys = set(EdgarProvider.supported_keys) - QUARTER_ANCHOR_EXCLUDED_KEYS
    for key in sorted(edgar_keys):
        provider = _quarterly_provider(key)
        if provider is None:
            continue
        for year in years:
            for quarter in ("Q1", "Q2", "Q3", "Q4"):
                try:
                    if _anchor_one_quarter_cell(db, company, provider, key, year, quarter):
                        written += 1
                except Exception as e:
                    logger.warning(
                        "Quarter-Anker cell failed %s/%s/%s FY%s: %s",
                        company.ticker, key, quarter, year, e,
                    )
    logger.info(
        "%s quarter anchor: %d Zellen aus EDGAR (Jahre %s)",
        company.ticker, written, years,
    )
    return written


# --- Nicht-US-Referenzen (Yahoo, nur Plausibilitaets-Gate) ------------------


def _market_quarterly_provider(key: str):
    """Markt-Provider (Yahoo) mit Quartals-Fundamentals in der Kette.
    Laeuft ueber get_providers, damit der conftest-Netzblock
    (get_providers -> []) auch den Referenz-Fetch hermetisch haelt."""
    for provider in get_providers(key):
        if getattr(provider, "provider_kind", None) == "market" and hasattr(
            provider, "fetch_quarterly"
        ):
            return provider
    return None


def _quarter_is_reported(company, year: int, quarter: str, today: date | None = None) -> bool:
    """Karenz-Kriterium wie consistency._quarter_reported (Nicht-US-Zweig):
    Quartalsende plus REPORTING_GRACE_DAYS abgelaufen."""
    from app.values.detail_page import REPORTING_GRACE_DAYS, quarter_end_date

    if today is None:
        today = date.today()
    q_end = quarter_end_date(
        year, quarter,
        getattr(company, "fiscal_year_end_month", None),
        getattr(company, "fiscal_year_end_day", None),
    )
    return q_end is not None and (today - q_end).days >= REPORTING_GRACE_DAYS


def yahoo_reference_map(
    company, years: list[int],
) -> dict[tuple[str, str, int], Decimal]:
    """Referenzwerte aus dem Marktdaten-Feed (Yahoo) — reiner Fetch, KEINE
    DB-Writes. Kundenentscheid: der Feed ist fuer Nicht-US-Firmen keine
    Wertequelle mehr, nur noch stilles Plausibilitaets-Gate der
    Statement-Recherche (Yahoo-Cross-Check in statement_research).

    Rueckgabe: {(value_key, period_type, year): Decimal} fuer die
    STATEMENT_RESEARCH_KEYS. FY-Referenzen kommen aus dem bisherigen
    FY-Anker-Datenpfad (provider.fetch, Minimal-Signatur), Quartale aus
    fetch_quarterly — nur berichtete Quartale (Karenz abgelaufen), Cache-/
    Netzverhalten wie der fruehere Yahoo-Quartals-Anker. Einzelne
    Fetch-Fehler werden geloggt und uebersprungen; ohne Markt-Provider
    in der Kette (conftest-Netzblock) bleibt die Map leer.
    """
    from app.values.statement_research import STATEMENT_RESEARCH_KEYS

    fy_end_month = getattr(company, "fiscal_year_end_month", None)
    fy_end_day = getattr(company, "fiscal_year_end_day", None)
    refs: dict[tuple[str, str, int], Decimal] = {}
    for key in sorted(STATEMENT_RESEARCH_KEYS):
        provider = _market_quarterly_provider(key)
        if provider is None:
            continue
        for year in years:
            if hasattr(provider, "fetch"):
                try:
                    result = provider.fetch(company.ticker, key, "FY", year)
                except Exception as e:
                    logger.warning(
                        "Yahoo-Referenz fetch failed %s/%s/FY%s: %s",
                        company.ticker, key, year, e,
                    )
                    result = None
                if result is not None and isinstance(result.value, Decimal):
                    refs[(key, "FY", year)] = result.value
            for quarter in ("Q1", "Q2", "Q3", "Q4"):
                if not _quarter_is_reported(company, year, quarter):
                    continue
                try:
                    result = provider.fetch_quarterly(
                        company.ticker, key, year, quarter,
                        fy_end_month=fy_end_month, fy_end_day=fy_end_day,
                    )
                except Exception as e:
                    logger.warning(
                        "Yahoo-Referenz fetch failed %s/%s/%s FY%s: %s",
                        company.ticker, key, quarter, year, e,
                    )
                    continue
                if result is not None and isinstance(result.value, Decimal):
                    refs[(key, quarter, year)] = result.value
    return refs
