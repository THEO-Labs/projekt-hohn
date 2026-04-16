from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.models import User
from app.db import get_db
from app.portfolios.models import Portfolio
from app.portfolios.schemas import PortfolioCreate, PortfolioOut, PortfolioUpdate

router = APIRouter(prefix="/api/portfolios", tags=["portfolios"])


@router.get("", response_model=list[PortfolioOut])
def list_portfolios(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[Portfolio]:
    return db.query(Portfolio).filter(Portfolio.owner_user_id == user.id).order_by(Portfolio.created_at).all()


@router.post("", response_model=PortfolioOut, status_code=status.HTTP_201_CREATED)
def create_portfolio(
    payload: PortfolioCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Portfolio:
    portfolio = Portfolio(name=payload.name, owner_user_id=user.id)
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return portfolio


def _get_owned(db: Session, user: User, portfolio_id: UUID) -> Portfolio:
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).one_or_none()
    if not portfolio or portfolio.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found")
    return portfolio


@router.patch("/{portfolio_id}", response_model=PortfolioOut)
def update_portfolio(
    portfolio_id: UUID,
    payload: PortfolioUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Portfolio:
    portfolio = _get_owned(db, user, portfolio_id)
    portfolio.name = payload.name
    db.commit()
    db.refresh(portfolio)
    return portfolio


@router.delete("/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_portfolio(
    portfolio_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    portfolio = _get_owned(db, user, portfolio_id)
    db.delete(portfolio)
    db.commit()
