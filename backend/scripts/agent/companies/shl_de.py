"""Siemens Healthineers AG. FY End Sep 30. Medical.

FIX 2026-07-08: FY Rev + NI + Q4 ergaenzt.
"""
from __future__ import annotations
TICKER = "SHL.DE"; COMPANY_NAME = "Siemens Healthineers AG"; FISCAL_YEAR_END_MONTH = 9; CURRENCY = "EUR"

Q_DATA = [
    ("Q2", 2025, "Siemens Healthineers Q2 FY2025", {
        "revenue": (5_910, 5_910),
    }),
    ("Q3", 2025, "Siemens Healthineers Q3 FY2025", {
        "revenue": (5_660, 5_660),
    }),
    ("Q4", 2025, "Siemens Healthineers Q4 FY2025 (Nov 2025)", {
        "revenue": (6_322, 6_322),
        "net_income": (597, 597),
    }),
    ("FY", 2025, "Siemens Healthineers FY 2025 (Nov 2025)", {
        "revenue": (23_375, 23_375),
        "net_income": (2_168, 2_168),
    }),
    ("Q1", 2026, "Siemens Healthineers Q1 FY 2026 (Feb 5 2026)", {
        "revenue": (5_400, 5_400),
        "net_income": (456, 456),
    }),
    ("Q2", 2026, "Siemens Healthineers Q2 FY 2026 (May 2026, Jan-Mar 2026)", {
        "revenue": (5_681, 5_681),
        "net_income": (512, 512),
        "ebitda": (900, 900),  # Adj EBIT est (~15.8% margin)
    }),
]
EPS_DATA = [
    ("Q3", 2025, ("0.49", "0.49"), "Siemens Healthineers Q3 FY2025"),
    ("FY", 2025, ("1.92", "2.35"), "Siemens Healthineers FY2025 Basic / Adjusted"),
    ("Q1", 2026, ("0.40", "0.49"), "Siemens Healthineers Q1 FY2026 Basic / Adjusted"),
    ("Q2", 2026, ("0.45", "0.53"), "Siemens Healthineers Q2 FY2026 Basic / Adjusted"),
]
BS_DATA = {}

Q_DATA_ESTIMATE = [
    ("Q3", 2026, "SHL Q3 FY2026 est (Apr-Jun 2026)", {
        "revenue": (5800, 5800),
        "ebitda": (950, 950),
        "net_income": (550, 550),
    }),
    ("Q4", 2026, "SHL Q4 FY2026 est (Jul-Sep 2026)", {
        "revenue": (6400, 6400),
        "ebitda": (1150, 1150),
        "net_income": (620, 620),
    }),
]

EPS_DATA_ESTIMATE = [
    ("Q3", 2026, ("0.48", "0.48"), "SHL Q3 FY2026 est (Apr-Jun 2026)"),
    ("Q4", 2026, ("0.54", "0.54"), "SHL Q4 FY2026 est (Jul-Sep 2026)"),
]
