from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.models import User
from app.calculations.engine import CALCULATED_KEYS, calculate_all
from app.companies.models import Company
from app.db import get_db
from app.portfolios.models import Portfolio
from app.providers.registry import get_providers
from app.values.models import CompanyValue, ValueDefinition
from app.values.schemas import CompanyValueOut, OverrideRequest, RefreshRequest, ValueDefinitionOut

catalog_router = APIRouter(prefix="/api/value-definitions", tags=["values"])
values_router = APIRouter(prefix="/api/companies", tags=["values"])


def _run_and_persist_calculations(
    db: Session,
    company_id: UUID,
    period_type: str,
    period_year: int | None,
) -> list[CompanyValue]:
    existing_rows = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.period_type == period_type,
            CompanyValue.period_year == period_year,
        )
        .all()
    )

    values_map: dict[str, Decimal | None] = {}
    for row in existing_rows:
        if row.numeric_value is not None:
            values_map[row.value_key] = row.numeric_value

    calc_results = calculate_all(values_map)

    by_key = {row.value_key: row for row in existing_rows}
    updated: list[CompanyValue] = []

    for key, value in calc_results.items():
        if key not in CALCULATED_KEYS:
            continue

        existing = by_key.get(key)
        if existing and existing.manually_overridden:
            updated.append(existing)
            continue

        if value is None and existing is None:
            continue

        if existing:
            existing.numeric_value = value
            existing.source_name = "Calculated"
            existing.source_link = None
            existing.fetched_at = datetime.now(timezone.utc)
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
def list_value_definitions(db: Session = Depends(get_db)) -> list[ValueDefinition]:
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


@values_router.post("/{company_id}/values/refresh", response_model=list[CompanyValueOut])
def refresh_company_values(
    company_id: UUID,
    payload: RefreshRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[CompanyValue]:
    company = _get_owned_company(db, user, company_id)
    ticker = company.ticker
    updated = []

    for key in payload.keys:
        providers = get_providers(key)
        if not providers:
            continue

        result = None
        for provider in providers:
            try:
                result = provider.fetch(ticker, key, payload.period_type, payload.period_year)
                if result is not None:
                    break
            except Exception:
                continue

        if result is None:
            continue

        existing = (
            db.query(CompanyValue)
            .filter(
                CompanyValue.company_id == company_id,
                CompanyValue.value_key == key,
                CompanyValue.period_type == payload.period_type,
                CompanyValue.period_year == payload.period_year,
            )
            .one_or_none()
        )

        if existing and existing.manually_overridden:
            updated.append(existing)
            continue

        numeric_value: Decimal | None = None
        text_value: str | None = None
        if isinstance(result.value, Decimal):
            numeric_value = result.value
        elif result.value is not None:
            text_value = str(result.value)

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
                period_type=payload.period_type,
                period_year=payload.period_year,
                numeric_value=numeric_value,
                text_value=text_value,
                currency=result.currency,
                source_name=result.source_name,
                source_link=result.source_link,
                fetched_at=datetime.now(timezone.utc),
            )
            db.add(cv)
            updated.append(cv)

    db.commit()

    _run_and_persist_calculations(db, company_id, payload.period_type, payload.period_year)
    db.commit()

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
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CompanyValue:
    _get_owned_company(db, user, company_id)

    existing = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == value_key,
            CompanyValue.period_type == "SNAPSHOT",
            CompanyValue.period_year == None,  # noqa: E711
        )
        .one_or_none()
    )

    if existing:
        if payload.numeric_value is not None:
            existing.numeric_value = payload.numeric_value
        if payload.text_value is not None:
            existing.text_value = payload.text_value
        if payload.source_name is not None:
            existing.source_name = payload.source_name
        existing.manually_overridden = True
        db.commit()
        db.refresh(existing)
        return existing

    cv = CompanyValue(
        id=uuid4(),
        company_id=company_id,
        value_key=value_key,
        period_type="SNAPSHOT",
        period_year=None,
        numeric_value=payload.numeric_value,
        text_value=payload.text_value,
        source_name=payload.source_name,
        manually_overridden=True,
    )
    db.add(cv)
    db.commit()
    db.refresh(cv)
    return cv
