"""Heidelberg Materials AG. FY End Dec 31. Construction Materials.

FIX 2026-07-08: Revenue Korrektur 21.55 -> 21.46, EBITDA + EPS ergaenzt.
"""
from __future__ import annotations
TICKER = "HEI.DE"; COMPANY_NAME = "Heidelberg Materials AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("FY", 2025, "Heidelberg Materials FY 2025 (Feb 25 2026)", {
        "revenue": (21_460, 21_460),
        "ebitda": (4_680, 4_680),  # Operating EBITDA
        "net_income": (1_940, 1_940),
    }),
]
EPS_DATA = [("FY", 2025, ("11.09", "12.41"), "Heidelberg Materials FY 2025 (Basic est NI/Shares / Adjusted)")]
BS_DATA = {}
