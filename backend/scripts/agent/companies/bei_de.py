"""Beiersdorf AG. FY End Dec 31. Consumer Goods."""
from __future__ import annotations
TICKER = "BEI.DE"; COMPANY_NAME = "Beiersdorf AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "Beiersdorf FY 2025 (Mar 2 2026)", {
        "revenue": (9_900, 9_900),
        "ebitda": (1_400, 1_400),  # EBIT excl special
        "net_income": (955, 955),
    }),
]
EPS_DATA = [("FY", 2025, ("4.25", "4.25"), "Beiersdorf FY 2025")]
BS_DATA = {}
