import enum
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SaEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DocumentType(str, enum.Enum):
    ANNUAL_REPORT = "ANNUAL_REPORT"
    FORM_10K = "FORM_10K"
    FORM_20F = "FORM_20F"
    EARNINGS_RELEASE = "EARNINGS_RELEASE"
    QUARTERLY_REPORT = "QUARTERLY_REPORT"
    INVESTOR_PRESENTATION = "INVESTOR_PRESENTATION"
    OTHER = "OTHER"


class PeriodCoverage(str, enum.Enum):
    FY = "FY"
    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    Q4 = "Q4"
    H1 = "H1"
    H2 = "H2"


class ExtractionStatus(str, enum.Enum):
    PENDING = "PENDING"
    EXTRACTING = "EXTRACTING"
    DONE = "DONE"
    FAILED = "FAILED"


class IRDocument(Base):
    __tablename__ = "ir_documents"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_type: Mapped[DocumentType] = mapped_column(SaEnum(DocumentType), nullable=False)
    period_coverage: Mapped[PeriodCoverage] = mapped_column(SaEnum(PeriodCoverage), nullable=False)
    period_year: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    uploaded_by_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    extraction_status: Mapped[ExtractionStatus] = mapped_column(
        SaEnum(ExtractionStatus), nullable=False, default=ExtractionStatus.PENDING
    )
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    extraction_results: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
