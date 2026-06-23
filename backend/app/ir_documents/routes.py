"""IR-Documents API: lean archive-only mode.

PDF-Auto-Extraction-Pipeline wurde abgeschaltet (siehe commit-history). PDFs
werden weiterhin akzeptiert und als Audit-Archive gespeichert, aber kein
Claude-Extraction-Call wird mehr ausgeloest. Daten kommen jetzt aus
EDGAR/ESEF/Yahoo-Provider-Chain und Claude+Web-Fallback (siehe
app.providers.{edgar,esef,yahoo} und app.llm.research).

Endpoints:
  POST   /companies/{id}/ir-documents          Upload PDF (Archive)
  GET    /companies/{id}/ir-documents          Liste
  PATCH  /companies/{id}/ir-documents/{doc_id} Metadata-Update
  DELETE /companies/{id}/ir-documents/{doc_id} Loeschen
  GET    /companies/{id}/ir-documents/{doc_id}/download
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.models import User
from app.companies.models import Company
from app.db import get_db
from app.ir_documents.models import (
    DocumentType,
    ExtractionStatus,
    IRDocument,
    PeriodCoverage,
)
from app.ir_documents.schemas import IRDocumentOut, IRDocumentUpdate
from app.ir_documents.storage import (
    delete_file,
    ensure_root,
    storage_path_for,
    write_bytes,
)
from app.portfolios.models import Portfolio

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/companies", tags=["ir-documents"])

MAX_PDF_SIZE = 50 * 1024 * 1024  # 50 MB


def _get_owned_company(db: Session, user: User, company_id: UUID) -> Company:
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    portfolio = db.query(Portfolio).filter(Portfolio.id == company.portfolio_id).one_or_none()
    if not portfolio or portfolio.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.post("/{company_id}/ir-documents", response_model=IRDocumentOut, status_code=201)
async def upload_ir_document(
    company_id: UUID,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    period_coverage: str = Form(...),
    period_year: int = Form(...),
    display_name: str = Form(...),
    notes: str | None = Form(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> IRDocument:
    _get_owned_company(db, user, company_id)

    try:
        doc_type = DocumentType(document_type)
        period_cov = PeriodCoverage(period_coverage)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid enum value: {e}")

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files allowed")

    contents = await file.read()
    if len(contents) > MAX_PDF_SIZE:
        raise HTTPException(status_code=413, detail=f"PDF exceeds max size of {MAX_PDF_SIZE // 1024 // 1024} MB")
    if len(contents) < 100:
        raise HTTPException(status_code=400, detail="Uploaded file looks empty/corrupted")

    ensure_root()
    doc_id = uuid4()
    path = storage_path_for(company_id, doc_id, file.filename)
    write_bytes(path, contents)

    doc = IRDocument(
        id=doc_id,
        company_id=company_id,
        document_type=doc_type,
        period_coverage=period_cov,
        period_year=period_year,
        display_name=display_name,
        original_filename=file.filename,
        storage_path=str(path),
        size_bytes=len(contents),
        mime_type=file.content_type or "application/pdf",
        uploaded_at=datetime.now(timezone.utc),
        uploaded_by_user_id=user.id,
        # Archive-Mode: kein Extraktion-Worker mehr, Status direkt SUCCESS.
        extraction_status=ExtractionStatus.SUCCESS,
        extraction_error="Archive-only (Auto-Extraction deaktiviert)",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


@router.get("/{company_id}/ir-documents", response_model=list[IRDocumentOut])
def list_ir_documents(
    company_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[IRDocument]:
    _get_owned_company(db, user, company_id)
    rows = (
        db.query(IRDocument)
        .filter(IRDocument.company_id == company_id)
        .order_by(IRDocument.period_year.desc(), IRDocument.period_coverage, IRDocument.uploaded_at.desc())
        .all()
    )
    return rows


@router.patch("/{company_id}/ir-documents/{doc_id}", response_model=IRDocumentOut)
def update_ir_document(
    company_id: UUID,
    doc_id: UUID,
    payload: IRDocumentUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> IRDocument:
    _get_owned_company(db, user, company_id)
    doc = db.query(IRDocument).filter(IRDocument.id == doc_id, IRDocument.company_id == company_id).one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    data = payload.model_dump(exclude_unset=True)
    if "document_type" in data and data["document_type"] is not None:
        try:
            doc.document_type = DocumentType(data.pop("document_type"))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid document_type: {e}")
    if "period_coverage" in data and data["period_coverage"] is not None:
        try:
            doc.period_coverage = PeriodCoverage(data.pop("period_coverage"))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid period_coverage: {e}")
    for key, value in data.items():
        if value is not None:
            setattr(doc, key, value)
    db.commit()
    db.refresh(doc)
    return doc


@router.delete("/{company_id}/ir-documents/{doc_id}", status_code=204)
def delete_ir_document(
    company_id: UUID,
    doc_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    _get_owned_company(db, user, company_id)
    doc = db.query(IRDocument).filter(IRDocument.id == doc_id, IRDocument.company_id == company_id).one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.storage_path:
        delete_file(Path(doc.storage_path))
    db.delete(doc)
    db.commit()


@router.get("/{company_id}/ir-documents/{doc_id}/download")
def download_ir_document(
    company_id: UUID,
    doc_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    _get_owned_company(db, user, company_id)
    doc = db.query(IRDocument).filter(IRDocument.id == doc_id, IRDocument.company_id == company_id).one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    path = Path(doc.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")
    return FileResponse(
        path,
        media_type=doc.mime_type or "application/pdf",
        filename=doc.original_filename,
    )
