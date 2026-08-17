"""Company-Values API: Werte-Refresh + Manual-Override + Calculations.

Der Werte-Refresh laeuft komplett ueber den ValueOrchestrator (US-only):

  STAMMDATEN (immer Live-Snapshot)
    - stock_price / market_cap / shares_outstanding -> Yahoo-Feed
    - market_cap_calc = stock_price * shares (in calculation_engine)

  FUNDAMENTALS (abgeschlossene + laufendes FY)
    ValueOrchestrator.run pro Zelle mit fester Prioritaet
    Manual > provider(EDGAR) > perplexity:
      1. EDGAR-XBRL-Anker (direkt, 10-K/10-Q) fuer abgeschlossene Perioden
      2. Perplexity fuellt Luecken bzw. bildet Konsens (laufendes FY,
         nicht von EDGAR gedeckte Keys)

  CALCULATED FELDER (FCF-Yield, EV/EBITDA, Hohn-Return, ...)
    calculation_engine (engine.py) nach dem Werte-Refresh.

  Stammdaten-Only-Modus ("Daily Numbers"): nur der Live-Feed-Snapshot
  + Neuberechnung, kein EDGAR/Perplexity.
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.auth.deps import current_user
from app.auth.models import User
from app.calculations.engine import (
    CALCULATED_KEYS,
    FY_CALC_KEYS,
    HOHN_KEYS,
    STAMMDATEN_CALC_KEYS,
    calculate_cumulative,
    calculate_fy,
    calculate_stammdaten,
)
from app.calculations.lock import (
    annual_report_years,
    is_hohn_locked,
    is_us_company,
    quarter_years,
    quarter_years_in_progress,
)
from app.companies.models import Company
from app.db import get_db
from app.portfolios.models import Portfolio, PortfolioMember
from app.providers.registry import get_providers
from app.values.always_current import ALWAYS_CURRENT_KEYS
from app.values.currency_keys import CURRENCY_KEYS
from app.values.models import CompanyValue, ValueDefinition
from app.values.progress import cleanup_old_jobs, finish_job, get_job, set_phase, start_job, update_job
from app.values.schemas import (
    CompanyValueOut,
    OverrideRequest,
    RefreshRequest,
    ValueDefinitionOut,
)

catalog_router = APIRouter(prefix="/api/value-definitions", tags=["values"])
values_router = APIRouter(prefix="/api/companies", tags=["values"])


def _load_value_map(
    db: Session,
    company_id: UUID,
    period_type: str,
    period_year: int | None,
) -> tuple[list[CompanyValue], dict[str, Decimal | None]]:
    q = db.query(CompanyValue).filter(
        CompanyValue.company_id == company_id,
        CompanyValue.period_type == period_type,
    )
    if period_year is not None:
        q = q.filter(CompanyValue.period_year == period_year)
    else:
        q = q.filter(CompanyValue.period_year.is_(None))
    rows = q.all()
    values = {row.value_key: row.numeric_value for row in rows if row.numeric_value is not None}
    return rows, values


def _load_adjusted_map(rows: list[CompanyValue]) -> dict[str, Decimal | None]:
    """Adjusted-Werte aus einem CompanyValue-Set. Nur fuer NI/EBITDA/FCF
    relevant; andere Keys haben numeric_value_adjusted=NULL und sind in der
    Map nicht enthalten."""
    return {
        row.value_key: row.numeric_value_adjusted
        for row in rows
        if row.numeric_value_adjusted is not None
    }


def _persist_calc_results(
    db: Session,
    company_id: UUID,
    period_type: str,
    period_year: int | None,
    existing_rows: list[CompanyValue],
    calc_results: dict[str, Decimal | None],
    allowed_keys: set[str],
    company_currency: str | None,
    source_name_override: str | None = None,
    is_forecast_override: bool | None = None,
    calc_results_adjusted: dict[str, Decimal | None] | None = None,
) -> list[CompanyValue]:
    by_key = {row.value_key: row for row in existing_rows}
    updated: list[CompanyValue] = []
    default_source = source_name_override or "Calculated"
    adj_map = calc_results_adjusted or {}

    for key, value in calc_results.items():
        if key not in allowed_keys:
            continue

        existing = by_key.get(key)
        adj_value = adj_map.get(key)
        if value is None and adj_value is None and existing is None:
            continue

        calc_currency = company_currency if key in CURRENCY_KEYS else None

        if existing:
            if existing.manually_overridden:
                # Ein Calculated-Key kann via API nicht manuell ueberschrieben
                # werden (override_company_value blockt das) — ein gesetztes
                # Flag ist ein Legacy-Artefakt. Wuerde es respektiert, bliebe
                # der stale Wert fuer immer stehen und verfaelschte die
                # H-Return. Daher: Formelwert schreiben, Flag zuruecksetzen.
                logger.info(
                    "Stale manual lock on calculated key %s/%s %s%s — overwriting with formula value",
                    company_id, key, period_type, period_year or "",
                )
                existing.manually_overridden = False
            existing.numeric_value = value
            existing.numeric_value_adjusted = adj_value
            existing.source_name = default_source
            existing.source_link = None
            existing.fetched_at = datetime.now(timezone.utc)
            existing.from_ir_pdf = False
            if is_forecast_override is not None:
                existing.is_forecast = is_forecast_override
            if calc_currency and not existing.currency:
                existing.currency = calc_currency
            updated.append(existing)
        else:
            cv = CompanyValue(
                id=uuid4(),
                company_id=company_id,
                value_key=key,
                period_type=period_type,
                period_year=period_year,
                numeric_value=value,
                numeric_value_adjusted=adj_value,
                source_name=default_source,
                source_link=None,
                fetched_at=datetime.now(timezone.utc),
                currency=calc_currency,
                is_forecast=bool(is_forecast_override) if is_forecast_override is not None else False,
            )
            db.add(cv)
            updated.append(cv)
    return updated


def _run_and_persist_calculations(
    db: Session,
    company_id: UUID,
    period_type: str,
    period_year: int | None,
) -> list[CompanyValue]:
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    company_currency = company.currency if company else None

    snapshot_rows, stammdaten = _load_value_map(db, company_id, "SNAPSHOT", None)

    stammdaten_calc = calculate_stammdaten(stammdaten)
    # Stale-Calc-Override: berechnete Stammdaten-Keys (market_cap_calc) IMMER
    # ueberschreiben — auch wenn der alte Wert noch in stammdaten steht. Sonst
    # nutzt die FY-Calculation einen stale market_cap_calc nach z.B. Shares-
    # Outstanding-Korrektur (Dual-Class-Fix).
    for key, val in stammdaten_calc.items():
        if val is not None:
            stammdaten[key] = val

    updated: list[CompanyValue] = []
    updated += _persist_calc_results(
        db, company_id, "SNAPSHOT", None,
        snapshot_rows, stammdaten_calc, STAMMDATEN_CALC_KEYS, company_currency,
    )

    if period_type == "FY" and period_year is not None:
        hohn_locked = company is not None and is_hohn_locked(db, company, period_year)
        if hohn_locked:
            db.query(CompanyValue).filter(
                CompanyValue.company_id == company_id,
                CompanyValue.period_type == "FY",
                CompanyValue.period_year == period_year,
                CompanyValue.value_key.in_(HOHN_KEYS),
            ).delete(synchronize_session=False)
            db.flush()

        current_rows, current = _load_value_map(db, company_id, "FY", period_year)
        _prev_rows, previous = _load_value_map(db, company_id, "FY", period_year - 1)

        if current.get("stock_price") is not None and current.get("shares_outstanding") is not None:
            fy_stammdaten_calc = calculate_stammdaten(current)
            updated += _persist_calc_results(
                db, company_id, "FY", period_year,
                current_rows, fy_stammdaten_calc, STAMMDATEN_CALC_KEYS, company_currency,
            )
            current_rows, current = _load_value_map(db, company_id, "FY", period_year)

        # End-of-FY market cap = start-of-FY+1 market cap (our anchor convention).
        # Used to compute realised total shareholder return (`actual_return`).
        # If the FY+1 anchor is missing AND that FY-end is in the past, try
        # to fetch it on demand so actual_return doesn't show "Inputs fehlen"
        # for an already-completed FY.
        _next_rows, next_year = _load_value_map(db, company_id, "FY", period_year + 1)
        next_mcap = next_year.get("market_cap")
        if next_mcap is None:
            from datetime import date as _date_today
            company = db.query(Company).filter(Company.id == company_id).one_or_none()
            fy_end_in_past = False
            if company is not None and company.fiscal_year_end_month and company.fiscal_year_end_day:
                try:
                    fy_end = _date_today(period_year, company.fiscal_year_end_month, company.fiscal_year_end_day)
                    fy_end_in_past = fy_end <= _date_today.today()
                except ValueError:
                    fy_end_in_past = False
            if fy_end_in_past and company is not None:
                try:
                    _fetch_and_store_historical_mcap(db, company.ticker, company_id, period_year + 1)
                    db.flush()
                    _next_rows, next_year = _load_value_map(db, company_id, "FY", period_year + 1)
                    next_mcap = next_year.get("market_cap")
                except Exception as e:
                    logger.warning("Auto-fetch FY+1 anchor for actual_return failed %s/%s: %s",
                                   company_id, period_year + 1, e)
        current_adj = _load_adjusted_map(current_rows)
        previous_adj = _load_adjusted_map(_prev_rows)

        # Forecast-Year-Erkennung: wenn fuer dieses FY die Kern-Inputs als
        # Forecast in DB stehen (z.B. net_income aus Web-Guidance), nutzt die
        # Engine den aktuellen Stammdaten-Snapshot fuer MCap.
        # Trailing-Bewertung (FY[N-1]-Multiples im Estimate-Mode) entfernt —
        # User-Anforderung: auf der Estimates-Seite immer die aktuellen
        # Stammdaten (Live-Snapshot) nutzen, nicht historische FY-1-Werte.
        # → PE/EV-EBITDA/FCF-Yield sind jetzt Forward-Multiples mit Live-MCap.
        _fc_keys = ("net_income", "ebitda", "fcf", "net_debt", "market_cap")
        _has_actual = {
            k: any(r.value_key == k and not r.is_forecast and r.numeric_value is not None for r in current_rows)
            for k in _fc_keys
        }
        is_forecast_year = any(
            r.is_forecast and r.value_key in _fc_keys and not _has_actual[r.value_key]
            for r in current_rows
        )

        from app.calculations.engine import is_financial
        fy_calc, fy_calc_adj = calculate_fy(
            current, previous, stammdaten,
            next_year_market_cap=next_mcap,
            current_adjusted=current_adj,
            previous_adjusted=previous_adj,
            is_running_fy=is_forecast_year,
            exclude_net_debt_change=is_financial(company.ticker if company else None),
        )

        allowed_fy_keys = FY_CALC_KEYS - HOHN_KEYS if hohn_locked else FY_CALC_KEYS

        updated += _persist_calc_results(
            db, company_id, "FY", period_year,
            current_rows, fy_calc, allowed_fy_keys, company_currency,
            calc_results_adjusted=fy_calc_adj,
        )

    return updated


def run_and_persist_calculations_for_years(db: Session, company: Company, years: list[int]) -> None:
    """Leitet die CALCULATED-Werte (H-Rendite, Multiples, Margen) pro FY ab,
    indem der bestehende _run_and_persist_calculations-Pfad je Jahr aufgerufen
    wird. Ein einzelnes Jahr mit unvollstaendigen Inputs darf den Refresh nicht
    abbrechen; schlagen aber ALLE Jahre fehl, deutet das auf einen systemischen
    Fehler hin und wird nicht still geschluckt. Formeln bleiben in engine.py."""
    failures = 0
    last_exc = None
    for year in years:
        try:
            _run_and_persist_calculations(db, company.id, "FY", year)
            # Nach JEDEM Jahr flushen: _run_and_persist_calculations schreibt bei
            # jedem Aufruf auch die SNAPSHOT-Stammdaten-Calc (market_cap_calc,
            # period_year=None). Ohne Flush sieht die Existenzpruefung des
            # naechsten Jahres (autoflush aus) die pending SNAPSHOT-Zeile nicht
            # -> zweite INSERT -> UniqueViolation. Flush macht daraus ein Update.
            db.flush()
        except Exception as e:  # noqa: BLE001 - pro Jahr isolieren, s. u.
            failures += 1
            last_exc = e
            logger.warning("calc derive FY%s for company %s failed: %s", year, company.id, e)
    if years and failures == len(years) and last_exc is not None:
        raise last_exc


def _get_owned_company(db: Session, user: User, company_id: UUID) -> Company:
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    portfolio = db.query(Portfolio).filter(Portfolio.id == company.portfolio_id).one_or_none()
    from app.portfolios.models import has_portfolio_access
    if not portfolio or not has_portfolio_access(db, user.id, portfolio):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company


@catalog_router.get("", response_model=list[ValueDefinitionOut])
def list_value_definitions(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[ValueDefinition]:
    return (
        db.query(ValueDefinition)
        .order_by(ValueDefinition.sort_order)
        .all()
    )


@values_router.get("/{company_id}/values", response_model=list[CompanyValueOut])
def list_company_values(
    company_id: UUID,
    period_type: str = Query(default="SNAPSHOT"),
    period_year: int | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[CompanyValue]:
    _get_owned_company(db, user, company_id)
    q = db.query(CompanyValue).filter(
        CompanyValue.company_id == company_id,
        CompanyValue.period_type == period_type,
    )
    if period_year is not None:
        q = q.filter(CompanyValue.period_year == period_year)
    return q.all()


@values_router.get("/{company_id}/values/{value_key}/quarterly", response_model=list[CompanyValueOut])
def get_quarterly_breakdown(
    company_id: UUID,
    value_key: str,
    period_year: int = Query(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[CompanyValue]:
    """Q1/Q2/Q3/Q4 Rows fuer einen Key + FY — fuer das Drilldown-Modal mit
    Quartals-Tabelle. Bei Duplikaten (PDF-Actual + frueheres Web-Fallback)
    wird die Actual-Row bevorzugt (is_forecast=False sortiert zuerst)."""
    _get_owned_company(db, user, company_id)
    rows = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == value_key,
            CompanyValue.period_type.in_(("Q1", "Q2", "Q3", "Q4")),
            CompanyValue.period_year == period_year,
        )
        .order_by(CompanyValue.period_type, CompanyValue.is_forecast.asc())
        .all()
    )
    seen: dict[str, CompanyValue] = {}
    for r in rows:
        if r.period_type not in seen:
            seen[r.period_type] = r
    return list(seen.values())


def _upsert_fy_value(
    db: Session,
    company_id: UUID,
    value_key: str,
    period_year: int,
    value: Decimal,
    source_name: str,
    source_link: str | None,
    currency: str | None,
) -> None:
    existing = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == value_key,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == period_year,
        )
        .one_or_none()
    )
    now = datetime.now(timezone.utc)
    if existing:
        if existing.manually_overridden:
            return
        if existing.from_ir_pdf and existing.numeric_value is not None:
            return  # PDF value wins over Yahoo historical helper
        existing.numeric_value = value
        existing.source_name = source_name
        existing.source_link = source_link
        existing.currency = currency
        existing.fetched_at = now
        existing.from_ir_pdf = False
    else:
        db.add(CompanyValue(
            id=uuid4(),
            company_id=company_id,
            value_key=value_key,
            period_type="FY",
            period_year=period_year,
            numeric_value=value,
            source_name=source_name,
            source_link=source_link,
            currency=currency,
            fetched_at=now,
        ))


def _ensure_company_fy_end(db: Session, company: Company) -> tuple[int, int]:
    """Return (fy_end_month, fy_end_day). If missing on company, auto-detect via
    Yahoo and persist. Falls back to (12, 31) if detection fails."""
    if company.fiscal_year_end_month and company.fiscal_year_end_day:
        return (company.fiscal_year_end_month, company.fiscal_year_end_day)
    providers = get_providers("market_cap")
    provider = next((p for p in providers if hasattr(p, "detect_fiscal_year_end")), None)
    if provider is None:
        return (12, 31)
    detected = None
    try:
        detected = provider.detect_fiscal_year_end(company.ticker)
    except Exception as e:
        logger.warning("FY-end detect failed for %s: %s", company.ticker, e)
    if (
        detected is None
        or not isinstance(detected, tuple)
        or len(detected) != 2
        or not all(isinstance(v, int) for v in detected)
        or not (1 <= detected[0] <= 12 and 1 <= detected[1] <= 31)
    ):
        return (12, 31)
    company.fiscal_year_end_month = detected[0]
    company.fiscal_year_end_day = detected[1]
    db.flush()
    return detected


def _fetch_and_store_historical_mcap(
    db: Session,
    ticker: str,
    company_id: UUID,
    period_year: int,
) -> None:
    """Fetch + store stammdaten for the START of FY `period_year`, i.e. anchored
    to the FY-end of (period_year-1). For a Dec-FY company this is 31.12.(N-1);
    for a Jun-FY company (e.g. Microsoft) it's 30.06.(N-1). The resulting
    market_cap, stock_price, shares_outstanding represent what the investor saw
    when entering the fiscal year. Stored as period_type=FY, period_year=N.
    Best-effort — failures logged."""
    providers = get_providers("market_cap")
    provider = next((p for p in providers if hasattr(p, "fetch_historical_market_cap")), None)
    if provider is None:
        return
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if company is None:
        return
    fy_month, fy_day = _ensure_company_fy_end(db, company)
    anchor_year = period_year - 1
    # Anker-Datum in der Zukunft (z.B. Folgejahres-Anker eines laufenden FY,
    # dessen Ende noch nicht erreicht ist) -> es gibt keinen Schlusskurs; nicht
    # fetchen, sonst greift der Feed den letzten verfuegbaren Kurs und legt einen
    # fabrizierten "FY-Ende"-Wert ab.
    from datetime import date as _d
    try:
        _anchor_date = _d(anchor_year, fy_month, fy_day)
    except ValueError:
        _anchor_date = _d(anchor_year, 12, 31)
    if _anchor_date > _d.today():
        return
    try:
        result = provider.fetch_historical_market_cap(ticker, anchor_year, fy_month, fy_day)
    except Exception as e:
        logger.warning("Historical MCap fetch failed %s/FY%s (anchor %s): %s", ticker, period_year, anchor_year, e)
        return
    if result is None or not isinstance(result.value, Decimal):
        return

    # Marktdaten-Close = last trading day on-or-before (anchor_year, fy_month, fy_day).
    # Konvention: Stammdaten der FY-Row[N] = Snapshot am letzten Tag von FY[N-1]
    # ("FY-Ende N-1"). Wirtschaftlich = Anfang FY[N] (gleicher Trading-Tag).
    # Label-Format einheitlich "FY-Ende {N-1} = DD.MM.YYYY" — damit alle 4
    # Stammdaten-Felder (mcap, stock, shares, mcap_calc) identisches Datum-Tag im UI zeigen.
    from datetime import date as _date
    try:
        fy_end_label = _date(anchor_year, fy_month, fy_day).strftime("%d.%m.%Y")
    except ValueError:
        fy_end_label = f"31.12.{anchor_year}"
    extras = getattr(result, "extras", None) or {}
    stock_price = extras.get("stock_price") if isinstance(extras, dict) else None
    shares = extras.get("shares_outstanding") if isinstance(extras, dict) else None
    shares_source = extras.get("shares_source") if isinstance(extras, dict) else None
    shares_label = shares_source or "current Shares"
    anchor_note = f"FY-Ende {anchor_year} = {fy_end_label}"

    _upsert_fy_value(db, company_id, "market_cap", period_year, result.value,
                     f"Marktdaten-Feed (Close {anchor_note} × {shares_label})", None, result.currency)
    if isinstance(stock_price, Decimal):
        _upsert_fy_value(db, company_id, "stock_price", period_year, stock_price,
                         f"Marktdaten-Feed (Adj Close {anchor_note})", None, result.currency)
    if isinstance(shares, Decimal):
        _upsert_fy_value(db, company_id, "shares_outstanding", period_year, shares,
                         f"Marktdaten-Feed ({shares_label}, {anchor_note})", None, None)


def _has_fy_price_anchor(db: Session, company_id: UUID, period_year: int) -> bool:
    """True wenn die FY-Zeile bereits einen Preis-Anker (market_cap)
    traegt — Guard gegen Doppel-Fetches, gleiches Kriterium wie der
    next_mcap-Guard in _run_and_persist_calculations."""
    return (
        db.query(CompanyValue.id)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == "market_cap",
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == period_year,
            CompanyValue.numeric_value.isnot(None),
        )
        .first()
        is not None
    )


def _maybe_refresh_next_earnings(db: Session, company: Company, ticker: str) -> None:
    """Naechsten Earnings-Termin hoechstens alle 24h neu holen.

    Laeuft im Stammdaten-Only-Pfad (Daily-Refresh-Button): ist
    earnings_checked_at juenger als 24h, passiert nichts. Sonst Yahoo
    fragen und Ergebnis (auch None) + Zeitstempel persistieren. Fehler
    werden nur geloggt — der Daily-Refresh darf daran nie scheitern."""
    now = datetime.now(timezone.utc)
    checked = company.earnings_checked_at
    if checked is not None:
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        if now - checked < timedelta(hours=24):
            return
    try:
        providers = get_providers("stock_price")
        provider = next(
            (p for p in providers if hasattr(p, "fetch_next_earnings_date")), None
        )
        if provider is None:
            return
        # Provider raist bei Ausfall — dann NICHT persistieren und NICHT
        # stempeln, damit ein bekannter Termin erhalten bleibt und der
        # naechste Refresh es erneut versucht. Ein echtes None ("kein
        # Termin bekannt") wird dagegen gespeichert.
        fetched = provider.fetch_next_earnings_date(ticker)
        company.next_earnings_date = fetched
        company.earnings_checked_at = now
        db.flush()
    except Exception as e:
        db.rollback()
        logger.warning("Next-earnings refresh failed for %s: %s", ticker, e)


@values_router.get("/{company_id}/refresh-status")
def get_refresh_status(
    company_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    _get_owned_company(db, user, company_id)
    job = get_job(company_id)
    if not job:
        return {"status": "idle"}
    return job


@values_router.post("/{company_id}/values/refresh", response_model=list[CompanyValueOut])
def refresh_company_values(
    company_id: UUID,
    payload: RefreshRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[CompanyValue]:
    cleanup_old_jobs()
    company = _get_owned_company(db, user, company_id)
    ticker = company.ticker
    if company.isin:
        providers = get_providers("stock_price")
        if providers and hasattr(providers[0], "resolve_ticker_from_isin"):
            resolved = providers[0].resolve_ticker_from_isin(company.isin)
            if resolved and resolved != ticker:
                ticker = resolved
                company.ticker = ticker
                db.flush()
    # Werte-Beschaffung laeuft komplett ueber den ValueOrchestrator:
    # Stammdaten (Feed) -> EDGAR-Anker (direkt, KEINE Provider-Chain) ->
    # Perplexity (Luecken/Konsens) -> engine.py-Ableitung. Prioritaet pro
    # Zelle: Manual > provider(EDGAR) > perplexity. Imports lazy, um jede
    # Modul-Ladereihenfolge-/Zyklus-Frage zu vermeiden.
    from app.config import settings
    from app.llm.perplexity import PerplexityClient
    from app.values.adapters import edgar_anchor, yahoo_stammdaten
    from app.values.orchestrator import ValueOrchestrator

    def _company_value_rows() -> list[CompanyValue]:
        rows = (
            db.query(CompanyValue)
            .filter(CompanyValue.company_id == company_id)
            .all()
        )
        for cv in rows:
            db.refresh(cv)
        return rows

    client = (
        PerplexityClient(
            api_key=settings.perplexity_api_key,
            model=settings.perplexity_model,
            base_url=settings.perplexity_base_url,
        )
        if settings.perplexity_api_key else None
    )
    def _progress(phase: str, label: str) -> None:
        set_phase(company_id, phase, label)
        update_job(company_id, label)

    orch = ValueOrchestrator(
        db=db, stammdaten_fetch=yahoo_stammdaten,
        edgar_fetch=edgar_anchor, perplexity=client, on_phase=_progress,
    )

    # Stammdaten-Only-Modus ("Daily Numbers"-Button): nur die Live-API-
    # Stammdaten-Keys refreshen (Market Cap, Stock Price, Shares) + naechsten
    # Earnings-Termin + Neuberechnung. Kein EDGAR/Perplexity, kein Historik-Fetch.
    if payload.stammdaten_only:
        start_job(company_id, 1)
        try:
            set_phase(company_id, "stammdaten", "Live-Stammdaten aktualisieren")
            orch.run_stammdaten_only(company)
            _maybe_refresh_next_earnings(db, company, ticker)
            db.commit()
        except Exception:
            db.rollback()
            finish_job(company_id, status="failed")
            raise
        else:
            finish_job(company_id)
        return _company_value_rows()

    # Full-Modus: Geschaeftsjahr-Ende sicherstellen (running_fy_year/
    # target_years haengen daran) -> historische Preis-Anker -> orch.run.
    # 8 Schritte: FY-Ende, Historik-Anker + 6 Orchestrator-Phasen (Stammdaten,
    # EDGAR, Quartale, Perplexity, Ableitungen, Berechnung).
    start_job(company_id, 9)
    try:
        set_phase(company_id, "fiscal_year_end", "Geschaeftsjahr-Ende ermitteln")
        update_job(company_id, "Geschaeftsjahr-Ende")
        _ensure_company_fy_end(db, company)
        db.flush()

        # Historische Preis-Anker VOR orch.run: der Derive-Schritt am Ende von
        # run() braucht market_cap je FY fuer die Yields-Denominatoren und
        # actual_return (Einstiegs-Anker Close FY-Ende N-1, Folgejahres-Anker
        # N fuer actual_return). Vorhandene Anker werden nicht erneut geholt.
        set_phase(company_id, "historical_mcap", "Historische Preis-Anker")
        update_job(company_id, "Historische Preis-Anker")
        # Jedes Zieljahr braucht seinen Einstiegs-Anker (Close FY-Ende N-1) und
        # den Folgejahres-Anker N+1 (actual_return). Anker-Jahre ueberschneiden
        # sich (y+1 des einen = y des naechsten) -> DEDUP als Set, sonst wird
        # dasselbe FY-market_cap zweimal eingefuegt (der _has_fy_price_anchor-
        # Guard sieht die im selben Lauf noch nicht geflushte Zeile nicht) und
        # der Unique-Index uq_company_values_slot schlaegt zu.
        target = orch.target_years(company)
        anchor_years = sorted(set(target) | {y + 1 for y in target})
        for anchor_year in anchor_years:
            if _has_fy_price_anchor(db, company_id, anchor_year):
                continue
            try:
                _fetch_and_store_historical_mcap(db, ticker, company_id, anchor_year)
            except Exception as e:
                logger.warning(
                    "historical mcap FY%s failed for %s: %s",
                    anchor_year, ticker, e,
                )
                continue
        db.flush()

        # orch.run emittiert selbst die Phasen stammdaten/edgar/perplexity/
        # calculating via _progress-Callback (echte fortschreitende Schritte).
        # orch.run macht intern: Stammdaten -> EDGAR-FY -> EDGAR-Quartale ->
        # Perplexity (Q4-Schaetzung/Gap-Fill) -> Finalize (FY-aus-Quartalen,
        # Q4-Residual, net_debt) -> Kennzahlen.
        orch.run(company)

        # Naechster Earnings-Termin (die Batch-Stale-Auswahl haengt daran).
        _maybe_refresh_next_earnings(db, company, ticker)
        db.commit()
    except Exception:
        db.rollback()
        finish_job(company_id, status="failed")
        raise
    else:
        finish_job(company_id)
    return _company_value_rows()


@values_router.post("/{company_id}/values/calculate", response_model=list[CompanyValueOut])
def calculate_company_values(
    company_id: UUID,
    period_type: str = Query(default="SNAPSHOT"),
    period_year: int | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[CompanyValue]:
    _get_owned_company(db, user, company_id)
    calc_updated = _run_and_persist_calculations(db, company_id, period_type, period_year)
    db.commit()
    for cv in calc_updated:
        db.refresh(cv)
    return calc_updated


@values_router.post("/{company_id}/values/{value_key}/explain")
def explain_company_value(
    company_id: UUID,
    value_key: str,
    period_type: str = Query(default="FY"),
    period_year: int | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Claude-Einordnung zu einem konkreten Finanzwert: ist der Wert normal/
    auffaellig? Falls auffaellig, welche Gruende? Was bedeutet er für die
    Capital-Allocation-Story?
    Wird NICHT persistiert — nur on-demand für User-Drilldown."""
    from app.config import settings
    from app.llm.claude import get_client, claude_limiter, _collect_text, WEB_SEARCH_TOOL

    company = _get_owned_company(db, user, company_id)
    if not settings.anthropic_api_key:
        raise HTTPException(503, "Claude-API nicht konfiguriert")
    if value_key in CALCULATED_KEYS:
        raise HTTPException(400, "Berechnete Werte (Formeln) brauchen keine Claude-Einordnung")
    if period_type != "FY" or period_year is None:
        raise HTTPException(400, "Einordnung nur für FY-Werte mit Jahresangabe")

    # Lookup ohne is_forecast-Filter — sowohl actuals (abgeschlossenes FY)
    # als auch Forecasts (Estimate-Mode FY[N]e) werden eingeordnet.
    cv = (db.query(CompanyValue).filter(
        CompanyValue.company_id == company_id,
        CompanyValue.value_key == value_key,
        CompanyValue.period_type == "FY",
        CompanyValue.period_year == period_year,
    ).order_by(CompanyValue.is_forecast.asc()).first())  # Actuals bevorzugt wenn beide existieren
    if cv is None or cv.numeric_value is None:
        raise HTTPException(404, "Wert nicht vorhanden")
    vd = db.query(ValueDefinition).filter(ValueDefinition.key == value_key).one_or_none()
    if vd is None:
        raise HTTPException(404, "Unbekannter Wert")

    is_forecast_value = bool(cv.is_forecast)

    # Historische Werte: für Forecast-Einordnung wollen wir auch FY[N-1] sehen
    # (das ist der Anker), bei Actuals nur frühere abgeschlossene Jahre.
    historical = (db.query(CompanyValue).filter(
        CompanyValue.company_id == company_id,
        CompanyValue.value_key == value_key,
        CompanyValue.period_type == "FY",
        CompanyValue.period_year < period_year,
        CompanyValue.is_forecast.is_(False),
        CompanyValue.numeric_value.isnot(None),
    ).order_by(CompanyValue.period_year.desc()).limit(5).all())

    history_lines = "\n".join(
        f"  FY{r.period_year}: {float(r.numeric_value):,.0f} {r.currency or ''}"
        for r in reversed(historical)
    ) or "  (keine historischen Werte verfügbar)"

    cur = cv.currency or company.currency or ""
    if is_forecast_value:
        # Forecast-Einordnung: Plausibilitaet vs Konsens, Risiken/Treiber.
        source_block = f"\nDatenquelle Forecast: {cv.source_name or '(unbekannt)'}"
        prompt = (
            f"Du bist Senior Equity Analyst. Schreibe eine kurze Einordnung (3-5 Sätze) "
            f"zu folgender Forecast-Schaetzung:\n\n"
            f"Unternehmen: {company.name} ({company.ticker})\n"
            f"Kennzahl: {vd.label_de} / {vd.label_en} ({value_key})\n"
            f"Zeitraum: FY{period_year}e (Forecast)\n"
            f"Geschaetzter Wert: {float(cv.numeric_value):,.0f} {cur}{source_block}\n\n"
            f"Historische Ist-Werte (Vergleich):\n{history_lines}\n\n"
            f"Beantworte konkret:\n"
            f"1. Ist die FY{period_year}e-Schaetzung plausibel im Vergleich zum "
            f"historischen Trend und zum Sell-Side-Konsens (sofern bekannt)?\n"
            f"2. Welche TREIBER stuetzen den Wert (Management-Guidance, Markt-Konsens, "
            f"sektorspezifische Trends, M&A, Programme)?\n"
            f"3. Welche RISIKEN koennten den Wert nach unten drehen (Konjunktur, "
            f"Margen-Druck, FX, Regulatorik, Capital-Allocation-Aenderungen)?\n\n"
            f"Nutze web_search aktiv um aktuelle Konsens-Schaetzungen, IR-Guidance, "
            f"Earnings-Call-Transcripts und News abzugleichen. "
            f"Praezise und kurz, keine Floskeln. Auf Deutsch."
        )
    else:
        prompt = (
            f"Du bist Senior Equity Analyst. Schreibe eine kurze Einordnung (3-5 Sätze) "
            f"zu folgendem Finanzwert:\n\n"
            f"Unternehmen: {company.name} ({company.ticker})\n"
            f"Kennzahl: {vd.label_de} / {vd.label_en} ({value_key})\n"
            f"Zeitraum: FY{period_year}\n"
            f"Wert: {float(cv.numeric_value):,.0f} {cur}\n\n"
            f"Historische Werte (Vergleich):\n{history_lines}\n\n"
            f"Beantworte konkret:\n"
            f"1. Ist der FY{period_year}-Wert normal/erwartbar im historischen Trend?\n"
            f"2. Falls auffaellig (z.B. starker Anstieg/Rueckgang): welche Gruende? "
            f"(Akquisition, One-Time-Charge, Marktveränderung, Sonderausschüttung, "
            f"Restructuring, neuer Bilanzposten etc.)\n"
            f"3. Was bedeutet der Wert für die langfristige Capital-Allocation-Story "
            f"des Unternehmens?\n\n"
            f"Nutze web_search wenn noetig für aktuelle Begründungen / News-Kontext. "
            f"Praezise und kurz, keine Floskeln. Auf Deutsch."
        )
    try:
        client = get_client()
        response = claude_limiter.call(lambda: client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            tools=[WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": prompt}],
        ))
        # stop_reason loggen: max_tokens deutet auf zu kurzes Budget, end_turn
        # ist normal, tool_use waere unerwartet (wir akzeptieren nur Text).
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "max_tokens":
            logger.warning(
                "Explain-call %s/%s/%s wurde bei max_tokens=4096 abgeschnitten — "
                "Antwort unvollstaendig. Erwaege weitere Erhoehung oder kuerzere Prompt-Struktur.",
                company.ticker, value_key, period_year,
            )
        text = _collect_text(response)
        return {"explanation": text or "(Keine Antwort von Claude erhalten)"}
    except Exception as e:
        logger.warning("Explain-call failed for %s/%s/%s: %s", company.ticker, value_key, period_year, e)
        raise HTTPException(503, f"Claude-Einordnung fehlgeschlagen: {str(e)[:200]}")


