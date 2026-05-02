import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.config import settings
from app.auth.deps import current_user
from app.auth.models import User
from app.calculations.engine import (
    CALCULATED_KEYS,
    CUMULATIVE_KEYS,
    FY_CALC_KEYS,
    STAMMDATEN_CALC_KEYS,
    calculate_cumulative,
    calculate_fy,
    calculate_stammdaten,
)
from app.companies.models import Company
from app.db import get_db
from app.portfolios.models import Portfolio
from app.llm.claude import research_value, validate_claude_value
from app.providers.registry import get_providers
from app.values.always_current import ALWAYS_CURRENT_KEYS
from app.values.currency_keys import CURRENCY_KEYS
from app.values.models import CompanyValue, SourceType, ValueDefinition
from app.values.progress import cleanup_old_jobs, finish_job, get_job, mark_success, set_phase, start_job, update_job
from app.values.schemas import CompanyValueOut, OverrideRequest, RefreshRequest, ValueDefinitionOut

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


def _persist_calc_results(
    db: Session,
    company_id: UUID,
    period_type: str,
    period_year: int | None,
    existing_rows: list[CompanyValue],
    calc_results: dict[str, Decimal | None],
    allowed_keys: set[str],
    company_currency: str | None,
) -> list[CompanyValue]:
    by_key = {row.value_key: row for row in existing_rows}
    updated: list[CompanyValue] = []

    for key, value in calc_results.items():
        if key not in allowed_keys:
            continue

        existing = by_key.get(key)
        if value is None and existing is None:
            continue

        calc_currency = company_currency if key in CURRENCY_KEYS else None

        if existing:
            existing.numeric_value = value
            existing.source_name = "Calculated"
            existing.source_link = None
            existing.fetched_at = datetime.now(timezone.utc)
            existing.manually_overridden = False
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
                source_name="Calculated",
                source_link=None,
                fetched_at=datetime.now(timezone.utc),
                currency=calc_currency,
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
    for key, val in stammdaten_calc.items():
        if val is not None and key not in stammdaten:
            stammdaten[key] = val

    updated: list[CompanyValue] = []
    updated += _persist_calc_results(
        db, company_id, "SNAPSHOT", None,
        snapshot_rows, stammdaten_calc, STAMMDATEN_CALC_KEYS, company_currency,
    )

    if period_type == "FY" and period_year is not None:
        current_rows, current = _load_value_map(db, company_id, "FY", period_year)
        _prev_rows, previous = _load_value_map(db, company_id, "FY", period_year - 1)

        if current.get("stock_price") is not None and current.get("shares_outstanding") is not None:
            fy_stammdaten_calc = calculate_stammdaten(current)
            updated += _persist_calc_results(
                db, company_id, "FY", period_year,
                current_rows, fy_stammdaten_calc, STAMMDATEN_CALC_KEYS, company_currency,
            )
            current_rows, current = _load_value_map(db, company_id, "FY", period_year)

        fy_calc = calculate_fy(current, previous, stammdaten)
        updated += _persist_calc_results(
            db, company_id, "FY", period_year,
            current_rows, fy_calc, FY_CALC_KEYS, company_currency,
        )

    return updated


def _get_owned_company(db: Session, user: User, company_id: UUID) -> Company:
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    portfolio = db.query(Portfolio).filter(Portfolio.id == company.portfolio_id).one_or_none()
    if not portfolio or portfolio.owner_user_id != user.id:
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


