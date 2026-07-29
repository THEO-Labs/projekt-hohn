from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.models import User
from app.db import get_db
from app.portfolios.models import Portfolio, PortfolioMember, has_portfolio_access
from app.portfolios.schemas import MemberAdd, MemberOut, PortfolioCreate, PortfolioOut, PortfolioUpdate

router = APIRouter(prefix="/api/portfolios", tags=["portfolios"])


@router.get("", response_model=list[PortfolioOut])
def list_portfolios(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[Portfolio]:
    member_ids = db.query(PortfolioMember.portfolio_id).filter(PortfolioMember.user_id == user.id)
    return (
        db.query(Portfolio)
        .filter((Portfolio.owner_user_id == user.id) | (Portfolio.id.in_(member_ids)))
        .order_by(Portfolio.created_at)
        .all()
    )


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
    if not portfolio or not has_portfolio_access(db, user.id, portfolio):
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


def _owned_portfolio(db: Session, user: User, portfolio_id: UUID) -> Portfolio:
    p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).one_or_none()
    if not p or not has_portfolio_access(db, user.id, p):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found")
    return p


def _owner_only(portfolio: Portfolio, user: User) -> None:
    """Mitglieder-Verwaltung ist dem Owner vorbehalten."""
    if portfolio.owner_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nur der Owner kann Mitglieder verwalten",
        )


@router.get("/{portfolio_id}/members", response_model=list[MemberOut])
def list_members(
    portfolio_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[MemberOut]:
    """Owner und Mitglieder sehen, wer Zugriff hat. Owner steht zuerst."""
    portfolio = _owned_portfolio(db, user, portfolio_id)
    owner = db.query(User).filter(User.id == portfolio.owner_user_id).one()
    rows = (
        db.query(PortfolioMember, User)
        .join(User, User.id == PortfolioMember.user_id)
        .filter(PortfolioMember.portfolio_id == portfolio.id)
        .order_by(PortfolioMember.created_at)
        .all()
    )
    return [MemberOut(user_id=owner.id, email=owner.email, is_owner=True)] + [
        MemberOut(user_id=u.id, email=u.email, is_owner=False) for _, u in rows
    ]


@router.post("/{portfolio_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def add_member(
    portfolio_id: UUID,
    payload: MemberAdd,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MemberOut:
    portfolio = _owned_portfolio(db, user, portfolio_id)
    _owner_only(portfolio, user)

    email = payload.email.strip().lower()
    target = db.query(User).filter(func.lower(User.email) == email).order_by(User.email).first()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User nicht gefunden — muss erst angelegt werden",
        )
    if target.id == portfolio.owner_user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Owner hat bereits vollen Zugriff",
        )
    existing = (
        db.query(PortfolioMember)
        .filter(PortfolioMember.portfolio_id == portfolio.id, PortfolioMember.user_id == target.id)
        .one_or_none()
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User ist bereits Mitglied")

    db.add(PortfolioMember(portfolio_id=portfolio.id, user_id=target.id))
    db.commit()
    return MemberOut(user_id=target.id, email=target.email, is_owner=False)


@router.delete("/{portfolio_id}/members/{member_user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    portfolio_id: UUID,
    member_user_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    portfolio = _owned_portfolio(db, user, portfolio_id)
    _owner_only(portfolio, user)

    if member_user_id == portfolio.owner_user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Owner kann nicht entfernt werden",
        )
    membership = (
        db.query(PortfolioMember)
        .filter(PortfolioMember.portfolio_id == portfolio.id, PortfolioMember.user_id == member_user_id)
        .one_or_none()
    )
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mitglied nicht gefunden")
    db.delete(membership)
    db.commit()


@router.post("/{portfolio_id}/full-recompute")
def start_full_recompute(
    portfolio_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Startet den portfolio-weiten Full Recompute (Queue, 3 Firmen parallel).

    Idempotent: laeuft bereits ein Batch fuer dieses Portfolio, wird dessen
    Status zurueckgegeben statt ein zweiter gestartet.
    """
    from app.values.batch import start_portfolio_recompute

    _owned_portfolio(db, user, portfolio_id)
    return start_portfolio_recompute(portfolio_id, user.id)


@router.post("/{portfolio_id}/smart-recompute")
def start_smart_recompute(
    portfolio_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Smart Recompute: nur Firmen mit neuen Earnings seit dem letzten
    Two-Stage-Lauf (plus nie gerechnete Firmen). Antwort enthaelt 'selected'
    mit den Tickern der ausgewaehlten Firmen; leere Auswahl -> done/total=0.
    """
    from app.values.batch import start_portfolio_recompute

    _owned_portfolio(db, user, portfolio_id)
    return start_portfolio_recompute(portfolio_id, user.id, only_stale=True)


@router.get("/{portfolio_id}/full-recompute-status")
def full_recompute_status(
    portfolio_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    from app.values.batch import get_batch_status

    _owned_portfolio(db, user, portfolio_id)
    return get_batch_status(portfolio_id)