@values_router.post("/recalc-all-fy")
def recalc_all_fy(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    refetch_mcap: bool = Query(default=True, description="Re-fetch historical MCap with start-of-FY anchor"),
) -> dict:
    """Re-anchor all historical stammdaten to start-of-FY and re-run
    _run_and_persist_calculations for every (company, FY-year) the user owns.
    Use after changing the MCap-anchor semantics so stored values + Calculated
    rows reflect the new logic. Returns counts."""
    owned_pids = (
        db.query(Portfolio.id)
        .filter(
            (Portfolio.owner_user_id == user.id)
            | Portfolio.id.in_(
                db.query(PortfolioMember.portfolio_id).filter(PortfolioMember.user_id == user.id)
            )
        )
        .all()
    )
    pid_set = {p[0] for p in owned_pids}
    companies = (
        db.query(Company).filter(Company.portfolio_id.in_(pid_set)).all()
    )
    n_companies = 0
    n_years = 0
    n_mcap_refetched = 0
    for c in companies:
        years = (
            db.query(CompanyValue.period_year)
            .filter(
                CompanyValue.company_id == c.id,
                CompanyValue.period_type == "FY",
                CompanyValue.period_year.isnot(None),
            )
            .distinct()
            .all()
        )
        if not years:
            continue
        n_companies += 1
        for (yr,) in sorted(years):
            if refetch_mcap:
                # Wipe previous Yahoo-historical row so the new anchor takes effect
                # without being blocked by the from_ir_pdf=false / non-manual check.
                for k in ("market_cap", "stock_price", "shares_outstanding"):
                    db.query(CompanyValue).filter(
                        CompanyValue.company_id == c.id,
                        CompanyValue.value_key == k,
                        CompanyValue.period_type == "FY",
                        CompanyValue.period_year == yr,
                        CompanyValue.manually_overridden == False,  # noqa: E712
                        CompanyValue.from_ir_pdf == False,          # noqa: E712
                    ).delete(synchronize_session=False)
                try:
                    _fetch_and_store_historical_mcap(db, c.ticker, c.id, yr)
                    n_mcap_refetched += 1
                except Exception as e:
                    logger.warning("recalc-all-fy mcap refetch failed company=%s year=%s: %s", c.id, yr, e)
                    db.rollback()
            try:
                _run_and_persist_calculations(db, c.id, "FY", yr)
                n_years += 1
            except Exception as e:
                logger.warning("recalc-all-fy failed for company=%s year=%s: %s", c.id, yr, e)
                db.rollback()
        try:
            _run_and_persist_calculations(db, c.id, "SNAPSHOT", None)
        except Exception as e:
            logger.warning("recalc-all-fy SNAPSHOT failed for company=%s: %s", c.id, e)
            db.rollback()
    db.commit()
    return {
        "companies_processed": n_companies,
        "fy_rows_recalculated": n_years,
        "mcap_anchors_refetched": n_mcap_refetched,
    }