def _process_one_key(
    db: Session,
    key: str,
    ticker: str,
    company,
    company_id: UUID,
    payload,
    updated: list,
) -> None:
    from app.providers.base import ProviderResult
    from app.llm.models import LlmConversation, LlmMessage

    effective_period_type = "SNAPSHOT" if key in ALWAYS_CURRENT_KEYS else payload.period_type
    effective_period_year = None if key in ALWAYS_CURRENT_KEYS else payload.period_year

    providers = get_providers(key)
    result = None
    for provider in providers:
        try:
            result = provider.fetch(ticker, key, payload.period_type, payload.period_year)
            if result is not None:
                break
        except Exception as e:
            logger.warning("Provider fetch failed for %s/%s: %s", ticker, key, e)
            continue

    if result is None:
        vd = db.query(ValueDefinition).filter(ValueDefinition.key == key).one_or_none()
        if vd and vd.source_type.value in ("API",) and settings.anthropic_api_key:
            label = f"{vd.label_en} ({vd.label_de})"
            try:
                research_val, research_source, research_url, user_prompt, assistant_response = research_value(
                    company.name, ticker, label, company.currency,
                    period_type=effective_period_type, period_year=effective_period_year,
                    value_key=key,
                )
            except Exception as e:
                logger.warning("Claude research failed for %s/%s: %s", ticker, key, e)
                return

            if research_val is not None:
                research_val = validate_claude_value(key, research_val)

            if research_val is not None:
                result = ProviderResult(
                    value=research_val,
                    source_name=research_source or "Claude-Recherche",
                    source_link=research_url,
                    currency=company.currency if key in CURRENCY_KEYS else None,
                )

            if user_prompt and assistant_response:
                try:
                    q = db.query(LlmConversation).filter(
                        LlmConversation.company_id == company_id,
                        LlmConversation.value_key == key,
                        LlmConversation.period_type == effective_period_type,
                    )
                    if effective_period_year is None:
                        q = q.filter(LlmConversation.period_year.is_(None))
                    else:
                        q = q.filter(LlmConversation.period_year == effective_period_year)
                    existing_conv = q.first()
                    if not existing_conv:
                        existing_conv = LlmConversation(
                            company_id=company_id, value_key=key,
                            period_type=effective_period_type, period_year=effective_period_year,
                        )
                        db.add(existing_conv)
                        db.flush()
                    msg_count = (
                        db.query(LlmMessage)
                        .filter(LlmMessage.conversation_id == existing_conv.id)
                        .count()
                    )
                    if msg_count == 0:
                        db.add(LlmMessage(conversation_id=existing_conv.id, role="user", content=user_prompt))
                        db.add(LlmMessage(
                            conversation_id=existing_conv.id,
                            role="assistant",
                            content=assistant_response,
                            score_suggestion=research_val,
                        ))
                        db.flush()
                except Exception as e:
                    logger.warning("Failed to save Claude conversation for %s/%s: %s", ticker, key, e)

    if result is None:
        return

    eq = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == effective_period_type,
        )
    )
    if effective_period_year is not None:
        eq = eq.filter(CompanyValue.period_year == effective_period_year)
    else:
        eq = eq.filter(CompanyValue.period_year.is_(None))
    existing = eq.one_or_none()

    if existing and existing.manually_overridden:
        updated.append(existing)
        return

    numeric_value: Decimal | None = None
    text_value: str | None = None
    if isinstance(result.value, Decimal):
        numeric_value = result.value
    elif result.value is not None:
        text_value = str(result.value)

    def _apply_update(target: CompanyValue) -> None:
        target.numeric_value = numeric_value
        target.text_value = text_value
        target.currency = result.currency
        target.source_name = result.source_name
        target.source_link = result.source_link
        target.fetched_at = datetime.now(timezone.utc)

    try:
        if existing:
            _apply_update(existing)
            updated.append(existing)
            db.flush()
        else:
            cv = CompanyValue(
                id=uuid4(),
                company_id=company_id,
                value_key=key,
                period_type=effective_period_type,
                period_year=effective_period_year,
                numeric_value=numeric_value,
                text_value=text_value,
                currency=result.currency,
                source_name=result.source_name,
                source_link=result.source_link,
                fetched_at=datetime.now(timezone.utc),
            )
            try:
                with db.begin_nested():
                    db.add(cv)
                    db.flush()
                updated.append(cv)
            except IntegrityError:
                # Concurrent insert from another request — re-query and update
                eq2 = (
                    db.query(CompanyValue)
                    .filter(
                        CompanyValue.company_id == company_id,
                        CompanyValue.value_key == key,
                        CompanyValue.period_type == effective_period_type,
                    )
                )
                if effective_period_year is not None:
                    eq2 = eq2.filter(CompanyValue.period_year == effective_period_year)
                else:
                    eq2 = eq2.filter(CompanyValue.period_year.is_(None))
                row = eq2.one_or_none()
                if row is None:
                    raise
                if row.manually_overridden:
                    updated.append(row)
                else:
                    _apply_update(row)
                    updated.append(row)
                    db.flush()
    except Exception as e:
        logger.error("DB save failed for key=%s company=%s: %s", key, ticker, e)
        db.rollback()


