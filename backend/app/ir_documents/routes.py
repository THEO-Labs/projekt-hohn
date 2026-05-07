import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.models import User
from app.companies.models import Company
from app.db import SessionLocal, get_db
from app.ir_documents.extraction import extract_values_from_pdf
from app.ir_documents.models import (
    DocumentType,
    ExtractionStatus,
    IRDocument,
    PeriodCoverage,
)
from app.ir_documents.queue import queue_position, wake_worker
from app.ir_documents.schemas import IRDocumentOut, IRDocumentUpdate
from app.ir_documents.storage import (
    delete_file,
    ensure_root,
    storage_path_for,
    write_bytes,
)
from app.portfolios.models import Portfolio
from app.values.models import CompanyValue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/companies", tags=["ir-documents"])

MAX_PDF_SIZE = 50 * 1024 * 1024  # 50 MB


def _get_owned_company(db: Session, user: User, company_id: UUID) -> Company:
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    portfolio = db.query(Portfolio).filter(Portfolio.id == company.portfolio_id).one_or_none()
    if not portfolio or portfolio.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company


def _post_extraction_web_fallback(
    db: Session,
    doc: IRDocument,
    results: dict[str, dict],
    company: Company,
    source_link: str,
) -> int:
    """Für jeden Key in results mit value=None: Claude-Web-Recherche versuchen
    und bei Erfolg die CompanyValue-Row mit dem Web-Wert auffüllen.

    Source-Name wird zu 'PDF leer → Claude-Recherche: <quelle>' damit der
    User im Drilldown sieht dass der Wert nicht aus dem PDF kam.
    Returns Anzahl erfolgreich ausgefüllter Werte.
    """
    from app.config import settings
    if not settings.anthropic_api_key:
        return 0
    from app.llm.claude import research_value, validate_claude_value
    from app.values.currency_keys import CURRENCY_KEYS
    from app.values.models import SourceType, ValueDefinition

    period_type = doc.period_coverage.value
    period_year = doc.period_year
    filled = 0

    for key, info in results.items():
        if info.get("value") is not None:
            continue  # PDF hat einen Wert — nichts nachfüllen
        vd = db.query(ValueDefinition).filter(ValueDefinition.key == key).one_or_none()
        if vd is None or vd.source_type != SourceType.API:
            continue

        label = f"{vd.label_en} ({vd.label_de})"
        try:
            web_val, source, url, _user_prompt, _assistant = research_value(
                company.name, company.ticker, label, company.currency,
                period_type=period_type, period_year=period_year, value_key=key,
            )
        except Exception as e:
            logger.warning("Post-extraction web fallback %s/%s/%s/%s failed: %s",
                           company.ticker, key, period_type, period_year, e)
            continue

        if web_val is None:
            continue
        web_val = validate_claude_value(key, web_val)
        if web_val is None:
            continue

        existing = (
            db.query(CompanyValue)
            .filter(
                CompanyValue.company_id == company.id,
                CompanyValue.value_key == key,
                CompanyValue.period_type == period_type,
                CompanyValue.period_year == period_year,
                CompanyValue.is_forecast.is_(False),
            )
            .one_or_none()
        )
        if existing and existing.manually_overridden:
            continue
        original_reason = (info.get("reason") or "")[:80]
        new_source = f"PDF leer ({original_reason}) → Claude-Recherche: {source}" if source else f"PDF leer → Claude-Recherche"
        currency = company.currency if key in CURRENCY_KEYS else None
        now = datetime.now(timezone.utc)
        if existing:
            existing.numeric_value = web_val
            existing.source_name = new_source[:512]
            existing.source_link = url or existing.source_link
            existing.currency = currency or existing.currency
            existing.fetched_at = now
            existing.from_ir_pdf = False
        else:
            db.add(CompanyValue(
                id=uuid4(),
                company_id=company.id,
                value_key=key,
                period_type=period_type,
                period_year=period_year,
                numeric_value=web_val,
                source_name=new_source[:512],
                source_link=url or source_link,
                currency=currency,
                fetched_at=now,
                from_ir_pdf=False,
            ))
        filled += 1
        logger.info("Web-fallback filled %s/%s/%s/%s = %s (src=%s)",
                    company.ticker, key, period_type, period_year, web_val, source)
    return filled


