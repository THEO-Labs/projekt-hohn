from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    portfolio_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    fiscal_year_end_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fiscal_year_end_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Naechster bekannter Earnings-Termin (Yahoo-Kalender), 24h-TTL via
    # earnings_checked_at im Daily-Refresh (stammdaten_only).
    next_earnings_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    earnings_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