_PREV_YEAR_GROWTH_KEYS = (
    "net_income",
    "fcf",
    "cash_and_equivalents",
    "marketable_securities_st",
    "marketable_securities_lt",
    "lease_liabilities",
    "long_term_debt",
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
        existing.numeric_value = value
        existing.source_name = source_name
        existing.source_link = source_link
        existing.currency = currency
        existing.fetched_at = now
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
    """Fetch historical Market Cap, Stock Price and current Shares for FY-end approximation
    (Close FY-end-of-YYYY × current shares). Stores all three as period_type=FY values.
    Best-effort — failures logged."""
    providers = get_providers("market_cap")
    provider = next((p for p in providers if hasattr(p, "fetch_historical_market_cap")), None)
    if provider is None:
        return
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if company is None:
        return
    fy_month, fy_day = _ensure_company_fy_end(db, company)
    try:
        result = provider.fetch_historical_market_cap(ticker, period_year, fy_month, fy_day)
    except Exception as e:
        logger.warning("Historical MCap fetch failed %s/FY%s: %s", ticker, period_year, e)
        return
    if result is None or not isinstance(result.value, Decimal):
        return

    extras = getattr(result, "extras", None) or {}
    stock_price = extras.get("stock_price") if isinstance(extras, dict) else None
    shares = extras.get("shares_outstanding") if isinstance(extras, dict) else None
    as_of = extras.get("as_of_date") if isinstance(extras, dict) else None
    shares_source = extras.get("shares_source") if isinstance(extras, dict) else None
    as_of_label = as_of or f"FY{period_year}"
    shares_label = shares_source or "current Shares"

    _upsert_fy_value(db, company_id, "market_cap", period_year, result.value,
                     f"Yahoo (Close {as_of_label} × {shares_label})", result.source_link, result.currency)
    if isinstance(stock_price, Decimal):
        _upsert_fy_value(db, company_id, "stock_price", period_year, stock_price,
                         f"Yahoo (Adj Close {as_of_label})", result.source_link, result.currency)
    if isinstance(shares, Decimal):
        _upsert_fy_value(db, company_id, "shares_outstanding", period_year, shares,
                         f"Yahoo ({shares_label})", result.source_link, None)


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

    start_job(company_id, len(payload.keys))
    try:
        for key in payload.keys:
            update_job(company_id, key)
            before_count = len(updated)
            try:
                _process_one_key(
                    db=db,
                    key=key,
                    ticker=ticker,
                    company=company,
                    company_id=company_id,
                    payload=payload,
                    updated=updated,
                )
                if len(updated) > before_count:
                    mark_success(company_id)
            except Exception as e:
                logger.error("Unexpected error processing key=%s for company=%s: %s", key, ticker, e)
                db.rollback()

        db.commit()

        if payload.period_type == "FY" and payload.period_year is not None:
            from datetime import date
            current_calendar_year = date.today().year
            if payload.period_year < current_calendar_year:
                set_phase(company_id, "historical_mcap", f"Historische Market Cap (31.12.{payload.period_year})")
                _fetch_and_store_historical_mcap(db, ticker, company_id, payload.period_year)
                db.commit()

            set_phase(company_id, "prev_year_inputs", f"Vorjahres-Daten holen (FY{payload.period_year - 1})")
            _ensure_previous_year_inputs(db, ticker, company, company_id, payload.period_year)
            db.commit()

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


_CUM_INPUT_KEYS_FY: tuple[str, ...] = (
    "fcf",
    "net_income",
    "sbc",
    "buyback_volume",
    "dividends",
    "cash_and_equivalents",
    "marketable_securities_st",
    "marketable_securities_lt",
    "lease_liabilities",
    "long_term_debt",
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


@values_router.get("/{company_id}/stock-return")
def get_stock_return(
    company_id: UUID,
    start_date: str = Query(..., description="ISO YYYY-MM-DD"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Returns total return + CAGR of the stock from start_date until today,
    based on Adj Close. Used for backtest-comparison vs Hohn-Rendite."""
    company = _get_owned_company(db, user, company_id)
    from datetime import date as _date
    try:
        sd = _date.fromisoformat(start_date)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="start_date must be ISO YYYY-MM-DD")
    providers = get_providers("stock_price")
    provider = next((p for p in providers if hasattr(p, "fetch_stock_return")), None)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No stock-return provider available")
    result = provider.fetch_stock_return(company.ticker, sd)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No price data for {company.ticker} from {start_date}")
    return {
        "company_id": str(company_id),
        "ticker": company.ticker,
        "requested_start": start_date,
        **result,
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
    _get_owned_company(db, user, company_id)
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
        for k in ("net_income", "net_debt", "cash_and_equivalents", "marketable_securities_st", "marketable_securities_lt", "lease_liabilities", "long_term_debt", "debt_sum", "cash_sum")
    }

    return {
        "from_year": from_year,
        "to_year": to_year,
        "n_years": len(period_years),
        "market_cap": _decimal_to_str(stammdaten.get("market_cap")),
        "values": {k: _cell_to_dict(v) for k, v in cum_results.items()},
        "per_year_breakdown": per_year_breakdown,
        "pre_period_year": pre_year,
        "pre_period_breakdown": pre_breakdown,
        "missing_years": missing_years,
    }


_CROSS_YEAR_TRIGGER_INPUTS: frozenset[str] = frozenset({
    "net_income",
    "cash_and_equivalents",
    "marketable_securities_st",
    "marketable_securities_lt",
    "lease_liabilities",
    "long_term_debt",
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


def _snapshot_calc_values(
    db: Session,
    company_id: UUID,
    period_type: str,
    period_year: int | None,
) -> dict[str, Decimal | None]:
    q = db.query(CompanyValue).filter(
        CompanyValue.company_id == company_id,
        CompanyValue.value_key.in_(CALCULATED_KEYS),
        CompanyValue.period_type == period_type,
    )
    if period_year is None:
        q = q.filter(CompanyValue.period_year.is_(None))
    else:
        q = q.filter(CompanyValue.period_year == period_year)
    return {row.value_key: row.numeric_value for row in q.all()}


def _format_period_label(period_type: str, period_year: int | None) -> str:
    if period_type == "FY" and period_year is not None:
        return f"FY{period_year}"
    if period_type == "SNAPSHOT":
        return "aktuell"
    return period_type


def _format_value_for_log(v: Decimal | None) -> str:
    return "—" if v is None else str(v)


def _log_recalc_messages(
    db: Session,
    company_id: UUID,
    period_type: str,
    period_year: int | None,
    snapshot: dict[str, Decimal | None],
    trigger_value_key: str,
    trigger_label: str,
    trigger_period_label: str,
    trigger_period_type: str,
    trigger_period_year: int | None,
) -> None:
    from app.llm.models import LlmMessage
    from app.llm.routes import _get_or_create_conversation

    new_values = _snapshot_calc_values(db, company_id, period_type, period_year)
    for key, new_val in new_values.items():
        old_val = snapshot.get(key)
        if old_val == new_val:
            continue
        if (
            key == trigger_value_key
            and period_type == trigger_period_type
            and period_year == trigger_period_year
        ):
            continue
        try:
            conv = _get_or_create_conversation(db, company_id, key, period_type, period_year)
            content = (
                f"Automatisch neu berechnet: {_format_value_for_log(old_val)} → "
                f"{_format_value_for_log(new_val)} "
                f"(Trigger: {trigger_label} {trigger_period_label})"
            )
            db.add(LlmMessage(
                conversation_id=conv.id,
                role="system",
                content=content,
                source="recalc",
            ))
        except Exception as e:
            logger.warning("Recalc system-message failed for %s/%s: %s", company_id, key, e)


def _recalc_after_override(
    db: Session,
    company_id: UUID,
    value_key: str,
    period_type: str,
    period_year: int | None,
    trigger_label: str,
) -> None:
    affected: list[tuple[str, int | None]] = [(period_type, period_year)]

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

    snapshots: dict[tuple[str, int | None], dict[str, Decimal | None]] = {
        (pt, py): _snapshot_calc_values(db, company_id, pt, py) for pt, py in affected
    }

    for pt, py in affected:
        _run_and_persist_calculations(db, company_id, pt, py)

    db.flush()

    trigger_period_label = _format_period_label(period_type, period_year)
    for pt, py in affected:
        _log_recalc_messages(
            db, company_id, pt, py,
            snapshots[(pt, py)],
            value_key, trigger_label, trigger_period_label,
            period_type, period_year,
        )


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
    from app.llm.models import LlmConversation, LlmMessage
    from app.llm.routes import _get_or_create_conversation

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
    existing = oq.one_or_none()

    inherit_currency = company.currency if value_key in CURRENCY_KEYS else None

    if existing:
        if payload.numeric_value is not None:
            existing.numeric_value = payload.numeric_value
        if payload.text_value is not None:
            existing.text_value = payload.text_value
        if payload.source_name is not None:
            existing.source_name = payload.source_name
        if inherit_currency and not existing.currency:
            existing.currency = inherit_currency
        existing.manually_overridden = True
        result_cv = existing
    else:
        cv = CompanyValue(
            id=uuid4(),
            company_id=company_id,
            value_key=value_key,
            period_type=effective_period_type,
            period_year=effective_period_year,
            numeric_value=payload.numeric_value,
            text_value=payload.text_value,
            source_name=payload.source_name,
            currency=inherit_currency,
            manually_overridden=True,
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

    try:
        conv = _get_or_create_conversation(db, company_id, value_key, effective_period_type, effective_period_year)
        formatted_value = (
            str(payload.numeric_value) if payload.numeric_value is not None
            else (payload.text_value or "—")
        )
        source_hint = payload.source_name or "Manuell"
        system_msg = LlmMessage(
            conversation_id=conv.id,
            role="system",
            content=f"Manuell auf {formatted_value} gesetzt am {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M')} (Quelle: {source_hint})",
            source="manual",
        )
        db.add(system_msg)
        db.commit()
    except Exception as e:
        logger.warning("Failed to log manual override as system message for %s/%s: %s", company_id, value_key, e)

    return result_cv