def _run_extraction_job(doc_id: UUID, company_id: UUID) -> None:
    """Background job: pull doc + company from DB, run Claude PDF extraction,
    persist values + status. Uses its own DB session."""
    db = SessionLocal()
    try:
        doc = db.query(IRDocument).filter(IRDocument.id == doc_id).one_or_none()
        if doc is None:
            return
        company = db.query(Company).filter(Company.id == company_id).one_or_none()
        if company is None:
            return

        doc.extraction_status = ExtractionStatus.EXTRACTING
        db.commit()

        try:
            results, guidance_fy, raw = extract_values_from_pdf(
                Path(doc.storage_path),
                company_name=company.name,
                document_type=doc.document_type.value,
                period_coverage=doc.period_coverage.value,
                period_year=doc.period_year,
            )
        except Exception as e:
            logger.exception("PDF extraction failed for doc %s: %s", doc_id, e)
            doc.extraction_status = ExtractionStatus.FAILED
            doc.extraction_error = str(e)[:500]
            db.commit()
            return

        # Persist as company_values where extraction succeeded.
        # Cumulative-style storage: each successful key writes a CompanyValue with from_ir_pdf=True.
        # Period-coverage maps directly to period_type (FY or Q1/Q2/...).
        json_safe_results: dict[str, dict] = {}
        for key, info in results.items():
            json_safe_results[key] = {
                k: (str(v) if isinstance(v, Decimal) else v)
                for k, v in info.items()
            }
        period_type = doc.period_coverage.value  # "FY" or "Q1" etc.
        period_year = doc.period_year
        source_link = f"/api/companies/{company_id}/ir-documents/{doc_id}/download"
        for key, info in results.items():
            value = info.get("value")
            page = info.get("page")
            currency = info.get("currency")
            reason = info.get("reason") or "im Bericht nicht gefunden"
            now = datetime.now(timezone.utc)

            existing = (
                db.query(CompanyValue)
                .filter(
                    CompanyValue.company_id == company_id,
                    CompanyValue.value_key == key,
                    CompanyValue.period_type == period_type,
                    CompanyValue.period_year == period_year,
                    CompanyValue.is_forecast.is_(False),
                )
                .one_or_none()
            )
            if existing and existing.manually_overridden:
                continue  # never overwrite manual

            if isinstance(value, Decimal):
                source_name = f"PDF: {doc.display_name}" + (f" (S.{page})" if page else "")
                if existing:
                    existing.numeric_value = value
                    existing.source_name = source_name
                    existing.source_link = source_link
                    existing.currency = currency
                    existing.fetched_at = now
                    existing.from_ir_pdf = True
                else:
                    db.add(CompanyValue(
                        id=uuid4(),
                        company_id=company_id,
                        value_key=key,
                        period_type=period_type,
                        period_year=period_year,
                        numeric_value=value,
                        source_name=source_name,
                        source_link=source_link,
                        currency=currency,
                        fetched_at=now,
                        from_ir_pdf=True,
                    ))
            else:
                # Claude tried but found no value — record an explanatory placeholder
                # so the user sees "PDF analyzed, value not found: <reason>" instead
                # of an empty cell.
                source_name = f"PDF: {doc.display_name} — kein Wert: {reason[:120]}"
                if existing:
                    if existing.from_ir_pdf:
                        # Only overwrite previous PDF-source entries, not API-fetched values
                        existing.numeric_value = None
                        existing.source_name = source_name
                        existing.source_link = source_link
                        existing.fetched_at = now
                else:
                    db.add(CompanyValue(
                        id=uuid4(),
                        company_id=company_id,
                        value_key=key,
                        period_type=period_type,
                        period_year=period_year,
                        numeric_value=None,
                        source_name=source_name,
                        source_link=source_link,
                        currency=currency,
                        fetched_at=now,
                        from_ir_pdf=True,
                    ))

        # Guidance-Werte als FY-Forecast-Rows mappen.
        #  - Q-Berichte: Guidance gilt fuer FY der gleichen period_year (Q1 2026 → FY2026)
        #  - Annual Report: 'Outlook for next year' → FY = period_year + 1 (AR2025 → FY2026)
        guidance_target_fy: int | None = None
        if period_type in ("Q1", "Q2", "Q3", "Q4", "H1", "H2"):
            guidance_target_fy = period_year
        elif period_type == "FY" and doc.document_type.value in (
            "ANNUAL_REPORT", "FORM_10K", "FORM_20F",
        ):
            guidance_target_fy = period_year + 1
        if guidance_target_fy is not None and guidance_fy:
            _persist_guidance_as_fy_forecast(
                db, company_id=company_id, doc=doc,
                fy_target=guidance_target_fy,
                guidance=guidance_fy,
                source_link=source_link,
            )
            json_safe_results["_guidance_fy"] = {
                "target_fy": guidance_target_fy,
                "values": {
                    k: {kk: (str(vv) if isinstance(vv, Decimal) else vv) for kk, vv in v.items()}
                    for k, v in guidance_fy.items()
                },
            }

        # Auto-Web-Fallback: für jeden None-Key in den Results probiert Claude
        # Web-Recherche und füllt die Row falls erfolgreich. Source wird zu
        # 'PDF leer (<reason>) → Claude-Recherche: <quelle>'.
        try:
            filled = _post_extraction_web_fallback(
                db, doc=doc, results=results, company=company, source_link=source_link,
            )
            if filled:
                logger.info("Post-extraction web-fallback filled %d values for doc %s",
                            filled, doc_id)
                db.commit()
        except Exception as e:
            logger.exception("Post-extraction web-fallback failed for doc %s: %s", doc_id, e)
            db.rollback()

        doc.extraction_status = ExtractionStatus.DONE
        doc.extracted_at = datetime.now(timezone.utc)
        doc.extraction_results = json_safe_results
        doc.extraction_error = None
        db.commit()

        # Trigger downstream recalc — same FY (fcf_yield, ni_growth etc.)
        # plus +1 FY because ni_growth(N+1) and net_debt_change(N+1) depend on N.
        if period_type == "FY":
            from app.values.routes import _fy_year_has_data, _run_and_persist_calculations
            try:
                _run_and_persist_calculations(db, company_id, "FY", period_year)
                if _fy_year_has_data(db, company_id, period_year + 1):
                    _run_and_persist_calculations(db, company_id, "FY", period_year + 1)
                db.commit()
            except Exception as e:
                logger.exception("Post-PDF recalc failed for doc %s: %s", doc_id, e)
                db.rollback()
        elif period_type in ("Q1", "Q2", "Q3", "Q4"):
            # Quarterly upload → refresh the running-FY estimate so factor-based
            # values pick up the new quarter immediately without manual click.
            try:
                _trigger_estimate_refresh_for_running_fy(db, company_id, period_year)
                db.commit()
            except Exception as e:
                logger.exception("Post-Q-PDF estimate refresh failed for doc %s: %s", doc_id, e)
                db.rollback()
    finally:
        db.close()


