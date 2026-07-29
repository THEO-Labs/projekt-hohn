from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class PortfolioUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class PortfolioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime


class MemberAdd(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class MemberOut(BaseModel):
    user_id: UUID
    email: str
    is_owner: bool
