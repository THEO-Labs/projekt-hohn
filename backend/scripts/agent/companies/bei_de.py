"""Beiersdorf AG. FY End Dec 31. Consumer Goods."""
from __future__ import annotations
TICKER = "BEI.DE"; COMPANY_NAME = "Beiersdorf AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "Beiersdorf FY 2025 (Mar 2 2026)", {
        "revenue": (9_900, 9_900),
        "ebitda": (1_400, 1_400),  # EBIT excl special
        "net_income": (955, 955),
    }),
    ("Q1", 2026, "Beiersdorf Q1 2026 (Apr 21 2026)", {
        "revenue": (2_484, 2_484),
        "ebitda": (523, 523),  # EBIT (Operating income)
    }),
]
EPS_DATA = [("FY", 2025, ("4.25", "4.25"), "Beiersdorf FY 2025")]
BS_DATA = {}
