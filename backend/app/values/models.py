import enum
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy import Enum as SaEnum
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ValueCategory(str, enum.Enum):
    STAMMDATEN = "STAMMDATEN"
    CASH_DEBT = "CASH_DEBT"
    BUYBACKS_SBC = "BUYBACKS_SBC"
    FCF = "FCF"
    NI_GROWTH = "NI_GROWTH"
    DELTA_ND = "DELTA_ND"
    DIVIDENDS = "DIVIDENDS"
    HOHN_RETURN = "HOHN_RETURN"


class SourceType(str, enum.Enum):
    USER_INPUT = "USER_INPUT"
    API = "API"
    CALCULATED = "CALCULATED"
    QUALITATIVE = "QUALITATIVE"


class DataType(str, enum.Enum):
    NUMERIC = "NUMERIC"
    TEXT = "TEXT"
    FACTOR = "FACTOR"


class ValueDefinition(Base):
    __tablename__ = "value_definitions"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    label_de: Mapped[str] = mapped_column(String(128), nullable=False)
    label_en: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[ValueCategory] = mapped_column(SaEnum(ValueCategory), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(SaEnum(SourceType), nullable=False)
    data_type: Mapped[DataType] = mapped_column(
        SaEnum(DataType), nullable=False, default=DataType.NUMERIC
    )
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CompanyValue(Base):
    __tablename__ = "company_values"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    value_key: Mapped[str] = mapped_column(
        String(64), ForeignKey("value_definitions.key"), nullable=False
    )
    period_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    period_type: Mapped[str] = mapped_column(String(16), nullable=False, default="SNAPSHOT")
    is_forecast: Mapped[bool] = mapped_column(Boolean, default=False)
    numeric_value: Mapped[Decimal | None] = mapped_column(Numeric(25, 6), nullable=True)
    text_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    manually_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
