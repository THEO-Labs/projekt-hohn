"""Dedupe fuer company_values.

Der Code nimmt Eindeutigkeit pro (company_id, value_key, period_type,
period_year, is_forecast) an (one_or_none-Lookups) — Races zwischen
Batch-Threads/Gap-Fill haben aber real Duplikate erzeugt. Pro Slot ueberlebt
die beste Zeile (siehe _rank), der Rest wird geloescht.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.values.models import CompanyValue

logger = logging.getLogger(__name__)

# Rangfolge primary_method (hoeher = besser). Unbekannte Methoden landen
# zwischen web_guidance und not_found.
_METHOD_RANK = {
    "provider": 7,
    "two_stage_confirmed": 6,
    "two_stage_verified": 5,
    "manual": 4,
    "calculated": 3,
    "web_guidance": 2,
    "not_found": 0,
}
_METHOD_RANK_DEFAULT = 1

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _rank(row: CompanyValue) -> tuple:
    """Sortier-Key (aufsteigend, max() gewinnt): Wert vor NULL, dann
    Manual-Override mit Wert, dann PDF-Zeile mit Wert, dann Methoden-Rang;
    Gleichstand: neuestes fetched_at, dann groesste id (deterministisch).
    Manual vor PDF — konsistent mit den Schreibpfad-Guards, die
    manually_overridden zuerst pruefen."""
    has_value = row.numeric_value is not None
    fetched = row.fetched_at or _EPOCH
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return (
        has_value,
        has_value and bool(row.manually_overridden),
        has_value and bool(row.from_ir_pdf),
        _METHOD_RANK.get(row.primary_method or "", _METHOD_RANK_DEFAULT),
        fetched,
        str(row.id),
    )


def dedupe_company_values(db: Session) -> int:
    """Loescht Duplikat-Zeilen pro Slot, behaelt die beste. Returns Anzahl
    geloeschter Zeilen. Flusht, committet nicht."""
    dup_groups = (
        db.query(
            CompanyValue.company_id,
            CompanyValue.value_key,
            CompanyValue.period_type,
            CompanyValue.period_year,
            CompanyValue.is_forecast,
        )
        .group_by(
            CompanyValue.company_id,
            CompanyValue.value_key,
            CompanyValue.period_type,
            CompanyValue.period_year,
            CompanyValue.is_forecast,
        )
        .having(func.count() > 1)
        .all()
    )
    deleted = 0
    for g in dup_groups:
        q = db.query(CompanyValue).filter(
            CompanyValue.company_id == g.company_id,
            CompanyValue.value_key == g.value_key,
            CompanyValue.period_type == g.period_type,
            CompanyValue.is_forecast == g.is_forecast,
        )
        if g.period_year is None:
            q = q.filter(CompanyValue.period_year.is_(None))
        else:
            q = q.filter(CompanyValue.period_year == g.period_year)
        rows = q.all()
        if len(rows) < 2:
            continue
        best = max(rows, key=_rank)
        for row in rows:
            if row.id == best.id:
                continue
            logger.info(
                "dedupe company_values: delete %s/%s/%s/%s forecast=%s "
                "(method=%s, value=%s) — keep %s (method=%s)",
                g.company_id, g.value_key, g.period_type, g.period_year,
                g.is_forecast, row.primary_method, row.numeric_value,
                best.id, best.primary_method,
            )
            db.delete(row)
            deleted += 1
    db.flush()
    return deleted
