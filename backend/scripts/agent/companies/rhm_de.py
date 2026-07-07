"""Rheinmetall AG. FY End Dec 31. Aerospace/Defence."""
from __future__ import annotations
TICKER = "RHM.DE"; COMPANY_NAME = "Rheinmetall AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("Q1", 2025, "Rheinmetall Q1 2025", {
        "revenue": (2_305, 2_305),
        "ebitda": (199, 199),  # Operating result
    }),
    # 9M: Rev 7.5B, Op 835M -> Q2+Q3 = 7.5 - 2.305 = 5.195; Op 835 - 199 = 636
    ("Q4", 2025, "Rheinmetall Q4 2025", {
        "revenue": (2_420, 2_420),
        "ebitda": (1_005, 1_005),  # FY 1.84B - 9M 835M = 1005
    }),
    ("FY", 2025, "Rheinmetall FY 2025 (Mar 11 2026)", {
        "revenue": (9_935, 9_935),
        "ebitda": (1_840, 1_840),  # Operating result
        "net_income": (696, 696),
    }),
]
EPS_DATA = [("Q1", 2025, ("1.91", "1.91"), "Rheinmetall Q1 2025 Diluted continuing")]
BS_DATA = {}
