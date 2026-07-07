import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

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


_TICKER_SUFFIX_CURRENCY: dict[str, str] = {
    # Yahoo-Ticker-Suffix → Reporting-Currency. Schuetzt vor versehentlichem
    # USD-Default fuer EU/UK/JP-Listings (z.B. AIR.PA = Airbus reportet in EUR,
    # nicht USD trotz USD-Listing-Doppelnotierung).
    ".DE": "EUR", ".F": "EUR", ".PA": "EUR", ".AS": "EUR", ".MI": "EUR",
    ".MC": "EUR", ".LS": "EUR", ".VI": "EUR", ".HE": "EUR", ".BR": "EUR",
    ".IR": "EUR",
    ".L": "GBP", ".LON": "GBP",
    ".SW": "CHF",
    ".CO": "DKK", ".OL": "NOK", ".ST": "SEK",
    ".T": "JPY", ".TYO": "JPY",
    ".HK": "HKD",
    ".SS": "CNY", ".SZ": "CNY",
    ".KS": "KRW", ".KQ": "KRW",
    ".AX": "AUD",
    ".TO": "CAD", ".V": "CAD",
}


def _suggest_currency_from_ticker(ticker: str) -> str | None:
    """Heuristik: aus Yahoo-Ticker-Suffix (z.B. '.DE', '.PA', '.L') die
    Reporting-Currency ableiten. Returns None wenn US-Listing oder unbekannt."""
    if not ticker or "." not in ticker:
        return None
    suffix = "." + ticker.split(".")[-1].upper()
    return _TICKER_SUFFIX_CURRENCY.get(suffix)


@portfolio_scoped.post("", response_model=CompanyOut, status_code=status.HTTP_201_CREATED)
def create_company(
    portfolio_id: UUID,
    payload: CompanyCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Company:
    _get_owned_portfolio(db, user, portfolio_id)
    data = payload.model_dump()
    # Currency-Sanity: wenn User USD eingibt aber Ticker-Suffix klare EU/UK/JP-
    # Heimatboerse signalisiert, ueberschreiben — schuetzt vor Currency-Mismatch
    # in Cross-Year-Aggregaten (z.B. Airbus.PA mit USD => Web liefert EUR-Werte
    # die als USD persistiert werden).
    suggested = _suggest_currency_from_ticker(data.get("ticker", ""))
    if suggested and data.get("currency") != suggested:
        logger.info(
            "Currency-Auto-Detect: %s ticker -> %s (User-Eingabe %s ueberschrieben)",
            data.get("ticker"), suggested, data.get("currency"),
        )
        data["currency"] = suggested
    # Auto-detect fiscal_year_end from Yahoo when the user didn't specify.
    # Without this a US-listed non-December filer (Visa=Sep, Microsoft=Jun,
    # Broadcom=Oct, Apple=Sep) silently degrades: _q_end_date returns None,
    # EDGAR-Q-Backfill is skipped, all Q values come from Claude estimates
    # instead of 10-Q actuals.
    if not data.get("fiscal_year_end_month") or not data.get("fiscal_year_end_day"):
        try:
            from app.providers.yahoo import YahooFinanceProvider
            detected = YahooFinanceProvider().detect_fiscal_year_end(data.get("ticker", ""))
        except Exception as exc:
            logger.warning("FY-End auto-detect failed for %s: %s", data.get("ticker"), exc)
            detected = None
        if detected:
            data["fiscal_year_end_month"], data["fiscal_year_end_day"] = detected
            logger.info(
                "FY-End auto-detected for %s: %02d/%02d",
                data.get("ticker"), detected[0], detected[1],
            )
    company = Company(portfolio_id=portfolio_id, **data)
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
