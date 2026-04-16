from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.models import User
from app.companies.lookup import lookup_by_isin, lookup_by_ticker
from app.companies.models import Company
from app.companies.schemas import CompanyCreate, CompanyLookupOut, CompanyOut, CompanyUpdate
from app.db import get_db
from app.portfolios.models import Portfolio

portfolio_scoped = APIRouter(prefix="/api/portfolios/{portfolio_id}/companies", tags=["companies"])
company_router = APIRouter(prefix="/api/companies", tags=["companies"])
lookup_router = APIRouter(prefix="/api/company-lookup", tags=["companies"])


def _get_owned_portfolio(db: Session, user: User, portfolio_id: UUID) -> Portfolio:
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).one_or_none()
    if not portfolio or portfolio.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found")
    return portfolio


def _get_owned_company(db: Session, user: User, company_id: UUID) -> Company:
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    _get_owned_portfolio(db, user, company.portfolio_id)
    return company


@portfolio_scoped.get("", response_model=list[CompanyOut])
def list_companies(
    portfolio_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[Company]:
    _get_owned_portfolio(db, user, portfolio_id)
    return (
        db.query(Company)
        .filter(Company.portfolio_id == portfolio_id)
        .order_by(Company.created_at)
        .all()
    )


@portfolio_scoped.post("", response_model=CompanyOut, status_code=status.HTTP_201_CREATED)
def create_company(
    portfolio_id: UUID,
    payload: CompanyCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Company:
    _get_owned_portfolio(db, user, portfolio_id)
    company = Company(portfolio_id=portfolio_id, **payload.model_dump())
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


@company_router.patch("/{company_id}", response_model=CompanyOut)
def update_company(
    company_id: UUID,
    payload: CompanyUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Company:
    company = _get_owned_company(db, user, company_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(company, key, value)
    db.commit()
    db.refresh(company)
    return company


@company_router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_company(
    company_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    company = _get_owned_company(db, user, company_id)
    db.delete(company)
    db.commit()


@lookup_router.get("", response_model=CompanyLookupOut)
async def company_lookup(
    isin: str | None = Query(default=None),
    ticker: str | None = Query(default=None),
    user: User = Depends(current_user),
) -> CompanyLookupOut:
    if (isin is None) == (ticker is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Exactly one of 'isin' or 'ticker' must be provided",
        )
    if isin:
        result = await lookup_by_isin(isin)
    else:
        result = await lookup_by_ticker(ticker)

    if result is None:
        return CompanyLookupOut(name=None, ticker=None, isin=None, currency=None)
    return CompanyLookupOut(**result)
