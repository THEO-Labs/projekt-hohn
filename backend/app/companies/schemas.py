from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.companies.isin import validate_isin


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
    created_at: datetime
    updated_at: datetime


class CompanyLookupOut(BaseModel):
    name: str | None
    ticker: str | None
    isin: str | None
    currency: str | None
