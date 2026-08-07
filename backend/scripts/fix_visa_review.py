"""One-off: Visa-Datenkorrekturen aus dem Kunden-Review (laeuft auf Prod).

Korrekturen:
  - net_income adjusted: Q1 FY2025, FY2025, FY2026 (Summe der Quartale).
  - eps_diluted GAAP: Q1/Q2 FY2026 als Manual (Werte aus den Earnings
    Releases); Q1-Q4 FY2025 als Manual stempeln (Werte stimmen bereits,
    Schutz vor der EDGAR-Strikt-Regel — Visa-EPS kommt nie aus
    companyfacts).
  - eps_diluted FY2026 adjusted: Summe der Non-GAAP-Quartale.
  - Danach validate_cross_metrics + Recalc fuer 2025 + 2026.
  - lt_debt wird bewusst NICHT geaendert (Definitionsfrage non-current
    vs total — nur im Bericht dokumentiert).

Aufruf im Backend-Container/venv:
    cd backend && uv run python scripts/fix_visa_review.py
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from app.companies.models import Company
from app.db import SessionLocal
from app.values.consistency import validate_cross_metrics
from app.values.models import CompanyValue
from app.values.routes import _run_and_persist_calculations

VISA_ID = "e61548c4-e309-4e1a-96d9-eb8366fde479"

# Non-GAAP Net Income (absolute USD).
ADJ_NET_INCOME = [
    # (period_type, year, value, note)
    ("Q1", 2025, Decimal("5463000000"), "Non-GAAP Q1 FY2025 Reconciliation"),
    ("FY", 2025, Decimal("22542000000"), "Non-GAAP FY2025 Reconciliation"),
    ("FY", 2026, Decimal("25812000000"),
     "Summe Non-GAAP Q1-Q4 FY2026 (6124+6342+6296+7050 Mio)"),
]

# GAAP Diluted EPS als Manual (Quelle: Earnings Releases).
MANUAL_EPS_GAAP = [
    # (period_type, year, value, source_name)
    ("Q1", 2026, Decimal("3.01"), "Visa Q1 FY2026 Earnings Release"),
    ("Q2", 2026, Decimal("3.10"), "Visa Q2 FY2026 Earnings Release"),
    ("Q1", 2025, Decimal("2.58"), "Visa Q1 FY2025 Earnings Release"),
    ("Q2", 2025, Decimal("2.32"), "Visa Q2 FY2025 Earnings Release"),
    ("Q3", 2025, Decimal("2.69"), "Visa Q3 FY2025 Earnings Release"),
    ("Q4", 2025, Decimal("2.62"), "Visa Q4 FY2025 Earnings Release"),
]

# Non-GAAP Diluted EPS FY2026 = 3.17 + 3.31 + 3.32 + 3.61.
ADJ_EPS_FY2026 = Decimal("13.41")


def _pick_row(db, company, key: str, ptype: str, year: int) -> CompanyValue:
    """Zielzeile: Actual-Slot bevorzugt, sonst Forecast-Slot; fehlt beides,
    wird eine Actual-Zeile angelegt."""
    rows = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == ptype,
            CompanyValue.period_year == year,
        )
        .order_by(CompanyValue.is_forecast.asc())
        .all()
    )
    if rows:
        return rows[0]
    row = CompanyValue(
        id=uuid4(), company_id=company.id, value_key=key,
        period_type=ptype, period_year=year, is_forecast=False,
        currency=company.currency,
    )
    db.add(row)
    db.flush()
    return row


def _set_adjusted(db, company, key: str, ptype: str, year: int,
                  value: Decimal, note: str) -> None:
    row = _pick_row(db, company, key, ptype, year)
    old = row.numeric_value_adjusted
    row.numeric_value_adjusted = value
    row.adjustments_source = "Manual"
    row.adjustments_note = note
    print(f"  {key} {ptype} {year} adjusted: {old} -> {value}")


def _set_gaap_manual(db, company, key: str, ptype: str, year: int,
                     value: Decimal, source_name: str) -> None:
    row = _pick_row(db, company, key, ptype, year)
    old = row.numeric_value
    if old is not None and old != value:
        print(f"  HINWEIS {key} {ptype} {year}: bestehender Wert {old} != {value}")
    row.numeric_value = value
    row.source_name = source_name
    row.source_link = None
    row.primary_method = "manual"
    row.manually_overridden = True
    row.is_forecast = False
    row.from_ir_pdf = False
    row.currency = company.currency
    now = datetime.now(timezone.utc)
    row.fetched_at = now
    row.last_refresh_attempt = now
    print(f"  {key} {ptype} {year} GAAP manual: {old} -> {value} ({source_name})")


def main() -> int:
    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.id == VISA_ID).one_or_none()
        if company is None:
            print(f"Company {VISA_ID} not found")
            return 1
        print(f"Visa-Review-Korrekturen fuer {company.ticker} ({company.name})")

        print("\nNet Income adjusted:")
        for ptype, year, value, note in ADJ_NET_INCOME:
            _set_adjusted(db, company, "net_income", ptype, year, value, note)

        print("\nEPS diluted GAAP (Manual):")
        for ptype, year, value, source in MANUAL_EPS_GAAP:
            _set_gaap_manual(db, company, "eps_diluted", ptype, year, value, source)

        print("\nEPS diluted FY2026 adjusted (Manual):")
        _set_adjusted(
            db, company, "eps_diluted", "FY", 2026, ADJ_EPS_FY2026,
            "Summe Non-GAAP Q1-Q4 FY2026 (3.17+3.31+3.32+3.61)",
        )
        # Der Manual-Adjusted-Marker schuetzt die Zeile vor Anker/Two-Stage.
        db.commit()

        print("\nKonsistenz + Recalc 2025/2026:")
        for year in (2025, 2026):
            flags = validate_cross_metrics(db, company.id, year)
            print(f"  FY{year} consistency flags: {flags or 'keine'}")
            _run_and_persist_calculations(db, company.id, "FY", year)
            db.commit()

        print(
            "\nlt_debt bewusst unveraendert (Definitionsfrage non-current "
            "vs total debt — siehe Review-Bericht)."
        )
        print("done")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
