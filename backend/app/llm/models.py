from datetime import datetime
from uuid import UUID, uuid4
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db import Base


class LlmConversation(Base):
    __tablename__ = "llm_conversations"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    value_key: Mapped[str] = mapped_column(String(64), nullable=False)
    period_type: Mapped[str] = mapped_column(String(16), nullable=False, default="SNAPSHOT")
    period_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    messages: Mapped[list["LlmMessage"]] = relationship(back_populates="conversation", order_by="LlmMessage.created_at")

    # Uniqueness across (company, value, period) enforced by partial unique index
    # in migration (COALESCE handles nullable period_year).


class LlmMessage(Base):
    __tablename__ = "llm_messages"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("llm_conversations.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    score_suggestion: Mapped[Decimal | None] = mapped_column(Numeric(25, 6), nullable=True)
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[LlmConversation] = relationship(back_populates="messages")
