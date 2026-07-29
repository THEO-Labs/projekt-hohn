from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.companies.isin import accounting_standard, validate_isin


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    ticker: str = Field(min_length=1, max_length=32)
    isin: str | None = Field(default=None, max_length=12)
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("isin")
    @classmethod
    def isin_must_be_valid(cls, v: str | None) -> str | None:
        if v and not validate_isin(v):
            raise ValueError("Invalid ISIN")
        return v


class CompanyUpdate(BaseModel):
    name: str | None = None
    ticker: str | None = None
    isin: str | None = None
    currency: str | None = None
    fiscal_year_end_month: int | None = None
    fiscal_year_end_day: int | None = None

    @field_validator("isin")
    @classmethod
    def isin_must_be_valid(cls, v: str | None) -> str | None:
        if v and not validate_isin(v):
            raise ValueError("Invalid ISIN")
        return v


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    portfolio_id: UUID
    name: str
    ticker: str
    isin: str | None
    currency: str
    fiscal_year_end_month: int | None = None
    fiscal_year_end_day: int | None = None
    # Naechster bekannter Earnings-Termin (via Daily-Refresh gepflegt)
    next_earnings_date: date | None = None
    created_at: datetime
    updated_at: datetime

    # Berechnetes Response-Feld (kein DB-Feld): US-Filer -> "US-GAAP", sonst "IFRS"
    @computed_field  # type: ignore[prop-decorator]
    @property
    def accounting_standard(self) -> str:
        return accounting_standard(self.isin)


class CompanyLookupOut(BaseModel):
    name: str | None
    ticker: str | None
    isin: str | None
    currency: str | None
