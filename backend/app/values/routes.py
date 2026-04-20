import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.config import settings
from app.auth.deps import current_user
from app.auth.models import User
from app.calculations.engine import CALCULATED_KEYS, calculate_all
from app.companies.models import Company
from app.db import get_db
from app.portfolios.models import Portfolio
from app.llm.claude import research_value, validate_claude_value
from app.providers.registry import get_providers
from app.values.always_current import ALWAYS_CURRENT_KEYS
from app.values.currency_keys import CURRENCY_KEYS
from app.values.models import CompanyValue, SourceType, ValueDefinition
from app.values.progress import cleanup_old_jobs, finish_job, get_job, mark_success, start_job, update_job
from app.values.schemas import CompanyValueOut, OverrideRequest, RefreshRequest, ValueDefinitionOut

catalog_router = APIRouter(prefix="/api/value-definitions", tags=["values"])
values_router = APIRouter(prefix="/api/companies", tags=["values"])


def _run_and_persist_calculations(
    db: Session,
    company_id: UUID,
    period_type: str,
    period_year: int | None,
) -> list[CompanyValue]:
    q = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.period_type == period_type,
        )
    )
    if period_year is not None:
        q = q.filter(CompanyValue.period_year == period_year)
    else:
        q = q.filter(CompanyValue.period_year.is_(None))
    existing_rows = q.all()

    values_map: dict[str, Decimal | None] = {}
    for row in existing_rows:
        if row.numeric_value is not None:
            values_map[row.value_key] = row.numeric_value

    snapshot_rows = db.query(CompanyValue).filter(
        CompanyValue.company_id == company_id,
        CompanyValue.period_type == "SNAPSHOT",
        CompanyValue.period_year.is_(None),
        CompanyValue.value_key.in_(ALWAYS_CURRENT_KEYS),
    ).all()
    for row in snapshot_rows:
        if row.value_key not in values_map and row.numeric_value is not None:
            values_map[row.value_key] = row.numeric_value

    previous_values: dict[str, Decimal | None] | None = None
    if period_type == "FY" and period_year is not None:
        prev_rows = db.query(CompanyValue).filter(
            CompanyValue.company_id == company_id,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == period_year - 1,
        ).all()
        previous_values = {
            row.value_key: row.numeric_value
            for row in prev_rows
            if row.numeric_value is not None
        }

    calc_results = calculate_all(values_map, previous_values)

    by_key = {row.value_key: row for row in existing_rows}
    updated: list[CompanyValue] = []

    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    company_currency = company.currency if company else None

    for key, value in calc_results.items():
        if key not in CALCULATED_KEYS:
            continue

        existing = by_key.get(key)
        if existing and existing.manually_overridden:
            updated.append(existing)
            continue

        if value is None and existing is None:
            continue

        calc_currency = company_currency if key in CURRENCY_KEYS else None

        if existing:
            existing.numeric_value = value
            existing.source_name = "Calculated"
            existing.source_link = None
            existing.fetched_at = datetime.now(timezone.utc)
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

    try:
        if existing:
            existing.numeric_value = numeric_value
            existing.text_value = text_value
            existing.currency = result.currency
            existing.source_name = result.source_name
            existing.source_link = result.source_link
            existing.fetched_at = datetime.now(timezone.utc)
            updated.append(existing)
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
            db.add(cv)
            updated.append(cv)
        db.flush()
    except Exception as e:
        logger.error("DB save failed for key=%s company=%s: %s", key, ticker, e)
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
        db.commit()
        db.refresh(existing)
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
        db.commit()
        db.refresh(cv)
        result_cv = cv

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
