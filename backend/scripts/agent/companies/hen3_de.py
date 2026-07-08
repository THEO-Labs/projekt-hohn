"""Henkel AG & Co KGaA. FY End Dec 31. Consumer Goods.

FIX 2026-07-08: NI 2.058B ergaenzt aus Annual Report Search.
"""
from __future__ import annotations
TICKER = "HEN3.DE"; COMPANY_NAME = "Henkel AG & Co KGaA"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "Henkel FY 2025 (Mar 11 2026)", {
        "revenue": (20_500, 20_500), "ebitda": (3_026, 3_026), "net_income": (2_058, 2_058),
    }),
    ("Q1", 2026, "Henkel Q1 2026 (May 7 2026)", {
        "revenue": (4_952, 4_952),
    }),
]
EPS_DATA = [("FY", 2025, ("5.33", "5.33"), "Henkel FY 2025 Adjusted preferred share EPS")]
BS_DATA = {}

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Henkel Q2 2026 est", {
        "revenue": (4950, 4950),
        "ebitda": (750, 750),
        "net_income": (500, 500),
    }),
    ("Q3", 2026, "Henkel Q3 2026 est", {
        "revenue": (5000, 5000),
        "ebitda": (780, 780),
        "net_income": (520, 520),
    }),
    ("Q4", 2026, "Henkel Q4 2026 est (Weihnachten Cosmetics)", {
        "revenue": (5400, 5400),
        "ebitda": (850, 850),
        "net_income": (570, 570),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("1.15", "1.15"), "Henkel Q2 2026 est"),
    ("Q3", 2026, ("1.20", "1.20"), "Henkel Q3 2026 est"),
    ("Q4", 2026, ("1.32", "1.32"), "Henkel Q4 2026 est (Weihnachten Cosmetics)"),
]
