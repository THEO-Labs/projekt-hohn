"""Bayer AG. FY End Dec 31. Pharma/Crop Science.

FIX 2026-07-08: FY EBITDA before Special Items ergaenzt.
"""
from __future__ import annotations
TICKER = "BAYN.DE"; COMPANY_NAME = "Bayer AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("Q3", 2025, "Bayer Q3 2025 Quarterly Statement", {
        "ebitda": (1_511, 1_511),  # EBITDA before special items
    }),
    ("FY", 2025, "Bayer FY 2025 Annual Report", {
        "revenue": (45_575, 45_575),
        "net_income": (-3_620, -3_620),  # GAAP loss due to litigation
        "ebitda": (9_669, 9_669),  # EBITDA before Special Items (Core)
    }),
]
EPS_DATA = [
    ("FY", 2025, ("-3.68", "4.91"), "Bayer FY 2025 GAAP est / Core"),
]
BS_DATA = {}
