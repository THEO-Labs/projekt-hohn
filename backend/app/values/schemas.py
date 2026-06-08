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
    numeric_value_adjusted: Decimal | None = None
    adjustments_note: str | None = None
    adjustments_source: str | None = None
    text_value: str | None
    currency: str | None
    source_name: str | None
    source_link: str | None
    fetched_at: datetime | None
    manually_overridden: bool
    forecast_alternates: list[dict] | None = None
    primary_method: str | None = None
    last_refresh_attempt: datetime | None = None

    model_config = {"from_attributes": True}


class RefreshRequest(BaseModel):
    keys: list[str]
    period_type: str = "FY"
    period_year: int | None = None
    # True = nur Stammdaten-Keys (Live-Snapshot via API) refreshen, kein
    # FY-Fundamental-Web-Research. Use-Case: "Daily Numbers"-Button — User
    # will MCap/Stock-Price tagesaktuell ohne teure Web-Recherche.
    stammdaten_only: bool = False


class OverrideRequest(BaseModel):
    numeric_value: Decimal | None = None
    text_value: str | None = None
    source_name: str | None = None
