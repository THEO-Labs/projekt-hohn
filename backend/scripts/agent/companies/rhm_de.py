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
    ("Q1", 2026, "Rheinmetall Q1 2026 (May 7 2026)", {
        "revenue": (1_938, 1_938),
        "ebitda": (224, 224),  # Operating result
        "net_income": (111, 111),
    }),
]
EPS_DATA = [
    ("Q1", 2025, ("1.91", "1.91"), "Rheinmetall Q1 2025 Diluted continuing"),
    ("Q1", 2026, ("2.18", "2.18"), "Rheinmetall Q1 2026 Diluted continuing"),
]
BS_DATA = {}

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Rheinmetall Q2 2026 est (Defence Wachstum)", {
        "revenue": (2300, 2300),
        "ebitda": (280, 280),
        "net_income": (140, 140),
    }),
    ("Q3", 2026, "Rheinmetall Q3 2026 est (Naval orders + defence pickup)", {
        "revenue": (3100, 3100),
        "ebitda": (400, 400),
        "net_income": (200, 200),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("2.80", "2.80"), "Rheinmetall Q2 2026 est (Defence Wachstum)"),
    ("Q3", 2026, ("3.90", "3.90"), "Rheinmetall Q3 2026 est (Naval orders + defence pickup)"),
]
