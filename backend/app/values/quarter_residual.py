"""Q4 = FY − (Q1 + Q2 + Q3) fuer Flow-Kennzahlen.

Viele Firmen berichten Q4 nicht separat: das 10-K enthaelt nur das Gesamtjahr
(FY), die 10-Qs liefern Q1-Q3. Fuer die 3-Monats-Income-Statement-Zeilen
(revenue/net_income/ebitda/eps) fehlt Q4 daher in EDGAR — die Cashflow-Zeilen
(OCF/capex/fcf/sbc/buybacks/dividends) leitet EDGAR selbst via YTD-Differenz ab.
Dieser Pass fuellt die fehlenden Q4-Flow-Zellen deterministisch aus FY − Q1-Q3,
nur wenn FY (abgeschlossenes Jahr) UND Q1, Q2, Q3 als reported vorliegen und Q4
noch fehlt. eps_diluted ist dabei eine Naeherung (verwaesserte Aktienzahl
variiert je Quartal), aber die uebliche Q4-Herleitung.

Balance-/Snapshot-Keys sind bewusst NICHT dabei — eine Q4-Bilanz ist der
Stichtag zum FY-Ende, keine Quartalssumme.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.values.models import CompanyValue
from app.values.persistence import normalize_sign

logger = logging.getLogger(__name__)

Q4_RESIDUAL_KEYS = (
    "revenue", "net_income", "ebitda", "eps_diluted",
    "operating_cash_flow", "capex", "fcf",
    "sbc", "buyback_volume", "dividends",
)


def _reported_row(db, company_id, key: str, period_type: str, year: int):
    """Reported (nicht-forecast) Zeile im Slot; bevorzugt eine mit Wert."""
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == period_type,
            CompanyValue.period_year == year,
            CompanyValue.is_forecast.is_(False),
        )
        .order_by(CompanyValue.numeric_value.isnot(None).desc())
        .first()
    )


def _val(row) -> Decimal | None:
    return row.numeric_value if row is not None else None


def derive_q4_from_fy_residual(db, company, years) -> int:
    """Fuellt fehlende Q4-Flow-Zellen mit FY − Q1 − Q2 − Q3. Rueckgabe: Anzahl
    geschriebener Zellen."""
    written = 0
    for year in years:
        for key in Q4_RESIDUAL_KEYS:
            q4_row = _reported_row(db, company.id, key, "Q4", year)
            # Q4 schon berichtet (EDGAR/andere) oder manuell -> nicht anfassen.
            if q4_row is not None and (
                q4_row.numeric_value is not None or q4_row.manually_overridden
            ):
                continue

            fy_row = _reported_row(db, company.id, key, "FY", year)
            fy = _val(fy_row)
            q1 = _val(_reported_row(db, company.id, key, "Q1", year))
            q2 = _val(_reported_row(db, company.id, key, "Q2", year))
            q3 = _val(_reported_row(db, company.id, key, "Q3", year))
            if fy is None or q1 is None or q2 is None or q3 is None:
                continue

            value = normalize_sign(
                key, fy - q1 - q2 - q3,
                context=f"q4-residual {company.ticker} FY{year}",
            )
            now = datetime.now(timezone.utc)
            if q4_row is None:
                q4_row = CompanyValue(
                    id=uuid4(), company_id=company.id, value_key=key,
                    period_type="Q4", period_year=year, is_forecast=False,
                )
                db.add(q4_row)
            q4_row.numeric_value = value
            q4_row.numeric_value_adjusted = None
            q4_row.is_forecast = False
            if fy_row is not None and fy_row.currency is not None:
                q4_row.currency = fy_row.currency
            q4_row.source_name = "Berechnet (Q4 = FY − Q1−Q3)"
            q4_row.source_link = None
            q4_row.primary_method = "q4_residual"
            q4_row.manually_overridden = False
            q4_row.from_ir_pdf = False
            q4_row.fetched_at = now
            q4_row.last_refresh_attempt = now
            written += 1
    db.flush()
    return written