def _persist_guidance_as_fy_forecast(
    db,
    *,
    company_id,
    doc,
    fy_target: int,
    guidance: dict[str, dict],
    source_link: str,
) -> None:
    """Schreibt Guidance-Werte aus einem Q-PDF als FY-Forecast-Rows.

    is_forecast=True, from_ir_pdf=True, source_name='Guidance from Q1 2026 (S.X)'.
    Spaeterer Q-Upload ueberschreibt (gleiche Row), spaeterer AR-Upload
    ueberschreibt mit Actuals (is_forecast=False).
    Manuelle Overrides werden nicht angefasst.
    """
    now = datetime.now(timezone.utc)
    written = 0
    for key, info in guidance.items():
        value = info.get("value")
        if not isinstance(value, Decimal):
            continue
        page = info.get("page")
        currency = info.get("currency")

        existing = (
            db.query(CompanyValue)
            .filter(
                CompanyValue.company_id == company_id,
                CompanyValue.value_key == key,
                CompanyValue.period_type == "FY",
                CompanyValue.period_year == fy_target,
                CompanyValue.is_forecast.is_(True),
            )
            .one_or_none()
        )
        if existing and existing.manually_overridden:
            continue
        # Wenn ein NICHT-forecast Wert (Actuals aus AR) schon existiert, NICHT
        # mit Guidance ueberschreiben — Actuals trumpfen Guidance.
        actuals_exists = (
            db.query(CompanyValue.id)
            .filter(
                CompanyValue.company_id == company_id,
                CompanyValue.value_key == key,
                CompanyValue.period_type == "FY",
                CompanyValue.period_year == fy_target,
                CompanyValue.is_forecast.is_(False),
            )
            .first()
            is not None
        )
        if actuals_exists:
            continue

        source_name = f"Guidance from {doc.display_name}" + (f" (S.{page})" if page else "")
        if existing:
            existing.numeric_value = value
            existing.source_name = source_name
            existing.source_link = source_link
            existing.currency = currency
            existing.fetched_at = now
            existing.from_ir_pdf = True
            existing.is_forecast = True
        else:
            db.add(CompanyValue(
                id=uuid4(),
                company_id=company_id,
                value_key=key,
                period_type="FY",
                period_year=fy_target,
                numeric_value=value,
                source_name=source_name,
                source_link=source_link,
                currency=currency,
                fetched_at=now,
                from_ir_pdf=True,
                is_forecast=True,
            ))
        written += 1
    if written:
        logger.info("Persisted %d guidance values as FY%s forecast for company=%s",
                    written, fy_target, company_id)


