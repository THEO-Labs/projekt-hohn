"""Gezieltes Auffuellen der geloeschten Stale-Rows fuer SPGI 2025.

Nur EDGAR-Fetches (keine Claude-Calls). Wenn EDGAR nichts liefert, bleibt die
Row NULL — dann kann der User im UI manual overriden.

Aufruf:
    PYTHONPATH=/Users/till-olelohse/projekt-hohn/backend uv run python \\
        scripts/fill_spgi_stale.py
"""
from __future__ import annotations

import logging
import sys
from decimal import Decimal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from app.db import SessionLocal
from app.companies.models import Company
from app.values.models import CompanyValue
from app.providers.edgar import EdgarProvider
from app.values.quarterly_estimates import _upsert_q_actual_from_provider

SPGI_TICKER = "SPGI"
KEYS = ["operating_cash_flow", "dividends", "sbc", "buyback_volume", "fcf", "capex"]
QUARTERS_2025 = ["Q2", "Q3"]


def main() -> int:
    db = SessionLocal()
    provider = EdgarProvider()
    try:
        company = db.query(Company).filter(Company.ticker == SPGI_TICKER).one_or_none()
        if company is None:
            print(f"Company {SPGI_TICKER} not found")
            return 1
        fy_end_month = getattr(company, "fiscal_year_end_month", None)
        fy_end_day = getattr(company, "fiscal_year_end_day", None)
        currency = company.currency or "USD"

        # EDGAR Standalone Q2/Q3 2025
        for key in KEYS:
            for q in QUARTERS_2025:
                try:
                    res = provider.fetch_quarterly(
                        company.ticker, key, 2025, q,
                        fy_end_month=fy_end_month, fy_end_day=fy_end_day,
                    )
                except Exception as e:
                    print(f"FAIL fetch {key}/{q}/2025: {e}")
                    continue
                if res is None or res.value is None:
                    print(f"EDGAR nix fuer {key}/{q}/2025 (bleibt NULL)")
                    continue
                v = Decimal(str(res.value))
                if key in {"buyback_volume", "dividends"} and v < 0:
                    v = abs(v)
                _upsert_q_actual_from_provider(
                    db, company.id, key, 2025, q,
                    value=v,
                    source_name=res.source_name,
                    source_link=res.source_link,
                    currency=currency,
                )
                db.commit()
                print(f"OK {key}/{q}/2025 = {float(v)/1e6:,.0f} Mio")

        # Q4 2025 implied recompute fuer FCF/CapEx/Buyback (die gerade geloescht wurden)
        # Nur wenn Q1-Q3 alle vorhanden + FY-Total in DB.
        for key in ["fcf", "capex", "buyback_volume"]:
            q123 = (
                db.query(CompanyValue)
                .filter(
                    CompanyValue.company_id == company.id,
                    CompanyValue.value_key == key,
                    CompanyValue.period_type.in_(("Q1", "Q2", "Q3")),
                    CompanyValue.period_year == 2025,
                    CompanyValue.is_forecast.is_(False),
                    CompanyValue.numeric_value.isnot(None),
                )
                .all()
            )
            if len(q123) < 3:
                print(f"Q4-implied {key}: nicht alle Q1-Q3 da ({len(q123)}/3), skip")
                continue
            fy_row = (
                db.query(CompanyValue)
                .filter(
                    CompanyValue.company_id == company.id,
                    CompanyValue.value_key == key,
                    CompanyValue.period_type == "FY",
                    CompanyValue.period_year == 2025,
                    CompanyValue.is_forecast.is_(False),
                    CompanyValue.numeric_value.isnot(None),
                )
                .first()
            )
            if fy_row is None:
                print(f"Q4-implied {key}: kein FY-Total, skip")
                continue
            q123_sum = sum(r.numeric_value for r in q123)
            q4_implied = fy_row.numeric_value - q123_sum
            _upsert_q_actual_from_provider(
                db, company.id, key, 2025, "Q4",
                value=q4_implied,
                source_name=f"Implied Q4 = FY-Total ({float(fy_row.numeric_value):,.0f}) minus Sigma(Q1-Q3)",
                source_link=None,
                currency=currency,
            )
            db.commit()
            print(f"OK Q4-implied {key}/2025 = {float(q4_implied)/1e6:,.0f} Mio (Sigma={float(q123_sum)/1e6:,.0f})")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
