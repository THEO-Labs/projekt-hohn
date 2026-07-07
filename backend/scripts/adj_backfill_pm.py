"""Non-GAAP-Backfill fuer Philip Morris NI + EPS + EBITDA 2025+2026 Q1-Q4."""
from __future__ import annotations

import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from app.db import SessionLocal
from app.companies.models import Company
from app.values.quarterly_estimates import _ensure_q_adjusted

PM_ID = "290c0537-aaa9-434f-a8e0-6ff90af7a614"
TARGETS = [
    ("net_income", 2025, ["Q1", "Q2", "Q3", "Q4"]),
    ("net_income", 2026, ["Q1", "Q2", "Q3", "Q4"]),
    ("eps_diluted", 2025, ["Q1", "Q2", "Q3", "Q4"]),
    ("eps_diluted", 2026, ["Q1", "Q2", "Q3", "Q4"]),
    ("ebitda", 2025, ["Q1", "Q2", "Q3", "Q4"]),
    ("ebitda", 2026, ["Q1", "Q2", "Q3", "Q4"]),
]


def main() -> int:
    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.id == PM_ID).one_or_none()
        if company is None:
            print(f"Company {PM_ID} not found")
            return 1
        print(f"Backfill Adj-Werte fuer {company.ticker} ({company.name})")
        for key, year, quarters in TARGETS:
            for q in quarters:
                try:
                    _ensure_q_adjusted(db, company, key, year, q)
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    print(f"FAIL {key}/{year}/{q}: {exc}")
        print("done")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
