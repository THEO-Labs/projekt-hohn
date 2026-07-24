from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PortfolioMember(Base):
    """Freigeschaltete Nutzer eines Portfolios (zusaetzlich zum Owner).

    Mitglieder haben vollen Lese-/Schreibzugriff auf Portfolio-Inhalte;
    nur der Owner kann das Portfolio selbst loeschen.
    """

    __tablename__ = "portfolio_members"

    portfolio_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def has_portfolio_access(db, user_id: UUID, portfolio: "Portfolio | None") -> bool:
    """Owner ODER Mitglied. Zentraler Check fuer alle Routen."""
    if portfolio is None:
        return False
    if portfolio.owner_user_id == user_id:
        return True
    return (
        db.query(PortfolioMember)
        .filter(
            PortfolioMember.portfolio_id == portfolio.id,
            PortfolioMember.user_id == user_id,
        )
        .one_or_none()
        is not None
    )
