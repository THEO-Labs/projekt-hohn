"""Heidelberg Materials AG. FY End Dec 31. Construction Materials.

FIX 2026-07-08: Revenue Korrektur 21.55 -> 21.46, EBITDA + EPS ergaenzt.
"""
from __future__ import annotations
TICKER = "HEI.DE"; COMPANY_NAME = "Heidelberg Materials AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("FY", 2025, "Heidelberg Materials FY 2025 (Feb 25 2026)", {
        "revenue": (21_460, 21_460), "ebitda": (4_680, 4_680), "net_income": (1_940, 1_940),
    }),
    ("Q1", 2026, "Heidelberg Materials Q1 2026 (May 2026)", {
        "revenue": (4_540, 4_540),
        "ebitda": (484, 484),
        "net_income": (198, 198),  # ~EPS 1.27 * 156M shares
    }),
]
EPS_DATA = [
    ("FY", 2025, ("11.09", "12.41"), "Heidelberg Materials FY 2025 (Basic est / Adjusted)"),
    ("Q1", 2026, ("1.27", "1.27"), "Heidelberg Materials Q1 2026"),
]
BS_DATA = {}

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Heidelberg Q2 2026 est (seasonal peak)", {
        "revenue": (5800, 5800),
        "ebitda": (1400, 1400),
        "net_income": (630, 630),
    }),
    ("Q3", 2026, "Heidelberg Q3 2026 est (seasonal peak Bau)", {
        "revenue": (6200, 6200),
        "ebitda": (1550, 1550),
        "net_income": (720, 720),
    }),
    ("Q4", 2026, "Heidelberg Materials Q4 2026 est", {
        "revenue": (6500, 6500),
        "ebitda": (1600, 1600),
        "net_income": (780, 780),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("4.03", "4.03"), "Heidelberg Q2 2026 est (seasonal peak)"),
    ("Q3", 2026, ("4.62", "4.62"), "Heidelberg Q3 2026 est (seasonal peak Bau)"),
    ("Q4", 2026, ("5.01", "5.01"), "Heidelberg Materials Q4 2026 est"),
]
