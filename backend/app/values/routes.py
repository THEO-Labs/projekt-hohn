"""Company-Values API: Werte-Refresh + Manual-Override + Calculations.

Daten-Pipeline (Stand: ESEF-Iteration, PDF-Auto-Extraction deaktiviert):

  STAMMDATEN (immer Live-Snapshot)
    - stock_price / market_cap / shares_outstanding -> Yahoo-Provider
    - market_cap_calc = stock_price * shares (in calculation_engine)

  FY-BACKTESTS (abgeschlossene Geschaeftsjahre)
    Provider-Chain in _try_providers (EDGAR > ESEF > Yahoo):
      - US-Filer (ISIN US...): EDGAR-XBRL liefert FY-Werte aus 10-K/20-F
      - EU-Filer (NL/FR/ES/IT/SE/...): ESEF-XBRL via filings.xbrl.org
      - Yahoo-Fallback nur noch fuer US-Filer (Kundenentscheid: der
        Marktdaten-Feed ist fuer Nicht-US keine Fundamental-Wertequelle)
    Nicht-US ohne ESEF (Munich Re, Allianz, ...): Statement-Recherche
    (statement_research: EIN Call pro Firma+Jahr+Statement-Gruppe;
    Yahoo dient dort nur noch als Cross-Check-Referenz).

  FY-ESTIMATES (laufendes FY, period_year >= current_year)
    US- UND Nicht-US-Filer: EIN gebuendelter Guidance-Call
    (guidance_estimates) + deterministische Ableitungen (Carry-Forward,
    Residuen) — keine LLM-Recherche pro Key.

  CALCULATED FELDER (FCF-Yield, EV/EBITDA, Hohn-Return, ...)
    calculation_engine.calculate_fy nach Werte-Refresh.

  ADJUSTED-WERTE (NI/EBITDA/FCF non-GAAP)
    US: 8-K-Reconciliation (adjusted_enrichment) + Guidance-Sidecars.
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
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
from app.values.models import CompanyValue, SourceType, ValueDefinition
from app.values.progress import cleanup_old_jobs, finish_job, get_job, mark_success, set_phase, start_job, update_job
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


def _try_providers(ticker: str, key: str, payload, fy_end_month, fy_end_day,
                   isin: str | None = None, company=None):
    # Kundenentscheid: der Marktdaten-Feed (provider_kind='market', Yahoo)
    # schreibt fuer Nicht-US-Firmen keine Fundamental-Werte mehr.
    # Stammdaten (ALWAYS_CURRENT_KEYS: stock_price/shares/market_cap)
    # bleiben ausdruecklich auf dem Feed — Marktdaten sind kein
    # Berichtswert.
    skip_market = (
        company is not None
        and key not in ALWAYS_CURRENT_KEYS
        and not is_us_company(company)
    )
    for provider in get_providers(key):
        if skip_market and getattr(provider, "provider_kind", None) == "market":
            continue
        try:
            # Cascading fallback fuer unterschiedliche fetch-Signaturen:
            # 1) Full (mit isin) — ESEFProvider
            # 2) FY-Anker (fy_end_month/day) — EdgarProvider
            # 3) Minimal — YahooProvider
            result = None
            for kwargs in (
                {"fy_end_month": fy_end_month, "fy_end_day": fy_end_day, "isin": isin},
                {"fy_end_month": fy_end_month, "fy_end_day": fy_end_day},
                {},
            ):
                try:
                    result = provider.fetch(
                        ticker, key, payload.period_type, payload.period_year, **kwargs,
                    )
                    break
                except TypeError:
                    continue
            if result is not None:
                return result
        except Exception as e:
            logger.warning("Provider fetch failed for %s/%s: %s", ticker, key, e)
    return None


def _anchor_fy_after_apply(db: Session, company, key: str, year: int, updated: list) -> bool:
    """XBRL-Anker: der strukturierte Filing-Wert schlaegt jeden
    Recherche-Wert als FY-Anker fuer abgeschlossene Jahre. Fehler duerfen
    den Refresh niemals crashen."""
    try:
        from app.values.provider_anchor import anchor_fy_with_provider
        anchored = anchor_fy_with_provider(db, company, key, year)
    except Exception as e:
        logger.warning(
            "Provider-Anker failed for %s/%s/FY%s: %s", company.ticker, key, year, e,
        )
        return False
    if anchored:
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
        if row is not None and row not in updated:
            updated.append(row)
    return anchored


def _anchor_us_key_periods(
    db: Session,
    key: str,
    company,
    company_id: UUID,
    updated: list,
    year: int,
) -> bool:
    """US-Filer: EDGAR-XBRL-Anker statt LLM-Recherche.

    Deckt den Refresh-Key-Loop fuer US-Firmen komplett:
    FY + Q1-Q4 des Jahres aus XBRL ankern; Luecken
    schliessen 8-K-Bruecke und Residuum-/Carry-Forward-Ableitungen im
    Konsistenz-Pass — sonst bleibt die Zelle leer (Strich). Keys ohne
    EDGAR-Konzept (z.B. net_debt) sind No-op. Returns True wenn der
    Anker mindestens eine Periode gedeckt hat.
    """
    from app.providers.edgar import EdgarProvider
    from app.values.provider_anchor import anchor_key_periods_with_provider

    if key not in EdgarProvider.supported_keys:
        return False
    try:
        covered = anchor_key_periods_with_provider(db, company, key, year)
    except Exception as e:
        logger.warning(
            "EDGAR-Anker failed for %s/%s/FY%s: %s",
            company.ticker, key, year, e,
        )
        db.rollback()
        return False
    if not covered:
        return False
    for row in (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_year == year,
            CompanyValue.period_type.in_(("FY", "Q1", "Q2", "Q3", "Q4")),
            CompanyValue.is_forecast.is_(False),
        )
        .all()
    ):
        if row not in updated:
            updated.append(row)
    return True


def _prev_year_needs_backfill(
    db: Session, company_id: UUID, key: str, prev_year: int
) -> bool:
    """True if there is no fresh two-stage/statement/provider row for
    (company, key, FY prev_year).

    Skip-if-already-good: if we already have a two_stage_* row for FY N-1
    (any variant: confirmed / verified / insufficient), eine
    statement_research-Zeile oder eine geankerte 'provider'-Zeile (XBRL),
    we do NOT rerun it on a FY N refresh. That keeps subsequent
    'Refresh full' clicks cheap.
    """
    # first() statt one_or_none(): Actual+Forecast koennen koexistieren,
    # die Actual-Zeile entscheidet ueber die Frische.
    row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_year == prev_year,
            CompanyValue.period_type == "FY",
        )
        .order_by(CompanyValue.is_forecast.asc())
        .first()
    )
    if row is None:
        return True
    pm = row.primary_method or ""
    return not (
        pm.startswith("two_stage_")
        or pm in ("provider", "statement_research")
    )


# FY-N-2-Anker fuer die H-Rendite des Vorjahres (N-1): deren FY-Changes
# (ni_growth, Delta-Net-Debt) brauchen net_income/eps/revenue plus die
# Bilanzkomponenten als FY-N-2-Basis. dividends/buybacks/sbc/ocf/capex
# sind nice-to-have — der Nicht-US-FY-only-Lauf deckt sie ueber die
# Statement-Gruppen ohnehin mit ab.
_N2_FY_ANCHOR_KEYS = (
    "net_income",
    "eps_diluted",
    "revenue",
    "cash_and_equivalents",
    "st_investments",
    "st_debt",
    "lt_debt",
)


def _n2_fy_anchor_missing(db: Session, company_id: UUID, year: int) -> bool:
    """FY-Kernanker fuer N-2 fehlt: keine net_income-FY-Actual-Zeile mit
    Wert aus frischer/authoritativer Herkunft (provider/statement_research/
    two_stage_*/manual/PDF). Leer, not_found, calculated und web_*-Reste
    zaehlen als fehlend — dann lohnt der FY-only-Backfill."""
    row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == "net_income",
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == year,
            CompanyValue.is_forecast.is_(False),
            CompanyValue.numeric_value.isnot(None),
        )
        .first()
    )
    if row is None:
        return True
    if row.manually_overridden or row.from_ir_pdf:
        return False
    pm = row.primary_method or ""
    return not (
        pm.startswith("two_stage_")
        or pm in ("provider", "statement_research", "manual", "pdf")
    )


def _process_one_key(
    db: Session,
    key: str,
    ticker: str,
    company,
    company_id: UUID,
    payload,
    updated: list,
) -> bool:
    """Returns True if a value was actually written/updated, False if skipped
    (manual override / PDF value / no provider result).

    Source-Strategie (nur noch Provider — keine LLM-Recherche mehr):
      - Stammdaten (stock_price/shares/market_cap): immer Yahoo
      - US-Filer (ISIN US...): EDGAR + Yahoo Provider-Chain
      - Nicht-US abgeschlossene FY: ESEF; Luecken fuellt statement_research
        im FY-Refresh-Flow
    """
    effective_period_type = "SNAPSHOT" if key in ALWAYS_CURRENT_KEYS else payload.period_type
    effective_period_year = None if key in ALWAYS_CURRENT_KEYS else payload.period_year

    from datetime import date as _date_today

    is_stammdaten = key in ALWAYS_CURRENT_KEYS
    is_running_fy = (
        effective_period_type == "FY"
        and effective_period_year is not None
        and effective_period_year >= _date_today.today().year
    )

    # Priority-Lookup: erst actuals (is_forecast=False), dann forecast.
    # Actuals trumpfen Forecast/Guidance, Manual und PDF blockieren Estimate.
    def _query_for(is_forecast_val: bool):
        q = db.query(CompanyValue).filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == effective_period_type,
            CompanyValue.is_forecast.is_(is_forecast_val),
        )
        if effective_period_year is not None:
            q = q.filter(CompanyValue.period_year == effective_period_year)
        else:
            q = q.filter(CompanyValue.period_year.is_(None))
        return q.one_or_none()

    actuals_existing = _query_for(False)
    forecast_existing = _query_for(True) if is_running_fy else None

    # PDF-Werte sind authoritative — werden nie überschrieben.
    # Manual-Overrides bei FY actuals werden bei Refresh AUSDRUECKLICH
    # überschrieben (User-Wahl: 'Werte berechnen' = neu berechnen, Manual war
    # nur temporaer). Frontend zeigt "Manuell überschreiben"-Button am Cell-
    # Tooltip damit User weiss dass Override fluechtig ist.
    if actuals_existing and actuals_existing.from_ir_pdf and actuals_existing.numeric_value is not None:
        updated.append(actuals_existing)
        return False

    # Pre-existing für den Update-Pfad: bei is_running_fy nutzen wir die
    # forecast-Row (falls vorhanden), sonst die actuals-Row.
    pre_existing = forecast_existing if is_running_fy else actuals_existing
    result = None

    # Manual-Override bleibt Hard-Lock — User-Eingabe wird nie automatisch
    # ueberschrieben.
    forecast_locked = bool(forecast_existing and forecast_existing.manually_overridden)

    # Provider-Chain (EDGAR > ESEF > Yahoo): bei Stammdaten IMMER, bei
    # FY-Backtests fuer US-Filer (EDGAR) UND EU-Filer (ESEF). Bei
    # is_running_fy=True (Estimate-Mode) Provider-Chain skippen — die liefern
    # keine Forecasts; Estimates kommen aus fetch_guidance_estimates im
    # FY-Refresh-Flow, nicht aus diesem Pfad.
    if is_stammdaten or not is_running_fy:
        result = _try_providers(
            ticker, key, payload,
            getattr(company, "fiscal_year_end_month", None),
            getattr(company, "fiscal_year_end_day", None),
            isin=getattr(company, "isin", None),
            company=company,
        )

    # Forecast-Lock: nur Manual-Override blockiert ein automatisches Update.
    if forecast_locked and forecast_existing is not None:
        updated.append(forecast_existing)
        return True

    if result is None:
        # Refresh hat nichts geliefert. Aber: wir markieren die last_refresh_attempt
        # damit das Frontend einen Stale-Indikator zeigen kann (Daten in DB sind
        # alt, letzter Refresh produzierte nichts neues).
        if pre_existing is not None:
            pre_existing.last_refresh_attempt = datetime.now(timezone.utc)
            db.flush()
            updated.append(pre_existing)
        return False

    # `pre_existing` from the up-front guard is a fresh query result; reuse it
    # so we don't double-query. It can have changed under us only if a
    # concurrent writer mutated the row, which the SAVEPOINT branch below
    # handles via IntegrityError.
    existing = pre_existing

    numeric_value: Decimal | None = None
    text_value: str | None = None
    if isinstance(result.value, Decimal):
        numeric_value = result.value
    elif result.value is not None:
        text_value = str(result.value)

    # Sign normalisation last-mile: every persistence path (PDF, EDGAR, Yahoo,
    # Claude-research, factor-estimate) goes through here, so applying abs()
    # here covers ALL sources at once instead of patching each provider.
    from app.values.persistence import currency_conflict, normalize_sign
    numeric_value = normalize_sign(
        key, numeric_value,
        context=f"{ticker}/FY{effective_period_year} source={result.source_name}",
    )

    if existing and currency_conflict(key, existing.currency, result.currency):
        # Currency-Mismatch HARTER REJECT statt silent overwrite. Sonst mischen
        # sich USD/EUR/GBP-Werte in FY-Cross-Year-Aggregaten und produzieren
        # falsche Yields/Hohn-Renditen. Lieber alten Wert behalten + last_refresh
        # markieren damit User nachvollziehen kann dass Refresh nicht durchging.
        logger.warning(
            "Currency mismatch BLOCKED %s/%s/%s: existing=%s new=%s (source=%s) — "
            "alter Wert bleibt erhalten, Refresh-Versuch markiert",
            ticker, key, effective_period_year, existing.currency, result.currency, result.source_name,
        )
        existing.last_refresh_attempt = datetime.now(timezone.utc)
        db.flush()
        updated.append(existing)
        return False

    is_forecast_flag = bool((result.extras or {}).get("is_forecast", False)) if result.extras else False

    # Primary-Method-Marker setzen: explizites Field statt fragile
    # Source-Name-Heuristik im Frontend.
    extras = result.extras or {}
    if extras.get("guidance_method") in ("web_research", "quarterly_aggregation"):
        primary_method = "web_guidance"
    else:
        primary_method = "provider"

    # Adjusted/Non-GAAP-Variante aus extras parsen (kommt von Dual-Web-Research).
    raw_adj = extras.get("value_adjusted")
    value_adjusted: Decimal | None = None
    if raw_adj is not None:
        try:
            value_adjusted = Decimal(str(raw_adj))
        except Exception:
            value_adjusted = None
    adjustments_note_val = extras.get("adjustments_note")
    adjustments_source_val = extras.get("adjustments_source")

    def _apply_update(target: CompanyValue) -> None:
        target.numeric_value = numeric_value
        target.text_value = text_value
        target.currency = result.currency
        target.source_name = result.source_name
        target.source_link = result.source_link
        now = datetime.now(timezone.utc)
        target.fetched_at = now
        target.last_refresh_attempt = now
        target.from_ir_pdf = False
        target.is_forecast = is_forecast_flag
        target.primary_method = primary_method
        # Manual-Override-Flag zuruecksetzen wenn Refresh erfolgreich war
        # (User-Override war temporaer).
        target.manually_overridden = False
        # Adjusted-Werte nur ueberschreiben wenn der Run Adjusted geliefert hat
        # — sonst alten Adjusted-Wert (z.B. aus AR-PDF) erhalten.
        if value_adjusted is not None:
            target.numeric_value_adjusted = value_adjusted
            target.adjustments_note = (adjustments_note_val or "")[:4000] or None
            target.adjustments_source = (adjustments_source_val or "")[:2048] or None

    try:
        if existing:
            _apply_update(existing)
            updated.append(existing)
            db.flush()
            return True
        now = datetime.now(timezone.utc)
        cv = CompanyValue(
            id=uuid4(),
            company_id=company_id,
            value_key=key,
            period_type=effective_period_type,
            period_year=effective_period_year,
            numeric_value=numeric_value,
            numeric_value_adjusted=value_adjusted,
            adjustments_note=(adjustments_note_val or "")[:4000] or None,
            adjustments_source=(adjustments_source_val or "")[:2048] or None,
            text_value=text_value,
            currency=result.currency,
            source_name=result.source_name,
            source_link=result.source_link,
            fetched_at=now,
            last_refresh_attempt=now,
            is_forecast=is_forecast_flag,
            primary_method=primary_method,
        )
        try:
            with db.begin_nested():
                db.add(cv)
                db.flush()
            updated.append(cv)
            return True
        except IntegrityError:
            # Concurrent insert from another request — re-query and update.
            # Filter MUST include is_forecast — sonst kollidiert ein
            # Forecast-Row mit einem Actuals-Row im Result.
            eq2 = (
                db.query(CompanyValue)
                .filter(
                    CompanyValue.company_id == company_id,
                    CompanyValue.value_key == key,
                    CompanyValue.period_type == effective_period_type,
                    CompanyValue.is_forecast.is_(is_forecast_flag),
                )
            )
            if effective_period_year is not None:
                eq2 = eq2.filter(CompanyValue.period_year == effective_period_year)
            else:
                eq2 = eq2.filter(CompanyValue.period_year.is_(None))
            row = eq2.one_or_none()
            if row is None:
                raise
            if row.manually_overridden or (row.from_ir_pdf and row.numeric_value is not None):
                updated.append(row)
                return False
            _apply_update(row)
            updated.append(row)
            db.flush()
            return True
    except Exception as e:
        logger.error("DB save failed for key=%s company=%s: %s", key, ticker, e)
        db.rollback()
        return False


_PREV_YEAR_GROWTH_KEYS = (
    "net_income",
    "fcf",
    "net_debt",
    "sbc",
    "buyback_volume",
    "dividends",
)


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


def _ensure_previous_year_inputs(
    db: Session,
    ticker: str,
    company,
    company_id: UUID,
    period_year: int,
) -> None:
    """Fetch previous-FY per-input values if they are not yet persisted.
    Required so ni_growth and net_debt_change can be computed for period_year."""
    prev_year = period_year - 1

    existing = db.query(CompanyValue).filter(
        CompanyValue.company_id == company_id,
        CompanyValue.period_type == "FY",
        CompanyValue.period_year == prev_year,
        CompanyValue.value_key.in_(_PREV_YEAR_GROWTH_KEYS),
    ).all()
    existing_keys = {r.value_key for r in existing if r.numeric_value is not None}
    missing = [k for k in _PREV_YEAR_GROWTH_KEYS if k not in existing_keys]
    if not missing:
        return

    if not is_us_company(company):
        # Nicht-US: Statement-Recherche.
        # Erst der Provider-Anker (nur ESEF — der Markt-Feed schreibt
        # keine Fundamentals mehr; nur abgeschlossene Jahre), dann EIN
        # Statement-Lauf fuer die Gruppen der noch fehlenden Keys;
        # fcf/net_debt liefern die deterministischen Ableitungen.
        from app.values.provider_anchor import anchor_fy_with_provider
        for key in missing:
            try:
                anchor_fy_with_provider(db, company, key, prev_year)
            except Exception as e:
                logger.warning(
                    "Prev-year provider anchor failed for %s/%s FY%s: %s",
                    ticker, key, prev_year, e,
                )
                db.rollback()
        db.flush()
        still = db.query(CompanyValue).filter(
            CompanyValue.company_id == company_id,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == prev_year,
            CompanyValue.value_key.in_(missing),
            CompanyValue.numeric_value.isnot(None),
        ).all()
        still_missing = [k for k in missing if k not in {r.value_key for r in still}]
        if not still_missing:
            return
        try:
            from app.values.consistency import (
                derive_missing_fcf,
                derive_net_debt_from_components,
            )
            from app.values.statement_research import (
                fetch_statement_research,
                groups_for_keys,
            )
            fetch_statement_research(
                db, company, prev_year, groups=groups_for_keys(still_missing),
            )
            derive_net_debt_from_components(db, company_id, prev_year)
            derive_missing_fcf(db, company_id, [prev_year])
        except Exception as e:
            logger.warning(
                "Prev-year statement research failed for %s FY%s: %s",
                ticker, prev_year, e,
            )
            db.rollback()
        return

    class _PrevPayload:
        period_type = "FY"
        period_year = prev_year

    prev_payload = _PrevPayload()
    dummy_updated: list = []
    for key in missing:
        try:
            _process_one_key(
                db=db,
                key=key,
                ticker=ticker,
                company=company,
                company_id=company_id,
                payload=prev_payload,
                updated=dummy_updated,
            )
        except Exception as e:
            logger.warning("Prev-year prefetch failed for %s/%s FY%s: %s", ticker, key, prev_year, e)
            db.rollback()


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
    updated = []

    # Stammdaten-Only-Modus: nur die Live-API-Stammdaten-Keys refreshen
    # (Market Cap, Stock Price, Shares Outstanding) — kein FY-Fundamental-
    # Web-Research. "Daily Numbers"-Button im UI.
    if payload.stammdaten_only:
        effective_keys = [k for k in payload.keys if k in ALWAYS_CURRENT_KEYS]
    else:
        effective_keys = payload.keys

    # Full-FY-Refresh (not stammdaten_only): Provider-Anker + neue
    # Recherche-Fluesse (US: EDGAR/8-K-Bruecke/Guidance; Nicht-US:
    # statement_research/Guidance) statt Per-Key-LLM-Recherche.
    full_fy_refresh = (
        not payload.stammdaten_only
        and payload.period_type == "FY"
        and payload.period_year is not None
    )

    # US-Filer: abgeschlossene Jahre deckt EDGAR-Anker + 8-K-Bruecke +
    # Residuum, das laufende FY der gebuendelte Guidance-Call +
    # deterministische Ableitungen.
    us_filer = is_us_company(company)

    # Laufendes (nicht abgeschlossenes) FY: EIN fetch_guidance_estimates-
    # Call vor dem Konsistenz-Pass deckt die Estimate-Keys ab — fuer US-
    # UND Nicht-US-Filer. Die US-spezifischen Key-Loop-Skips (EDGAR-Anker
    # + Bilanz-Fortschreibung ersetzen dort die Recherche) haengen weiter
    # am kombinierten US-Gate.
    guidance_fy = False
    if full_fy_refresh:
        try:
            from app.values.provider_anchor import _fy_is_closed
            guidance_fy = not _fy_is_closed(company, payload.period_year)
        except Exception as e:
            logger.warning("guidance-estimate gate failed for %s: %s", ticker, e)
            guidance_fy = False
    us_guidance_fy = us_filer and guidance_fy

    # Precompute prev-year backfill list: ein FY-N-Refresh fuellt FY N-1
    # fuer jeden Key ohne frische Anker-/Recherche-Zeile mit. Skip
    # already-good rows so a second Refresh full click stays cheap.
    research_eligible_keys: list[str] = []
    prev_year_backfill_keys: list[str] = []
    if full_fy_refresh:
        for k in effective_keys:
            if k in ALWAYS_CURRENT_KEYS or k in CALCULATED_KEYS:
                continue
            research_eligible_keys.append(k)
            if _prev_year_needs_backfill(db, company_id, k, payload.period_year - 1):
                prev_year_backfill_keys.append(k)

    total_steps = len(effective_keys) + len(prev_year_backfill_keys)
    start_job(company_id, total_steps)
    try:
        # Pro Key committen: ein Fehler (und der zugehoerige Rollback) in
        # Key N darf die bereits erfolgreich geschriebenen Keys 1..N-1
        # nicht mit verwerfen. mark_success erst NACH erfolgreichem Commit.
        from app.values.consistency import BALANCE_CARRY_FORWARD_KEYS
        from app.values.guidance_estimates import GUIDANCE_ESTIMATE_KEYS
        for key in effective_keys:
            update_job(company_id, key)
            # US-Filer, laufendes FY: Estimate-Keys deckt der gebuendelte
            # Guidance-Call (unten, vor dem Konsistenz-Pass) ab. Berichtete
            # Quartale ankert der Quartals-Anker im Konsistenz-Block.
            if us_guidance_fy and key in GUIDANCE_ESTIMATE_KEYS:
                continue
            # Bilanz-Keys fuers laufende FY: die Fortschreibung des letzten
            # berichteten Bilanzstichtags (derive_balance_carry_forward im
            # Konsistenz-Block) deckt sie ab. Berichtete Quartale ankern
            # Quartals-Anker + 8-K-Bruecke.
            if us_guidance_fy and key in BALANCE_CARRY_FORWARD_KEYS:
                continue
            updated_before = len(updated)
            try:
                if (
                    full_fy_refresh
                    and key not in ALWAYS_CURRENT_KEYS
                    and key not in CALCULATED_KEYS
                ):
                    if us_filer:
                        # US: nur EDGAR-XBRL ankern — keine LLM-Recherche.
                        # Luecken schliessen 8-K-Bruecke + Residuum-Pass.
                        wrote = _anchor_us_key_periods(
                            db=db, key=key, company=company,
                            company_id=company_id, updated=updated,
                            year=payload.period_year,
                        )
                    else:
                        # Nicht-US: Provider-First-FY-Anker (nur ESEF —
                        # der Markt-Feed schreibt keine Fundamentals mehr)
                        # fuer abgeschlossene Jahre. Die LLM-Recherche
                        # laeuft danach EINMAL pro Jahr als Statement-
                        # Call-Trio (statement_research, vor dem
                        # Konsistenz-Pass).
                        wrote = _anchor_fy_after_apply(
                            db, company, key, payload.period_year, updated,
                        )
                else:
                    wrote = _process_one_key(
                        db=db,
                        key=key,
                        ticker=ticker,
                        company=company,
                        company_id=company_id,
                        payload=payload,
                        updated=updated,
                    )
                db.commit()
                if wrote:
                    mark_success(company_id)
            except Exception as e:
                logger.error("Unexpected error processing key=%s for company=%s: %s", key, ticker, e)
                db.rollback()
                # Verworfene Instanzen des fehlgeschlagenen Keys aus
                # `updated` entfernen — db.refresh am Ende darf keine nie
                # committeten Zeilen anfassen.
                del updated[updated_before:]

        # Stammdaten-Only (Daily-Refresh): naechsten Earnings-Termin
        # mitpflegen — hoechstens alle 24h (earnings_checked_at-TTL).
        if payload.stammdaten_only:
            _maybe_refresh_next_earnings(db, company, ticker)
            db.commit()

        # Prev-year backfill: fuer die Recherche-Keys ohne frische
        # FY-N-1-Zeile laeuft der Anker-/Recherche-Pfad auch fuer FY N-1.
        if prev_year_backfill_keys:
            prev_year = payload.period_year - 1
            for key in prev_year_backfill_keys:
                update_job(company_id, f"{key} (FY{prev_year})")
                updated_before = len(updated)
                try:
                    if us_filer:
                        # US: Vorjahres-Backfill nur via EDGAR-Anker.
                        wrote = _anchor_us_key_periods(
                            db=db, key=key, company=company,
                            company_id=company_id, updated=updated,
                            year=prev_year,
                        )
                    else:
                        # Nicht-US: Provider-Anker; die Recherche-Luecken
                        # fuellt der Statement-Lauf fuer FY N-1 unten.
                        wrote = _anchor_fy_after_apply(
                            db, company, key, prev_year, updated,
                        )
                    db.commit()
                    if wrote:
                        mark_success(company_id)
                except Exception as e:
                    logger.error(
                        "prev-year backfill failed for %s / %s / FY%s: %s",
                        ticker, key, prev_year, e,
                    )
                    db.rollback()
                    del updated[updated_before:]

        # Cross-Metrik-Konsistenz: net_debt aus Komponenten ableiten (eine
        # Definition ueber alle Jahre) und Kern-Identitaeten pruefen/flaggen.
        if full_fy_refresh and payload.period_year is not None:
            from app.values.consistency import (
                derive_balance_carry_forward,
                derive_clear_stale_forecasts,
                derive_declared_dividend_quarter,
                derive_gaap_from_adjusted_spread,
                derive_missing_fcf,
                derive_missing_ocf,
                derive_net_debt_from_components,
                derive_open_quarter_from_fy_estimate,
                derive_q4_instant_from_fy,
                derive_q4_residual_from_fy,
                derive_runrate_quarter,
                derive_sbc_quarters,
                validate_cross_metrics,
            )
            # Wenn der Prev-Year-Backfill Keys verarbeitet hat, muss der
            # Konsistenz-Pass auch fuer FY N-1 laufen — sonst bleiben
            # FY/Quartals-Mismatches im Vorjahr ungeflaggt.
            consistency_years = (
                [payload.period_year - 1] if prev_year_backfill_keys else []
            ) + [payload.period_year]

            # FY-only-Backfill fuer N-2 (User-Konvention: die H-Rendite
            # rechnet auf FY-Basis — die FY-Changes des VORJAHRES (N-1)
            # brauchen die FY-N-2-Anker). Fehlt der Kernanker (net_income
            # FY leer/ersetzbar), laeuft einmalig: Nicht-US ein FY-only-
            # Statement-Lauf (periods=('FY',), 1-3 Calls, kein Quartals-
            # Backfill — Konvention); US der EDGAR-FY-Anker fuer die
            # Kern-Keys (der Key-Loop/Prev-Year-Backfill decken nur
            # N/N-1). Danach net_debt aus den Komponenten, damit
            # Delta-ND/H-Rendite N-1 rechnen. Laeuft VOR der Statement-
            # Recherche fuer N-1/N, damit das Vorjahresband-Gate die
            # N-2-Actuals sieht. Fehler brechen den Refresh nie ab.
            n2_year = payload.period_year - 2
            n2_backfilled = False
            if _n2_fy_anchor_missing(db, company_id, n2_year):
                try:
                    set_phase(
                        company_id, "n2_fy_backfill",
                        f"FY-Anker FY{n2_year} nachladen",
                    )
                    if us_filer:
                        from app.values.provider_anchor import (
                            anchor_fy_with_provider,
                        )
                        for n2_key in _N2_FY_ANCHOR_KEYS:
                            try:
                                if anchor_fy_with_provider(
                                    db, company, n2_key, n2_year,
                                ):
                                    n2_backfilled = True
                            except Exception as e:
                                logger.warning(
                                    "n2 fy anchor failed %s/%s FY%s: %s",
                                    ticker, n2_key, n2_year, e,
                                )
                    else:
                        from app.values.statement_research import (
                            fetch_statement_research,
                        )
                        from app.llm.cost_tracker import CostTracker
                        n2_tracker = CostTracker()
                        wrote_n2 = fetch_statement_research(
                            db, company, n2_year,
                            cost_tracker=n2_tracker, periods=("FY",),
                        )
                        n2_backfilled = wrote_n2 > 0
                        if n2_tracker.calls:
                            logger.info(
                                "n2 fy backfill %s: %d Zeilen, %d Claude-"
                                "Calls, %.4f USD",
                                ticker, wrote_n2, n2_tracker.calls,
                                n2_tracker.spent_usd,
                            )
                    if n2_backfilled:
                        derive_net_debt_from_components(db, company_id, n2_year)
                    db.commit()
                except Exception as e:
                    logger.warning(
                        "n2 fy backfill failed for %s FY%s: %s",
                        ticker, n2_year, e,
                    )
                    db.rollback()
                    n2_backfilled = False

            # Nicht-US: EIN Recherche-Call pro Jahr und Statement-Gruppe
            # (max. 3 Calls) statt Recherche pro Key.
            # Reihenfolge N-1 vor N, damit das Vorjahresband-Gate die
            # frischen N-1-Actuals sieht. Nur die Gruppen der angefragten
            # Keys. Der Yahoo-Feed schreibt keine Quartale mehr
            # (Kundenentscheid) — er liefert nur noch die Cross-Check-
            # Referenzen innerhalb von fetch_statement_research.
            # Fehler brechen den Refresh nie ab.
            if not us_filer:
                try:
                    from app.values.statement_research import (
                        fetch_statement_research,
                        groups_for_keys,
                    )
                    from app.llm.cost_tracker import CostTracker
                    stmt_groups = groups_for_keys(research_eligible_keys)
                    if stmt_groups:
                        stmt_tracker = CostTracker()
                        for stmt_year in consistency_years:
                            set_phase(
                                company_id, "statement_research",
                                f"Statement-Recherche (FY{stmt_year})",
                            )
                            fetch_statement_research(
                                db, company, stmt_year,
                                cost_tracker=stmt_tracker, groups=stmt_groups,
                            )
                            db.commit()
                        if stmt_tracker.calls:
                            logger.info(
                                "statement research %s: %d Claude-Calls, %.4f USD",
                                ticker, stmt_tracker.calls, stmt_tracker.spent_usd,
                            )
                except Exception as e:
                    logger.warning("statement research failed for %s: %s", ticker, e)
                    db.rollback()

            # Quartals-Anker (Gegenstueck zum FY-Anker im Key-Loop):
            # gefilte Quartale mit exakten
            # EDGAR-XBRL-Werten ueberschreiben — VOR dem Konsistenz-Pass,
            # damit validate_cross_metrics die finalen Werte prueft.
            # Fehler duerfen den Refresh nicht abbrechen.
            try:
                from app.values.provider_anchor import anchor_quarters_with_provider
                anchor_quarters_with_provider(db, company, consistency_years)
                db.commit()
            except Exception as e:
                logger.warning("quarter anchor failed for %s: %s", ticker, e)
                db.rollback()
            # GAAP-Bruecke fuer das Release-zu-10-Q-Fenster: Zahlen aus dem
            # 8-K-Earnings-Release fuellen not_found/Estimate-Zellen, bis
            # das 10-Q-XBRL in der companyfacts-API ankommt und der Anker
            # die provider-Zeilen ueberschreibt. Fehler brechen nie ab.
            try:
                from app.values.gaap_bridge import bridge_gaap_from_earnings_releases
                from app.llm.cost_tracker import CostTracker
                bridge_tracker = CostTracker()
                bridge_gaap_from_earnings_releases(
                    db, company, consistency_years, cost_tracker=bridge_tracker,
                )
                db.commit()
                if bridge_tracker.calls:
                    logger.info(
                        "gaap bridge %s: %d Claude-Calls, %.4f USD",
                        ticker, bridge_tracker.calls, bridge_tracker.spent_usd,
                    )
            except Exception as e:
                logger.warning("gaap bridge failed for %s: %s", ticker, e)
                db.rollback()
            # Non-GAAP-Anreicherung aus 8-K-Earnings-Releases: der
            # Anker-Pfad liefert keine Adjusted-Werte. Fill-only-NULL;
            # Fehler brechen den Refresh nie ab.
            try:
                from app.values.adjusted_enrichment import (
                    enrich_adjusted_from_earnings_releases,
                )
                from app.llm.cost_tracker import CostTracker
                # Der Refresh-Flow hat keinen flowweiten CostTracker —
                # eigener Tracker fuer das Kosten-Logging der
                # Enrichment-Calls.
                adj_tracker = CostTracker()
                enrich_adjusted_from_earnings_releases(
                    db, company, consistency_years, cost_tracker=adj_tracker,
                )
                db.commit()
                if adj_tracker.calls:
                    logger.info(
                        "adjusted enrichment %s: %d Claude-Calls, %.4f USD",
                        ticker, adj_tracker.calls, adj_tracker.spent_usd,
                    )
            except Exception as e:
                logger.warning("adjusted enrichment failed for %s: %s", ticker, e)
                db.rollback()
            # FY-Guidance-Estimates fuers laufende FY (US- und Nicht-US-
            # Filer): EIN Claude-Call ersetzt die uebersprungenen bzw.
            # nicht mehr recherchierten Estimate-Keys. Muss VOR dem
            # Konsistenz-Pass laufen, damit
            # derive_open_quarter_from_fy_estimate die frischen
            # FY-Forecasts sieht. Fehler brechen den Refresh nie ab.
            if guidance_fy:
                try:
                    from app.values.guidance_estimates import fetch_guidance_estimates
                    from app.llm.cost_tracker import CostTracker
                    set_phase(
                        company_id, "guidance_estimates",
                        f"FY-Guidance-Schaetzungen (FY{payload.period_year})",
                    )
                    guid_tracker = CostTracker()
                    # Offenes Quartal mitgeben: der Call fragt das Modell
                    # direkt danach, statt Q-Werte als FY-Residuum zu bauen.
                    # Nur bei GENAU EINEM offenen Quartal eindeutig.
                    open_q = None
                    try:
                        # Generisches Berichtet-Kriterium: Karenz, US-Filer
                        # zusaetzlich Item-2.02-8-K (_quarter_reported).
                        from app.values.consistency import _quarter_reported
                        _subs: dict = {}
                        _open = [
                            q for q in ("Q1", "Q2", "Q3", "Q4")
                            if not _quarter_reported(company, payload.period_year, q, _subs)
                        ]
                        if len(_open) == 1:
                            open_q = _open[0]
                    except Exception:
                        open_q = None
                    wrote_est = fetch_guidance_estimates(
                        db, company, payload.period_year, cost_tracker=guid_tracker,
                        open_quarter=open_q,
                    )
                    db.commit()
                    if guid_tracker.calls:
                        logger.info(
                            "guidance estimates %s: %d FY-Werte, %d Claude-Calls, %.4f USD",
                            ticker, wrote_est, guid_tracker.calls, guid_tracker.spent_usd,
                        )
                except Exception as e:
                    logger.warning("guidance estimates failed for %s: %s", ticker, e)
                    db.rollback()
            for cons_year in consistency_years:
                try:
                    # Stale Forecast-Werte berichteter Perioden raeumen —
                    # NACH den Actual-Writern (Anker/Bruecke/Statement-
                    # Recherche laufen oben im Flow), VOR den Ableitungen
                    # und validate_cross_metrics: die Ableitungen bauen
                    # danach nur noch belegbare Werte neu auf (z.B. die
                    # Bilanz-Fortschreibung), veraltete Schaetzungen
                    # berichteter Perioden verschwinden aus der Anzeige.
                    derive_clear_stale_forecasts(db, company_id, cons_year)
                    # Bilanz-Fortschreibung VOR der net_debt-Ableitung und
                    # VOR validate_cross_metrics: Q4/FY-Staende aus dem
                    # letzten berichteten Stichtag ersetzen aeltere
                    # Bilanz-Schaetzungen, net_debt rechnet danach mit den
                    # fortgeschriebenen Komponenten.
                    derive_balance_carry_forward(db, company_id, cons_year)
                    # Q4 = FY bei Instant-Keys abgeschlossener Jahre
                    # (gleicher Stichtag) — VOR der net_debt-Ableitung,
                    # damit sie die Q4-Komponenten sieht.
                    derive_q4_instant_from_fy(db, company_id, cons_year)
                    derive_net_debt_from_components(db, company_id, cons_year)
                    derive_missing_ocf(db, company_id, cons_year)
                    # SBC-FY/4-Verteilung entfernt (Pilot-Befund SAP: die
                    # Gleichverteilung ueberschrieb echte Quartalswerte,
                    # 423.75 statt dokumentierter 420). Yahoo-Quartals-
                    # Anker, Statement-Recherche und Runrate decken SBC.
                    # Q4-Restwert (ebitda/revenue/net_income): EDGAR liefert
                    # Q4 fuer Income-Keys strukturell nicht (kein 3M-Frame
                    # im 10-K).
                    derive_q4_residual_from_fy(db, company_id, cons_year)
                    # Offenes Rest-Quartal deterministisch aus dem FY-
                    # Estimate (Guidance/Konsens) statt LLM-Schaetzung.
                    derive_open_quarter_from_fy_estimate(db, company_id, cons_year)
                    # GAAP-EPS/NI aus dem beobachteten GAAP/Non-GAAP-
                    # Abstand: braucht die adjusted-Residuen der Zeile
                    # darueber, VOR derive_missing_fcf/validate.
                    derive_gaap_from_adjusted_spread(db, company_id, cons_year)
                    # Deklarierte Dividendenrate NACH der Guidance-Ableitung:
                    # die Fortschreibung des zuletzt berichteten Quartals
                    # schlaegt das FY-Guidance-Residuum (calculated ist
                    # ersetzbar).
                    derive_declared_dividend_quarter(db, company_id, cons_year)
                    # SBC/Buyback-Runrate NUR als Fallback ohne FY-Konsens
                    # (nach derive_open_quarter: der Konsens-Pfad hat
                    # Vorrang), VOR derive_missing_fcf.
                    derive_runrate_quarter(db, company_id, cons_year)
                    # fcf = OCF - Capex als berechneter Wert — VOR
                    # validate_cross_metrics, damit der fcf-Check die
                    # frischen Werte sieht.
                    derive_missing_fcf(db, company_id, [cons_year])
                    # Validator-Diet auch fuer Nicht-US (full_checks=False):
                    # qsum + fcf-Identitaet + eps_ni bleiben; die uebrigen
                    # Checks decken die Schreib-Gates der neuen Pfade ab.
                    validate_cross_metrics(
                        db, company_id, cons_year, is_us=us_filer,
                        full_checks=False,
                    )
                    db.commit()
                except Exception as e:
                    logger.error("consistency pass failed for %s FY%s: %s", ticker, cons_year, e)
                    db.rollback()

            # Vorjahres-Kennzahlen + historische Preis-Anker (strukturell,
            # US und Nicht-US): der Schluss-Calc unten rechnet nur
            # payload.period_year — jedes weitere consistency_year
            # (abgeschlossenes Vorjahr) bekommt hier seinen FY-Ratio-Satz.
            # Fuer abgeschlossene Jahre vorher die Jahresschluss-Anker
            # sicherstellen: year (Einstiegs-Anker = Close FY-Ende N-1)
            # und year+1 (Close FY-Ende N, actual_return braucht den
            # Folgejahres-Anker). Vorhandene Anker werden nicht erneut
            # gefetcht (_has_fy_price_anchor); Fehler brechen den Refresh
            # nie ab.
            from app.values.provider_anchor import _fy_is_closed as _fy_closed
            for calc_year in consistency_years:
                if calc_year == payload.period_year:
                    continue  # laeuft am Ende des Refresh ohnehin
                try:
                    if _fy_closed(company, calc_year):
                        set_phase(
                            company_id, "historical_mcap",
                            f"Historische Preis-Anker (FY{calc_year})",
                        )
                        for anchor_year in (calc_year, calc_year + 1):
                            if not _has_fy_price_anchor(db, company_id, anchor_year):
                                _fetch_and_store_historical_mcap(
                                    db, ticker, company_id, anchor_year,
                                )
                        db.commit()
                    set_phase(
                        company_id, "calculating",
                        f"Kennzahlen FY{calc_year} berechnen",
                    )
                    _run_and_persist_calculations(db, company_id, "FY", calc_year)
                    db.commit()
                except Exception as e:
                    logger.warning(
                        "prev-year ratios/anchors failed for %s FY%s: %s",
                        ticker, calc_year, e,
                    )
                    db.rollback()

            # H-Rendite N-1 nach erfolgreichem N-2-Backfill: ni_growth/
            # Delta-ND des Vorjahres rechnen mit den frischen FY-N-2-
            # Ankern. Gezielter Zusatz-Call — nur wenn N-1 nicht ohnehin
            # als consistency_year gerechnet wurde (der Loop oben deckt
            # diesen Fall bereits ab).
            n1_year = payload.period_year - 1
            if n2_backfilled and n1_year not in consistency_years:
                try:
                    set_phase(
                        company_id, "calculating",
                        f"Kennzahlen FY{n1_year} berechnen",
                    )
                    _run_and_persist_calculations(db, company_id, "FY", n1_year)
                    db.commit()
                except Exception as e:
                    logger.warning(
                        "n2 backfill ratios failed for %s FY%s: %s",
                        ticker, n1_year, e,
                    )
                    db.rollback()

        # Bei Stammdaten-Only: kein historisches MCap-Fetch, kein Prev-Year-
        # Refresh — die haben mit den taeglichen Live-Werten nichts zu tun.
        if not payload.stammdaten_only and payload.period_type == "FY" and payload.period_year is not None:
            from datetime import date
            current_calendar_year = date.today().year
            if payload.period_year < current_calendar_year:
                set_phase(company_id, "historical_mcap", f"Historische Market Cap (31.12.{payload.period_year})")
                _fetch_and_store_historical_mcap(db, ticker, company_id, payload.period_year)
                db.commit()

            set_phase(company_id, "prev_year_inputs", f"Vorjahres-Daten holen (FY{payload.period_year - 1})")
            _ensure_previous_year_inputs(db, ticker, company, company_id, payload.period_year)
            db.commit()

        # Calculations laufen IMMER — auch bei Stammdaten-Only, damit
        # market_cap_calc + abhaengige Multiples (PE, EV/EBITDA, FCF-Yield)
        # mit den neuen Live-Stammdaten neu berechnet werden.
        set_phase(company_id, "calculating", "Berechnete Werte aktualisieren")
        _run_and_persist_calculations(db, company_id, payload.period_type, payload.period_year)
        db.commit()
    except Exception:
        finish_job(company_id, status="failed")
        raise
    else:
        finish_job(company_id)

    for cv in updated:
        db.refresh(cv)
    return updated


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
        # Do not override a manually_overridden FY row (user chose to lock it).
        if existing_fy.manually_overridden:
            return
        # FY-first (Nicht-US-Konvention): ein autoritativer FY-Wert direkt
        # aus dem Bericht (statement_research) oder vom XBRL/Berichts-
        # Provider schlaegt die Quartalssumme — Quartale sind Best-Effort
        # und duerfen einen exakten Jahreswert nie verwaessern. Die
        # Aggregation fuellt nur leere/abgeleitete/Alt-FY-Zeilen.
        if (
            existing_fy.numeric_value is not None
            and not existing_fy.is_forecast
            and (existing_fy.primary_method or "") in ("statement_research", "provider")
        ):
            # Nur die leere adjusted-Spur darf die Aggregation noch fuellen
            # (Quartals-adjusted vorhanden, FY-adjusted NULL — Mischsumme).
            from app.values.persistence import adjusted_is_protected
            if (
                fy_adj is not None
                and existing_fy.numeric_value_adjusted is None
                and not adjusted_is_protected(existing_fy.adjustments_source)
            ):
                existing_fy.numeric_value_adjusted = fy_adj
                existing_fy.adjustments_note = fy_adj_note
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
    """Geteiltes Berichtet-Kriterium der Pipeline (detail_page/gaap_bridge):
    Periodenende + REPORTING_GRACE_DAYS abgelaufen ODER (US-Filer) ein
    Item-2.02-8-K nach dem Periodenende. SNAPSHOT gilt immer als berichtet.
    Der Submissions-Fetch laeuft nur, wenn er wirklich entscheidet (US-Filer,
    Periode beendet, Karenz nicht um); Fetch-Fehler fallen in has_reported_8k
    konservativ auf die reine Karenz-Regel zurueck (-> nicht berichtet)."""
    if period_type == "SNAPSHOT" or period_year is None:
        return True
    from app.values import gaap_bridge
    from app.values.adjusted_enrichment import _period_end
    from app.values.detail_page import REPORTING_GRACE_DAYS

    period_end = _period_end(company, period_type, period_year)
    if period_end is None:
        # Kein bestimmbares Periodenende (unbekannter period_type) ->
        # Verhalten wie bisher (Actual-Slot).
        return True
    today = datetime.now(timezone.utc).date()
    if (today - period_end).days >= REPORTING_GRACE_DAYS:
        return True
    if period_end >= today or not is_us_company(company):
        return False
    return gaap_bridge.has_reported_8k(company.ticker, period_end, cache={})


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