_CUM_INPUT_KEYS_FY: tuple[str, ...] = (
    "fcf",
    "net_income",
    "sbc",
    "buyback_volume",
    "dividends",
    "net_debt",
)


def _decimal_to_str(v: Decimal | None) -> str | None:
    return str(v) if v is not None else None


def _cell_to_dict(cell: dict) -> dict:
    return {
        "cum": _decimal_to_str(cell.get("cum")),
        "pa_avg": _decimal_to_str(cell.get("pa_avg")),
        "pa_cagr": _decimal_to_str(cell.get("pa_cagr")),
        "missing": cell.get("missing", []),
    }


@values_router.post("/{company_id}/values/historical-stammdaten")
def post_historical_stammdaten(
    company_id: UUID,
    period_year: int = Query(...),
    force: bool = Query(default=False),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Fetch + store historical Stammdaten (market_cap, stock_price,
    shares_outstanding) for the given FY year, anchored to the company's
    fiscal year-end. Auto-detects FY-end on first call. Refetches if stored
    data references a different FY-end-date than the company currently has.
    Always runs _run_and_persist_calculations afterwards."""
    company = _get_owned_company(db, user, company_id)
    from datetime import date
    if period_year >= date.today().year:
        _run_and_persist_calculations(db, company_id, "FY", period_year)
        db.commit()
        return {"stored": False, "reason": "period_year is current or future; using SNAPSHOT MCap"}

    fy_month, fy_day = _ensure_company_fy_end(db, company)
    db.commit()
    expected_marker = f"{fy_day:02d}.{fy_month:02d}.{period_year}"

    existing_mcap = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == "market_cap",
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == period_year,
            CompanyValue.numeric_value.isnot(None),
        )
        .first()
    )
    src = existing_mcap.source_name if existing_mcap else None
    has_old_v1_format = bool(src) and "current Shares" in src and "Shares per" not in src
    needs_refetch = (
        force
        or existing_mcap is None
        or (src and expected_marker not in src)
        or has_old_v1_format
    )
    if needs_refetch:
        _fetch_and_store_historical_mcap(db, company.ticker, company_id, period_year)
        db.commit()
    _run_and_persist_calculations(db, company_id, "FY", period_year)
    db.commit()
    return {"stored": needs_refetch, "period_year": period_year, "fy_end_used": expected_marker}


@values_router.get("/{company_id}/fy-availability")
def get_fy_availability(
    company_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    company = _get_owned_company(db, user, company_id)
    rows = (
        db.query(CompanyValue.period_year, CompanyValue.value_key)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year.isnot(None),
            CompanyValue.numeric_value.isnot(None),
        )
        .all()
    )
    keys_per_year: dict[int, list[str]] = {}
    for year, key in rows:
        keys_per_year.setdefault(year, []).append(key)
    snap_market_cap = (
        db.query(CompanyValue.id)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.period_type == "SNAPSHOT",
            CompanyValue.value_key == "market_cap",
            CompanyValue.numeric_value.isnot(None),
        )
        .first()
        is not None
    )
    return {
        "fy_years_with_data": sorted(keys_per_year.keys()),
        "keys_per_year": {str(y): sorted(set(ks)) for y, ks in keys_per_year.items()},
        "has_snapshot_market_cap": snap_market_cap,
        "is_us": is_us_company(company),
        "annual_report_years": annual_report_years(db, company_id),
        "quarter_years": quarter_years(db, company_id),
        "quarter_years_in_progress": quarter_years_in_progress(db, company_id),
    }


@values_router.get("/{company_id}/values/cumulative")
def get_cumulative_values(
    company_id: UUID,
    from_year: int = Query(...),
    to_year: int = Query(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    _get_owned_company(db, user, company_id)
    if from_year > to_year:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="from_year must be <= to_year")

    pre_year = from_year - 1
    period_years = list(range(from_year, to_year + 1))

    def _has_any_data(year: int) -> bool:
        return (
            db.query(CompanyValue.id)
            .filter(
                CompanyValue.company_id == company_id,
                CompanyValue.period_type == "FY",
                CompanyValue.period_year == year,
            )
            .first()
            is not None
        )

    missing_years = [y for y in [pre_year] + period_years if not _has_any_data(y)]

    year_data: dict[int, dict[str, Decimal | None]] = {}
    for year in period_years:
        _, data = _load_value_map(db, company_id, "FY", year)
        year_data[year] = data
    _, pre_data = _load_value_map(db, company_id, "FY", pre_year)
    _, stammdaten = _load_value_map(db, company_id, "SNAPSHOT", None)

    cum_results = calculate_cumulative(year_data, pre_data, stammdaten)

    per_year_breakdown: dict[int, dict[str, str | None]] = {}
    for year in period_years:
        per_year_breakdown[year] = {
            k: _decimal_to_str(year_data[year].get(k))
            for k in _CUM_INPUT_KEYS_FY
        }
    pre_breakdown = {
        k: _decimal_to_str(pre_data.get(k))
        for k in ("net_income", "net_debt")
    }

    first_year_mcap = year_data.get(from_year, {}).get("market_cap")
    snap_mcap = stammdaten.get("market_cap")
    anchor_mcap = first_year_mcap if first_year_mcap is not None else snap_mcap
    anchor_label = (
        f"Anfang FY{from_year}" if first_year_mcap is not None
        else "SNAPSHOT (Fallback — historischer MCap fehlt)"
    )
    return {
        "from_year": from_year,
        "to_year": to_year,
        "n_years": len(period_years),
        "market_cap": _decimal_to_str(anchor_mcap),
        "market_cap_anchor": anchor_label,
        "snapshot_market_cap": _decimal_to_str(snap_mcap),
        "values": {k: _cell_to_dict(v) for k, v in cum_results.items()},
        "per_year_breakdown": per_year_breakdown,
        "pre_period_year": pre_year,
        "pre_period_breakdown": pre_breakdown,
        "missing_years": missing_years,
    }


_CROSS_YEAR_TRIGGER_INPUTS: frozenset[str] = frozenset({
    "net_income",
    "net_debt",
})


def _existing_fy_years(db: Session, company_id: UUID) -> list[int]:
    rows = (
        db.query(CompanyValue.period_year)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year.isnot(None),
        )
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows})


def _fy_year_has_data(db: Session, company_id: UUID, year: int) -> bool:
    return (
        db.query(CompanyValue.id)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == year,
        )
        .first()
        is not None
    )


def _refresh_fy_from_quarters(
    db: Session,
    company_id: UUID,
    value_key: str,
    year: int,
) -> None:
    """Re-derive the FY row of a quarterly-estimate key from its 4 Q rows.
    Called after any Q-level write (manual override, provider ingest) to keep
    the FY value in sync with the sum of its parts.

    For SUMMABLE keys: FY = Q1 + Q2 + Q3 + Q4 (all must be present).
    For POINT_IN_TIME keys: FY = Q4 (Bilanzstichtag).
    No-op if the key is not a QUARTERLY_ESTIMATE_KEY or if the required Q
    rows are missing.
    """
    from app.values.period_keys import (
        QUARTERLY_ESTIMATE_KEYS,
        SUMMABLE_QUARTERLY_KEYS,
        POINT_IN_TIME_QUARTERLY_KEYS,
    )
    if value_key not in QUARTERLY_ESTIMATE_KEYS:
        return

    q_rows = {
        r.period_type: r
        for r in db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == value_key,
            CompanyValue.period_type.in_(("Q1", "Q2", "Q3", "Q4")),
            CompanyValue.period_year == year,
        )
        .all()
    }
    if not q_rows:
        return

    fy_value: Decimal | None = None
    fy_adj: Decimal | None = None
    fy_adj_note: str | None = None
    fy_source_parts: list[str] = []
    fy_latest_ts: datetime | None = None
    method_summary = "provider"

    if value_key in SUMMABLE_QUARTERLY_KEYS:
        if not all(q in q_rows and q_rows[q].numeric_value is not None for q in ("Q1", "Q2", "Q3", "Q4")):
            return
        fy_value = sum((q_rows[q].numeric_value for q in ("Q1", "Q2", "Q3", "Q4")), Decimal("0"))
        # Adjusted-Spur: sobald MINDESTENS EIN Quartal einen echten
        # Adjusted-Wert hat, Mischsumme (adjusted wenn vorhanden, sonst
        # GAAP je Quartal). Haben alle Quartale kein Adjusted, bleibt
        # FY-Adjusted NULL (Fallback-Marker fuers UI).
        adj_present = [q for q in ("Q1", "Q2", "Q3", "Q4") if q_rows[q].numeric_value_adjusted is not None]
        if adj_present:
            fy_adj = sum(
                (
                    q_rows[q].numeric_value_adjusted
                    if q_rows[q].numeric_value_adjusted is not None
                    else q_rows[q].numeric_value
                    for q in ("Q1", "Q2", "Q3", "Q4")
                ),
                Decimal("0"),
            )
            if len(adj_present) < 4:
                fy_adj_note = "Summe der Quartale (adjusted, GAAP-Fallback je Quartal)"
        methods: set[str] = set()
        for q in ("Q1", "Q2", "Q3", "Q4"):
            r = q_rows[q]
            methods.add(r.primary_method or "unknown")
            fy_source_parts.append(f"{q}: {r.primary_method or 'unknown'}")
            if r.fetched_at and (fy_latest_ts is None or r.fetched_at > fy_latest_ts):
                fy_latest_ts = r.fetched_at
        method_summary = "manual" if "manual" in methods else (
            "provider" if methods <= {"provider", "pdf"} else "calculated"
        )
    elif value_key in POINT_IN_TIME_QUARTERLY_KEYS:
        q4 = q_rows.get("Q4")
        if q4 is None or q4.numeric_value is None:
            return
        fy_value = q4.numeric_value
        fy_adj = q4.numeric_value_adjusted
        fy_source_parts.append(f"Q4 snapshot ({q4.primary_method or 'unknown'})")
        fy_latest_ts = q4.fetched_at
        method_summary = q4.primary_method or "calculated"

    if fy_value is None:
        return

    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    currency = company.currency if company and value_key in CURRENCY_KEYS else None

    existing_fy = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == value_key,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == year,
        )
        .first()
    )
    source_name = ("Derived Annual = Q1+Q2+Q3+Q4 | " + " | ".join(fy_source_parts))[:4000]
    if existing_fy:
        # Produktregel (User-Entscheid): sind alle 4 Quartale (bzw. Q4 bei
        # POINT_IN_TIME) gefuellt, GEWINNEN die Quartale — der FY wird
        # IMMER aus ihnen neu berechnet und ueberschreibt jeden bestehenden
        # Direkt-/Provider-/Perplexity-FY (auch is_forecast=False,
        # primary_method='provider'/'perplexity').
        # Bewusste Umkehr der frueheren "authoritative FY beats quarter
        # sums"-Regel (Commit efbd81e). Gesperrt bleiben nur:
        #   - manually_overridden (User-Lock),
        #   - from_ir_pdf mit Wert (authoritatives IR-Berichtsdokument).
        if existing_fy.manually_overridden:
            return
        if existing_fy.from_ir_pdf and existing_fy.numeric_value is not None:
            return
        existing_fy.numeric_value = fy_value
        # Geschuetzte Adjusted-Werte (Manual, 8-K-Enrichment mit SEC-URL)
        # nie ueberschreiben oder nullen — alle anderen (source NULL oder
        # Alt-Recherche) werden aus den Quartalen neu abgeleitet.
        from app.values.persistence import adjusted_is_protected
        if not adjusted_is_protected(existing_fy.adjustments_source):
            existing_fy.numeric_value_adjusted = fy_adj
            # Note/Source konsistent zum abgeleiteten Wert setzen: stale
            # Belege eines ueberschriebenen LLM-Adjusted passen nicht mehr;
            # bei Mischsumme dokumentiert die Note den GAAP-Fallback.
            existing_fy.adjustments_note = fy_adj_note
            existing_fy.adjustments_source = None
        existing_fy.source_name = source_name
        existing_fy.fetched_at = fy_latest_ts or datetime.now(timezone.utc)
        existing_fy.primary_method = method_summary
        if currency and not existing_fy.currency:
            existing_fy.currency = currency
    else:
        db.add(
            CompanyValue(
                id=uuid4(),
                company_id=company_id,
                value_key=value_key,
                period_type="FY",
                period_year=year,
                numeric_value=fy_value,
                numeric_value_adjusted=fy_adj,
                adjustments_note=fy_adj_note,
                source_name=source_name,
                fetched_at=fy_latest_ts or datetime.now(timezone.utc),
                primary_method=method_summary,
                is_forecast=True,
                currency=currency,
            )
        )


def _recalc_after_override(
    db: Session,
    company_id: UUID,
    value_key: str,
    period_type: str,
    period_year: int | None,
    trigger_label: str,
) -> None:
    affected: list[tuple[str, int | None]] = [(period_type, period_year)]

    # Iteration 3: If a Q row was overridden, re-derive the FY row from Q rows
    # before running metric calcs — so downstream metrics see the fresh FY.
    if (
        period_type in ("Q1", "Q2", "Q3", "Q4")
        and period_year is not None
    ):
        _refresh_fy_from_quarters(db, company_id, value_key, period_year)
        affected.append(("FY", period_year))

    if value_key == "market_cap":
        for year in _existing_fy_years(db, company_id):
            affected.append(("FY", year))
    elif (
        period_type == "FY"
        and period_year is not None
        and value_key in _CROSS_YEAR_TRIGGER_INPUTS
        and _fy_year_has_data(db, company_id, period_year + 1)
    ):
        affected.append(("FY", period_year + 1))

    # Iteration 4: recalculate all derived metrics for every affected FY.
    for pt, py in affected:
        _run_and_persist_calculations(db, company_id, pt, py)

    db.flush()


def _is_period_reported(
    company: Company, period_type: str, period_year: int | None
) -> bool:
    """Geteiltes Berichtet-Kriterium der Pipeline (detail_page): Periodenende
    + REPORTING_GRACE_DAYS abgelaufen. SNAPSHOT gilt immer als berichtet. Ist
    das Periodenende nicht bestimmbar (unbekannter period_type), gilt die
    Periode wie bisher als berichtet (Actual-Slot)."""
    if period_type == "SNAPSHOT" or period_year is None:
        return True
    from app.values.detail_page import REPORTING_GRACE_DAYS, quarter_end_date

    quarter = "Q4" if period_type == "FY" else period_type
    period_end = quarter_end_date(
        period_year, quarter,
        getattr(company, "fiscal_year_end_month", None),
        getattr(company, "fiscal_year_end_day", None),
    )
    if period_end is None:
        return True
    today = datetime.now(timezone.utc).date()
    return (today - period_end).days >= REPORTING_GRACE_DAYS


@values_router.post("/{company_id}/values/{value_key}/override", response_model=CompanyValueOut)
def override_company_value(
    company_id: UUID,
    value_key: str,
    payload: OverrideRequest,
    period_type: str = Query(default="SNAPSHOT"),
    period_year: int | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CompanyValue:
    company = _get_owned_company(db, user, company_id)

    if value_key in CALCULATED_KEYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{value_key}' ist ein berechneter Wert (Formel) und kann nicht "
                "direkt gesetzt werden. Korrigiere stattdessen die Eingangswerte."
            ),
        )

    effective_period_type = "SNAPSHOT" if value_key in ALWAYS_CURRENT_KEYS else period_type
    effective_period_year = None if value_key in ALWAYS_CURRENT_KEYS else period_year

    oq = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == value_key,
            CompanyValue.period_type == effective_period_type,
        )
    )
    if effective_period_year is not None:
        oq = oq.filter(CompanyValue.period_year == effective_period_year)
    else:
        oq = oq.filter(CompanyValue.period_year.is_(None))
    rows = oq.order_by(CompanyValue.is_forecast.asc()).all()

    # Slot-Wahl: fuer BERICHTETE Perioden zielt der Override wie bisher auf
    # den Actual-Eintrag (is_forecast=False sortiert vorne). Fuer noch NICHT
    # berichtete Perioden ist ein manueller Wert eine Schaetzung — er gehoert
    # in den FORECAST-Slot. Sonst landete er als manueller ACTUAL auf der
    # not_found-Platzhalterzeile: falsche Farbe und per Lock-Kontrakt
    # dauerhaft gegen die Actual-Writer (Anker/Bruecke) gesperrt, obwohl
    # berichtete Zahlen ihn spaeter ersetzen sollen. Text-Overrides behalten
    # das alte Verhalten (nur der numerische Pfad ist betroffen).
    target_is_forecast = (
        payload.numeric_value is not None
        and not _is_period_reported(company, effective_period_type, effective_period_year)
    )
    if target_is_forecast:
        forecast_row = next((r for r in rows if r.is_forecast), None)
        actual_row = next((r for r in rows if not r.is_forecast), None)
        if forecast_row is not None:
            existing = forecast_row
            # Leere Actual-Platzhalterzeile (not_found) abraeumen — sie
            # bliebe sonst als leerer Actual neben dem manuellen Forecast.
            if (
                actual_row is not None
                and actual_row.numeric_value is None
                and actual_row.text_value is None
            ):
                db.delete(actual_row)
                db.flush()
        elif actual_row is not None:
            # Nur der Actual-Slot existiert (not_found-Platzhalter): auf
            # Forecast flippen. Unique-Index bleibt gewahrt, weil kein
            # Forecast-Slot existiert.
            actual_row.is_forecast = True
            existing = actual_row
        else:
            existing = None
    else:
        existing = rows[0] if rows else None

    inherit_currency = company.currency if value_key in CURRENCY_KEYS else None

    from app.values.persistence import normalize_sign
    override_value = normalize_sign(value_key, payload.numeric_value, context="manual override")

    if payload.variant == "adjusted":
        # Adjusted-Override: schreibt NUR numeric_value_adjusted auf der
        # bestehenden Zeile. GAAP-Felder (numeric_value, primary_method,
        # manually_overridden, source_name, is_forecast, from_ir_pdf)
        # bleiben unangetastet. adjustments_source='Manual' schuetzt den
        # Wert vor dem Anker-Cleanup (adjusted_is_protected) und
        # vor dem Adjusted-Enrichment (fuellt nur NULL-Felder).
        if payload.text_value is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Adjusted-Overrides gibt es nur numerisch (kein text_value).",
            )
        if payload.numeric_value is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="numeric_value ist fuer variant=adjusted erforderlich.",
            )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Kein Basiswert vorhanden — erst GAAP-Wert anlegen.",
            )
        existing.numeric_value_adjusted = override_value
        existing.adjustments_note = "Manuell ueberschrieben"
        existing.adjustments_source = "Manual"
        result_cv = existing
    elif existing:
        if override_value is not None:
            existing.numeric_value = override_value
        if payload.text_value is not None:
            existing.text_value = payload.text_value
        if payload.source_name is not None:
            existing.source_name = payload.source_name
        if inherit_currency and not existing.currency:
            existing.currency = inherit_currency
        existing.manually_overridden = True
        existing.from_ir_pdf = False
        existing.primary_method = "manual"
        existing.fetched_at = datetime.now(timezone.utc)
        result_cv = existing
    else:
        cv = CompanyValue(
            id=uuid4(),
            company_id=company_id,
            value_key=value_key,
            period_type=effective_period_type,
            period_year=effective_period_year,
            is_forecast=target_is_forecast,
            numeric_value=override_value,
            text_value=payload.text_value,
            source_name=payload.source_name,
            currency=inherit_currency,
            manually_overridden=True,
            from_ir_pdf=False,
            primary_method="manual",
            fetched_at=datetime.now(timezone.utc),
        )
        db.add(cv)
        result_cv = cv

    db.flush()

    vd_for_label = db.query(ValueDefinition).filter(ValueDefinition.key == value_key).one_or_none()
    trigger_label = vd_for_label.label_en if vd_for_label else value_key

    _recalc_after_override(
        db, company_id, value_key, effective_period_type, effective_period_year, trigger_label,
    )
    db.commit()
    db.refresh(result_cv)
    return result_cv
