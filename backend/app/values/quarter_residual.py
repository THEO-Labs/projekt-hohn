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


def _val(row) -> Decimal | None:
    return row.numeric_value if row is not None else None


def _slot_row(db, company_id, key: str, period_type: str, year: int, is_forecast: bool):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == period_type,
            CompanyValue.period_year == year,
            CompanyValue.is_forecast.is_(is_forecast),
        )
        .order_by(CompanyValue.numeric_value.isnot(None).desc())
        .first()
    )


def _fy_basis(db, company_id, key: str, year: int):
    """FY-Basis fuer das Q4-Residual: bevorzugt das berichtete Actual, sonst die
    Konsens-Schaetzung (Forecast). Rueckgabe: (wert, currency, is_forecast)."""
    actual = _slot_row(db, company_id, key, "FY", year, False)
    if actual is not None and actual.numeric_value is not None:
        return actual.numeric_value, actual.currency, False
    fc = _slot_row(db, company_id, key, "FY", year, True)
    if fc is not None and fc.numeric_value is not None:
        return fc.numeric_value, fc.currency, True
    return None, None, None


def derive_q4_from_fy_residual(db, company, years) -> int:
    """Fuellt fehlende Q4-Flow-Zellen mit FY − Q1 − Q2 − Q3.

    FY-Basis ist das berichtete Actual (dann ist Q4 ein Actual) ODER — wenn kein
    Actual vorliegt — die Konsens-Schaetzung (dann ist Q4 ein FORECAST, z.B. das
    laufende FY: Q1-Q3 sind EDGAR-Actuals, Q4 = FY-Konsens − Q1-Q3). Q1, Q2, Q3
    muessen als berichtete Actuals vorliegen. Ein bereits berichtetes Q4-Actual
    (EDGAR) wird nie ueberschrieben. Rueckgabe: Anzahl geschriebener Zellen."""
    written = 0
    for year in years:
        for key in Q4_RESIDUAL_KEYS:
            # Berichtetes Q4-Actual da? Dann nichts tun (EDGAR/Residual-Actual).
            actual_q4 = _slot_row(db, company.id, key, "Q4", year, False)
            if actual_q4 is not None and actual_q4.numeric_value is not None:
                continue

            fy, fy_cur, is_fc = _fy_basis(db, company.id, key, year)
            if fy is None:
                continue
            q1 = _val(_slot_row(db, company.id, key, "Q1", year, False))
            q2 = _val(_slot_row(db, company.id, key, "Q2", year, False))
            q3 = _val(_slot_row(db, company.id, key, "Q3", year, False))
            if q1 is None or q2 is None or q3 is None:
                continue

            # Ziel-Slot: is_forecast = Herkunft der FY-Basis.
            q4_row = _slot_row(db, company.id, key, "Q4", year, is_fc)
            if q4_row is not None and q4_row.manually_overridden:
                continue

            value = normalize_sign(
                key, fy - q1 - q2 - q3,
                context=f"q4-residual {company.ticker} FY{year} forecast={is_fc}",
            )
            now = datetime.now(timezone.utc)
            if q4_row is None:
                q4_row = CompanyValue(
                    id=uuid4(), company_id=company.id, value_key=key,
                    period_type="Q4", period_year=year, is_forecast=is_fc,
                )
                db.add(q4_row)
            q4_row.numeric_value = value
            q4_row.numeric_value_adjusted = None
            q4_row.is_forecast = is_fc
            if fy_cur is not None:
                q4_row.currency = fy_cur
            q4_row.source_name = (
                "Geschätzt (Q4 = FY-Konsens − Q1−Q3)" if is_fc
                else "Berechnet (Q4 = FY − Q1−Q3)"
            )
            q4_row.source_link = None
            q4_row.primary_method = "q4_residual"
            q4_row.manually_overridden = False
            q4_row.from_ir_pdf = False
            q4_row.fetched_at = now
            q4_row.last_refresh_attempt = now
            written += 1
    db.flush()
    return written
