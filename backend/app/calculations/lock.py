from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.companies.models import Company
from app.ir_documents.models import (
    DocumentType,
    ExtractionStatus,
    IRDocument,
    PeriodCoverage,
)


_ANNUAL_REPORT_TYPES = (
    DocumentType.ANNUAL_REPORT,
    DocumentType.FORM_10K,
    DocumentType.FORM_20F,
)


def is_us_company(company: Company) -> bool:
    return bool(company.isin and company.isin.upper().startswith("US"))


def annual_report_years(db: Session, company_id: UUID) -> list[int]:
    rows = (
        db.query(IRDocument.period_year)
        .filter(
            IRDocument.company_id == company_id,
            IRDocument.document_type.in_(_ANNUAL_REPORT_TYPES),
            IRDocument.period_coverage == PeriodCoverage.FY,
            IRDocument.extraction_status == ExtractionStatus.DONE,
        )
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows})


def quarter_years(db: Session, company_id: UUID) -> list[int]:
    """Jahre für die mindestens ein Q1/Q2/Q3-Bericht extrahiert wurde."""
    rows = (
        db.query(IRDocument.period_year)
        .filter(
            IRDocument.company_id == company_id,
            IRDocument.period_coverage.in_((PeriodCoverage.Q1, PeriodCoverage.Q2, PeriodCoverage.Q3)),
            IRDocument.extraction_status == ExtractionStatus.DONE,
        )
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows})


def quarter_years_in_progress(db: Session, company_id: UUID) -> list[int]:
    """Jahre für die ein Q-Report PENDING/EXTRACTING/FAILED ist (noch nicht
    DONE). Wird im Lock-Banner verwendet damit User sieht 'hochgeladen aber
    noch nicht durch'."""
    rows = (
        db.query(IRDocument.period_year)
        .filter(
            IRDocument.company_id == company_id,
            IRDocument.period_coverage.in_((PeriodCoverage.Q1, PeriodCoverage.Q2, PeriodCoverage.Q3)),
            IRDocument.extraction_status.in_(
                (ExtractionStatus.PENDING, ExtractionStatus.EXTRACTING, ExtractionStatus.FAILED)
            ),
        )
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows})


def has_done_annual_report(db: Session, company_id: UUID, period_year: int) -> bool:
    return (
        db.query(IRDocument.id)
        .filter(
            IRDocument.company_id == company_id,
            IRDocument.document_type.in_(_ANNUAL_REPORT_TYPES),
            IRDocument.period_coverage == PeriodCoverage.FY,
            IRDocument.period_year == period_year,
            IRDocument.extraction_status == ExtractionStatus.DONE,
        )
        .first()
        is not None
    )


def is_hohn_locked(db: Session, company: Company, period_year: int) -> bool:
    """Hohn-Rendite is locked when computing it from non-authoritative data
    would be misleading. Rule:
      - laufende FY (estimate) → nie gesperrt
      - US-Filer (ISIN US...) → nie gesperrt (EDGAR ist autoritativ)
      - sonst historische FY ohne fertig extrahiertem Annual Report → gesperrt
    """
    if period_year >= date.today().year:
        return False
    if is_us_company(company):
        return False
    return not has_done_annual_report(db, company.id, period_year)
