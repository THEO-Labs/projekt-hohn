from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    ticker: str = Field(min_length=1, max_length=32)
    isin: str | None = Field(default=None, max_length=12)
    currency: str = Field(min_length=3, max_length=3)


class CompanyUpdate(BaseModel):
    name: str | None = None
    ticker: str | None = None
    isin: str | None = None
    currency: str | None = None


class CompanyOut(BaseModel):
    id: UUID
    portfolio_id: UUID
    name: str
    ticker: str
    isin: str | None
    currency: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
