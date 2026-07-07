"""Henkel AG & Co KGaA. FY End Dec 31. Consumer Goods."""
from __future__ import annotations
TICKER = "HEN3.DE"; COMPANY_NAME = "Henkel AG & Co KGaA"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "Henkel FY 2025 (Mar 11 2026)", {
        "revenue": (20_500, 20_500),
        "ebitda": (3_026, 3_026),  # Adjusted EBIT
    }),
]
EPS_DATA = [("FY", 2025, ("5.33", "5.33"), "Henkel FY 2025 Adjusted preferred share EPS")]
BS_DATA = {}
