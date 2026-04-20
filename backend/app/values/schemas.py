from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, model_validator

from app.values.always_current import ALWAYS_CURRENT_KEYS
from app.values.currency_keys import CURRENCY_KEYS


class ValueDefinitionOut(BaseModel):
    key: str
    label_de: str
    label_en: str
    category: str
    source_type: str
    data_type: str
    unit: str | None
    sort_order: int
    always_current: bool = False
    is_currency: bool = False

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def set_flags(self) -> "ValueDefinitionOut":
        self.always_current = self.key in ALWAYS_CURRENT_KEYS
        self.is_currency = self.key in CURRENCY_KEYS
        return self


class CompanyValueOut(BaseModel):
    id: UUID
    company_id: UUID
    value_key: str
    period_year: int | None
    period_type: str
    is_forecast: bool
    numeric_value: Decimal | None
    text_value: str | None
    currency: str | None
    source_name: str | None
    source_link: str | None
    fetched_at: datetime | None
    manually_overridden: bool

    model_config = {"from_attributes": True}


class RefreshRequest(BaseModel):
    keys: list[str]
    period_type: str = "SNAPSHOT"
    period_year: int | None = None


class OverrideRequest(BaseModel):
    numeric_value: Decimal | None = None
    text_value: str | None = None
    source_name: str | None = None
