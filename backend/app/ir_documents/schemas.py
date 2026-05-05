from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class IRDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    document_type: str
    period_coverage: str
    period_year: int
    display_name: str
    original_filename: str
    size_bytes: int | None
    mime_type: str | None
    uploaded_at: datetime
    extraction_status: str
    extracted_at: datetime | None
    extraction_error: str | None
    notes: str | None


class IRDocumentUpdate(BaseModel):
    display_name: str | None = None
    document_type: str | None = None
    period_coverage: str | None = None
    period_year: int | None = None
    notes: str | None = None
