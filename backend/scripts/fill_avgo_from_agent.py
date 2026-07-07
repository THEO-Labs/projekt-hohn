"""Agent-Research: Broadcom FY2025 Q1-Q4 + FY, FY2026 Q1-Q2, BS FY2025.

Alle Werte aus Broadcom Investor Relations / SEC 8-K Filings (Q-Earnings-Releases).
Source-Referenzen im source_name pro Row.

Skippt existing rows mit primary_method IN (pdf, manual). Ueberschreibt
web_guidance und calculated.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from app.db import SessionLocal
from app.companies.models import Company
from app.values.models import CompanyValue

AVGO_TICKER = "AVGO"
SOURCE_BASE = "Agent-Research (Broadcom IR + SEC 8-K, per {} press release)"

# Data structure: (value_key, period_type, period_year, gaap_value_mio, adj_value_mio, source_pr_date)
# Werte in Millionen USD. EPS als raw (1.14, nicht Millionen).
DATA = [
    # ==== FY2025 Q1 (quarter ended Feb 2, 2025, released Mar 6, 2025) ====
    ("revenue", "Q1", 2025, 14_916, 14_916, "Mar 6 2025 8-K"),
    ("net_income", "Q1", 2025, 5_503, 7_823, "Mar 6 2025 8-K"),
    ("ebitda", "Q1", 2025, 10_083, 10_083, "Mar 6 2025 8-K"),  # Broadcom reports Adj EBITDA; GAAP EBITDA nicht separat -> gleiche
    ("operating_cash_flow", "Q1", 2025, 6_113, 6_113, "Mar 6 2025 8-K"),
    ("capex", "Q1", 2025, 100, 100, "Mar 6 2025 8-K"),
    ("fcf", "Q1", 2025, 6_013, 6_013, "Mar 6 2025 8-K"),
    ("sbc", "Q1", 2025, 1_280, 1_280, "Mar 6 2025 8-K"),
    ("dividends", "Q1", 2025, 2_774, 2_774, "Mar 6 2025 8-K"),
    ("buyback_volume", "Q1", 2025, 0, 0, "Mar 6 2025 8-K"),
    # EPS separat (raw, nicht mio)
    ("eps_diluted", "Q1", 2025, None, None, "Mar 6 2025 8-K"),  # placeholder — special handling below
    # ==== FY2025 Q2 (quarter ended May 4, 2025) ====
    ("revenue", "Q2", 2025, 15_004, 15_004, "Jun 5 2025 8-K"),
    ("net_income", "Q2", 2025, 4_965, 7_787, "Jun 5 2025 8-K"),
    ("ebitda", "Q2", 2025, 10_001, 10_001, "Jun 5 2025 8-K"),
    ("operating_cash_flow", "Q2", 2025, 6_555, 6_555, "Jun 5 2025 8-K"),
    ("capex", "Q2", 2025, 144, 144, "Jun 5 2025 8-K"),
    ("fcf", "Q2", 2025, 6_411, 6_411, "Jun 5 2025 8-K"),
    ("sbc", "Q2", 2025, 1_771, 1_771, "Jun 5 2025 8-K"),
    ("dividends", "Q2", 2025, 2_785, 2_785, "Jun 5 2025 8-K"),
    ("buyback_volume", "Q2", 2025, 2_450, 2_450, "Jun 5 2025 8-K"),
    # ==== FY2025 Q3 (quarter ended Aug 3, 2025) ====
    ("revenue", "Q3", 2025, 15_952, 15_952, "Sep 4 2025 8-K"),
    ("net_income", "Q3", 2025, 4_140, 8_404, "Sep 4 2025 8-K"),
    ("ebitda", "Q3", 2025, 10_702, 10_702, "Sep 4 2025 8-K"),
    ("operating_cash_flow", "Q3", 2025, 7_166, 7_166, "Sep 4 2025 8-K"),
    ("capex", "Q3", 2025, 142, 142, "Sep 4 2025 8-K"),
    ("fcf", "Q3", 2025, 7_024, 7_024, "Sep 4 2025 8-K"),
    ("sbc", "Q3", 2025, 2_322, 2_322, "Sep 4 2025 8-K"),
    ("dividends", "Q3", 2025, 2_786, 2_786, "Sep 4 2025 8-K"),
    ("buyback_volume", "Q3", 2025, 0, 0, "Sep 4 2025 8-K"),
    # ==== FY2025 Q4 (quarter ended Nov 2, 2025) ====
    ("revenue", "Q4", 2025, 18_015, 18_015, "Dec 11 2025 10-K"),
    ("net_income", "Q4", 2025, 8_518, 9_714, "Dec 11 2025 10-K"),
    ("ebitda", "Q4", 2025, 12_218, 12_218, "Dec 11 2025 10-K"),  # FY 43004 - Q1-Q3 sum
    ("operating_cash_flow", "Q4", 2025, 7_703, 7_703, "Dec 11 2025 10-K"),
    ("capex", "Q4", 2025, 237, 237, "Dec 11 2025 10-K"),
    ("fcf", "Q4", 2025, 7_466, 7_466, "Dec 11 2025 10-K"),
    ("sbc", "Q4", 2025, 2_195, 2_195, "Dec 11 2025 10-K"),
    ("dividends", "Q4", 2025, 2_797, 2_797, "Dec 11 2025 10-K"),
    ("buyback_volume", "Q4", 2025, 0, 0, "Dec 11 2025 10-K"),
    # ==== FY2025 Full Year ====
    ("revenue", "FY", 2025, 63_887, 63_887, "Dec 11 2025 10-K"),
    ("net_income", "FY", 2025, 23_126, 33_728, "Dec 11 2025 10-K"),
    ("ebitda", "FY", 2025, 43_004, 43_004, "Dec 11 2025 10-K"),
    ("operating_cash_flow", "FY", 2025, 27_537, 27_537, "Dec 11 2025 10-K"),
    ("capex", "FY", 2025, 623, 623, "Dec 11 2025 10-K"),
    ("fcf", "FY", 2025, 26_914, 26_914, "Dec 11 2025 10-K"),
    ("sbc", "FY", 2025, 7_568, 7_568, "Dec 11 2025 10-K"),
    ("dividends", "FY", 2025, 11_142, 11_142, "Dec 11 2025 10-K"),
    ("buyback_volume", "FY", 2025, 2_450, 2_450, "Dec 11 2025 10-K"),
    # ==== FY2026 Q1 (quarter ended Feb 1, 2026) ====
    ("revenue", "Q1", 2026, 19_311, 19_311, "Mar 4 2026 8-K"),
    ("net_income", "Q1", 2026, 7_349, 10_185, "Mar 4 2026 8-K"),
    ("ebitda", "Q1", 2026, 13_128, 13_128, "Mar 4 2026 8-K"),
    ("operating_cash_flow", "Q1", 2026, 8_260, 8_260, "Mar 4 2026 8-K"),
    ("capex", "Q1", 2026, 250, 250, "Mar 4 2026 8-K"),
    ("fcf", "Q1", 2026, 8_010, 8_010, "Mar 4 2026 8-K"),
    ("sbc", "Q1", 2026, 2_176, 2_176, "Mar 4 2026 8-K"),
    ("dividends", "Q1", 2026, 3_086, 3_086, "Mar 4 2026 8-K"),
    ("buyback_volume", "Q1", 2026, 7_850, 7_850, "Mar 4 2026 8-K"),
    # ==== FY2026 Q2 (quarter ended May 3, 2026) ====
    ("revenue", "Q2", 2026, 22_187, 22_187, "Jun 3 2026 8-K"),
    ("net_income", "Q2", 2026, 9_310, 12_074, "Jun 3 2026 8-K"),
    ("ebitda", "Q2", 2026, 15_244, 15_244, "Jun 3 2026 8-K"),
    ("operating_cash_flow", "Q2", 2026, 10_493, 10_493, "Jun 3 2026 8-K"),
    ("capex", "Q2", 2026, 231, 231, "Jun 3 2026 8-K"),
    ("fcf", "Q2", 2026, 10_262, 10_262, "Jun 3 2026 8-K"),
    ("sbc", "Q2", 2026, 2_092, 2_092, "Jun 3 2026 8-K"),
    ("dividends", "Q2", 2026, 3_092, 3_092, "Jun 3 2026 8-K"),
    ("buyback_volume", "Q2", 2026, 600, 600, "Jun 3 2026 8-K"),
]

# EPS-Werte (raw, per share)
EPS_DATA = [
    # (period_type, period_year, gaap, adj, source_date)
    ("Q1", 2025, Decimal("1.14"), Decimal("1.60"), "Mar 6 2025 8-K"),
    ("Q2", 2025, Decimal("1.03"), Decimal("1.58"), "Jun 5 2025 8-K"),
    ("Q3", 2025, Decimal("0.85"), Decimal("1.69"), "Sep 4 2025 8-K"),
    ("Q4", 2025, Decimal("1.74"), Decimal("1.95"), "Dec 11 2025 10-K"),
    ("FY", 2025, Decimal("4.77"), Decimal("6.82"), "Dec 11 2025 10-K"),
    ("Q1", 2026, Decimal("1.50"), Decimal("2.05"), "Mar 4 2026 8-K"),
    ("Q2", 2026, Decimal("1.91"), Decimal("2.44"), "Jun 3 2026 8-K"),
]

# Balance Sheet FY2025 (as of Nov 2, 2025)
BS_2025 = [
    ("cash_and_equivalents", 16_178, "Dec 11 2025 10-K"),
    ("st_debt", 3_152, "Dec 11 2025 10-K"),
    ("lt_debt", 61_984, "Dec 11 2025 10-K"),
]


def _upsert(db, company_id, key, period_type, period_year, gaap_mio, adj_mio, source_ref, is_eps=False, is_currency=True):
    """Idempotent upsert. Skip pdf/manual. Override web_guidance/calculated/provider."""
    existing = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == period_type,
            CompanyValue.period_year == period_year,
        )
        .first()
    )
    if existing and existing.primary_method in ("pdf", "manual"):
        print(f"SKIP {key}/{period_type}/{period_year}: existing pdf/manual")
        return
    if existing and getattr(existing, "manually_overridden", False):
        print(f"SKIP {key}/{period_type}/{period_year}: manually_overridden")
        return

    if is_eps:
        gaap_base = Decimal(str(gaap_mio)) if gaap_mio is not None else None
        adj_base = Decimal(str(adj_mio)) if adj_mio is not None else None
    else:
        gaap_base = Decimal(str(gaap_mio)) * Decimal("1000000") if gaap_mio is not None else None
        adj_base = Decimal(str(adj_mio)) * Decimal("1000000") if adj_mio is not None else None

    now = datetime.now(timezone.utc)
    source_name = f"Agent-Research: Broadcom IR ({source_ref})"[:4000]
    currency = "USD" if is_currency else None

    if existing:
        existing.numeric_value = gaap_base
        existing.numeric_value_adjusted = adj_base if adj_base != gaap_base else None
        existing.source_name = source_name
        existing.fetched_at = now
        existing.is_forecast = False
        existing.primary_method = "manual"  # Agent-eingetragen = wie manual override
        existing.currency = currency
        print(f"UPDATE {key}/{period_type}/{period_year} = {gaap_mio} / adj={adj_mio}")
    else:
        cv = CompanyValue(
            id=uuid4(),
            company_id=company_id,
            value_key=key,
            period_type=period_type,
            period_year=period_year,
            numeric_value=gaap_base,
            numeric_value_adjusted=adj_base if adj_base != gaap_base else None,
            source_name=source_name,
            currency=currency,
            fetched_at=now,
            is_forecast=False,
            primary_method="manual",
        )
        db.add(cv)
        print(f"INSERT {key}/{period_type}/{period_year} = {gaap_mio} / adj={adj_mio}")


def main() -> int:
    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.ticker == AVGO_TICKER).one_or_none()
        if company is None:
            print(f"Company {AVGO_TICKER} not found")
            return 1
        cid = company.id

        for (key, pt, py, gaap_mio, adj_mio, src) in DATA:
            _upsert(db, cid, key, pt, py, gaap_mio, adj_mio, src, is_eps=False)
        for (pt, py, gaap, adj, src) in EPS_DATA:
            _upsert(db, cid, "eps_diluted", pt, py, gaap, adj, src, is_eps=True, is_currency=False)
        for (key, val_mio, src) in BS_2025:
            _upsert(db, cid, key, "FY", 2025, val_mio, val_mio, src, is_eps=False)

        db.commit()
        print("done")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