def _trigger_estimate_refresh_for_running_fy(db, company_id, q_period_year: int) -> None:
    """Re-runs the API path for every estimable FY-key for `q_period_year`,
    so the new quarterly value flows into the FY estimate. Only runs if
    q_period_year is the running FY (>= current calendar year). Also recalcs
    FY+1 since cross-year metrics (ni_growth, net_debt_change) depend on
    the updated FY values."""
    from datetime import date as _date_today
    if q_period_year < _date_today.today().year:
        return
    from app.values.routes import _process_one_key, _run_and_persist_calculations, _fy_year_has_data
    from app.calculations.estimates import ESTIMABLE_KEYS
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if company is None:
        return

    class _Payload:
        period_type = "FY"
        period_year = q_period_year

    payload = _Payload()
    updated: list = []
    # Iterate over a sorted view so logging / behaviour is deterministic.
    for k in sorted(ESTIMABLE_KEYS):
        # Per-key SAVEPOINT so one failure doesn't roll back the previously-
        # written keys.
        try:
            with db.begin_nested():
                _process_one_key(db, k, company.ticker, company, company_id, payload, updated)
        except Exception as e:
            logger.warning("Estimate refresh key=%s failed: %s", k, e)
    try:
        _run_and_persist_calculations(db, company_id, "FY", q_period_year)
        # Cross-FY cascade: ni_growth and net_debt_change for FY+1 depend on
        # the (now updated) FY values, so recalc the next year too.
        if _fy_year_has_data(db, company_id, q_period_year + 1):
            _run_and_persist_calculations(db, company_id, "FY", q_period_year + 1)
    except Exception as e:
        logger.warning("Estimate refresh calc failed: %s", e)
        db.rollback()


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
        extraction_status=ExtractionStatus.PENDING,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    wake_worker()
    return _attach_queue_position(doc)


@router.post("/{company_id}/ir-documents/{doc_id}/extract", response_model=IRDocumentOut)
def trigger_extraction(
    company_id: UUID,
    doc_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> IRDocument:
    _get_owned_company(db, user, company_id)
    doc = db.query(IRDocument).filter(IRDocument.id == doc_id, IRDocument.company_id == company_id).one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    doc.extraction_status = ExtractionStatus.PENDING
    doc.extraction_error = None
    db.commit()
    db.refresh(doc)
    wake_worker()
    return _attach_queue_position(doc)


def _attach_queue_position(doc: IRDocument) -> IRDocument:
    """Compute queue_position once and stick it on the ORM instance for serialisation."""
    setattr(doc, "queue_position", queue_position(doc))
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
    return [_attach_queue_position(r) for r in rows]


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
