"""BASF SE Data-Modul. FY End Dec 31. Chemicals."""
from __future__ import annotations
TICKER = "BAS.DE"; COMPANY_NAME = "BASF SE"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("Q1", 2025, "BASF Q1 2025 (May 2 2025)", {
        "ebitda": (2_177, 2_177),
    }),
    ("Q2", 2025, "BASF Q2 2025 (Jul/Aug 2025)", {
        "revenue": (15_800, 15_800),
        "net_income": (79, 79),
        "ebitda": (1_800, 1_800),  # EBITDA before spec items
    }),
    ("Q3", 2025, "BASF Q3 2025 (Oct 29 2025)", {
        "revenue": (14_328, 14_328),
    }),
    ("FY", 2025, "BASF FY 2025 Preliminary (Jan 27 2026)", {
        "revenue": (59_700, 59_700),
        "net_income": (1_600, 1_600),
        "ebitda": (6_600, 6_600),  # EBITDA before spec items
    }),
]
EPS_DATA = [
    ("Q1", 2025, ("0.91", "0.91"), "BASF Q1 2025"),
    ("Q3", 2025, ("0.19", "0.19"), "BASF Q3 2025"),
]
BS_DATA = {}
