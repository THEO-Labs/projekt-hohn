"""Henkel AG & Co KGaA. FY End Dec 31. Consumer Goods.

FIX 2026-07-08: NI 2.058B ergaenzt aus Annual Report Search.
"""
from __future__ import annotations
TICKER = "HEN3.DE"; COMPANY_NAME = "Henkel AG & Co KGaA"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "Henkel FY 2025 (Mar 11 2026)", {
        "revenue": (20_500, 20_500),
        "ebitda": (3_026, 3_026),  # Adjusted EBIT
        "net_income": (2_058, 2_058),
    }),
]
EPS_DATA = [("FY", 2025, ("5.33", "5.33"), "Henkel FY 2025 Adjusted preferred share EPS")]
BS_DATA = {}
