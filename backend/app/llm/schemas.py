from datetime import datetime
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class LlmMessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    score_suggestion: Decimal | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ChatHistoryOut(BaseModel):
    conversation_id: UUID
    messages: list[LlmMessageOut]


class AnalyzeResponse(BaseModel):
    conversation_id: UUID
    message: LlmMessageOut


class ChatRequest(BaseModel):
    message: str
