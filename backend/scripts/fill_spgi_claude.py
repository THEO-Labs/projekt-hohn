"""Claude-Fallback fuer SPGI 2025 Q2/Q3 Rows die EDGAR nicht liefert.

Nutzt _claude_fetch_historical_q der YTD-Auto-Detect eingebaut hat.

Aufruf:
    PYTHONPATH=/Users/till-olelohse/projekt-hohn/backend uv run python \\
        scripts/fill_spgi_claude.py
"""
from __future__ import annotations

import logging
import sys
from decimal import Decimal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from app.db import SessionLocal
from app.companies.models import Company
from app.values.models import CompanyValue
from app.values.quarterly_estimates import (
    _claude_fetch_historical_q,
    _upsert_q_actual_from_provider,
)

SPGI_TICKER = "SPGI"
KEYS = ["operating_cash_flow", "dividends", "sbc", "buyback_volume", "fcf"]
QUARTERS_2025 = ["Q2", "Q3"]


def _q_exists(db, company_id, key: str, q: str, year: int) -> bool:
    row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == q,
            CompanyValue.period_year == year,
            CompanyValue.numeric_value.isnot(None),
        )
        .first()
    )
    return row is not None


def main() -> int:
    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.ticker == SPGI_TICKER).one_or_none()
        if company is None:
            print(f"Company {SPGI_TICKER} not found")
            return 1
        currency = company.currency or "USD"

        for key in KEYS:
            for q in QUARTERS_2025:
                if _q_exists(db, company.id, key, q, 2025):
                    print(f"SKIP {key}/{q}/2025 (schon da)")
                    continue
                v, s, u = _claude_fetch_historical_q(company, key, 2025, q)
                if v is None:
                    print(f"FAIL {key}/{q}/2025: Claude lieferte None")
                    continue
                if key in {"buyback_volume", "dividends"} and v < 0:
                    v = abs(v)
                _upsert_q_actual_from_provider(
                    db, company.id, key, 2025, q,
                    value=v,
                    source_name=(s or f"Claude Historical Q ({q} FY2025)"),
                    source_link=u,
                    currency=currency,
                )
                # Override primary_method to web_guidance (Claude-Fetch)
                row = (
                    db.query(CompanyValue)
                    .filter(
                        CompanyValue.company_id == company.id,
                        CompanyValue.value_key == key,
                        CompanyValue.period_type == q,
                        CompanyValue.period_year == 2025,
                    )
                    .first()
                )
                if row:
                    row.primary_method = "web_guidance"
                db.commit()
                print(f"OK {key}/{q}/2025 = {float(v)/1e6:,.0f} Mio")

        # Q4-implied recompute nach Q2/Q3 Backfill
        for key in KEYS + ["capex"]:
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
            if key == "capex":
                q4_implied = abs(q4_implied)
            # Q4-Row existiert ggf. schon (calc oder alt) — Upsert-Path erlaubt Update fuer Implied.
            _upsert_q_actual_from_provider(
                db, company.id, key, 2025, "Q4",
                value=q4_implied,
                source_name=f"Implied Q4 = FY-Total ({float(fy_row.numeric_value):,.0f}) minus Sigma(Q1-Q3)",
                source_link=None,
                currency=currency,
            )
            db.commit()
            print(f"OK Q4-implied {key}/2025 = {float(q4_implied)/1e6:,.0f} Mio")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
