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
    ("Q1", 2026, "Bayer Q1 2026 (May 2026)", {
        "revenue": (13_405, 13_405),
        "net_income": (2_763, 2_763),
        "ebitda": (4_453, 4_453),  # EBITDA before Special Items
    }),
]
EPS_DATA = [
    ("FY", 2025, ("-3.68", "4.91"), "Bayer FY 2025 GAAP est / Core"),
    ("Q1", 2026, ("2.71", "2.71"), "Bayer Q1 2026 Core EPS"),
]

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Bayer Q2 2026 Est (FY Guidance 45-47B Rev, 9.6-10.1B EBITDA, 4.30-4.80 Core EPS)", {
        "revenue": (11_400, 11_400),  # ~46B / 4
        "ebitda": (2_450, 2_450),  # ~9.85B / 4
        "net_income": (1_100, 1_100),  # Estimated (Q2 seasonal weak)
    }),
    ("Q3", 2026, "Bayer Q3 2026 est (Crop Science schwaecher Q3, Roundup Settlement gut)", {
        "revenue": (10500, 10500),
        "ebitda": (2100, 2100),
        "net_income": (800, 800),
    }),
    ("Q4", 2026, "Bayer Q4 2026 est (Crop schwach Q4, FY EBITDA 9.6-10.1B)", {
        "revenue": (10800, 10800),
        "ebitda": (1600, 1600),
        "net_income": (500, 500),
    }),
]

EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("1.10", "1.10"), "Bayer Q2 2026 Est (FY 4.55 mid / 4)"),
    ("Q3", 2026, ("0.85", "0.85"), "Bayer Q3 2026 est (Crop Science schwaecher Q3, Roundup Settlement gut)"),
    ("Q4", 2026, ("0.53", "0.53"), "Bayer Q4 2026 est (Crop schwach Q4, FY EBITDA 9.6-10.1B)"),
]
BS_DATA = {}
