"""E.ON SE. FY End Dec 31. Utility.

FIX 2026-07-08: NI + EPS ergaenzt.
"""
from __future__ import annotations
TICKER = "EOAN.DE"; COMPANY_NAME = "E.ON SE"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "E.ON FY 2025", {
        "revenue": (80_440, 80_440),
        "ebitda": (9_800, 9_800),  # Adjusted EBITDA
        "net_income": (3_000, 3_000),  # Adjusted Net Income
    }),
]
EPS_DATA = [("FY", 2025, ("1.14", "1.14"), "E.ON FY 2025 Adjusted (NI/Shares ~2.6B)")]
BS_DATA = {}
