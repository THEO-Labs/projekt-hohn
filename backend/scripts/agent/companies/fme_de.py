"""Fresenius Medical Care AG. FY End Dec 31. Healthcare (Dialysis)."""
from __future__ import annotations
TICKER = "FME.DE"; COMPANY_NAME = "Fresenius Medical Care AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "FMC FY 2025", {
        "revenue": (19_600, 19_600), "ebitda": (1_830, 1_830), "net_income": (978, 978),
    }),
    ("Q1", 2026, "FMC Q1 2026 (May 2026)", {
        "revenue": (4_610, 4_610),
        "ebitda": (286, 467),  # Op Income reported / excl special
        "net_income": (126, 267),  # ~EPS 0.43 / 0.91 * 293M shares
    }),
]
EPS_DATA = [
    ("FY", 2025, ("3.36", "4.28"), "FMC FY 2025 Basic / Excl-Special"),
    ("Q1", 2026, ("0.43", "0.91"), "FMC Q1 2026 Basic / Excl-Special"),
]
BS_DATA = {}

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "FMC Q2 2026 est", {
        "revenue": (4650, 4650),
        "ebitda": (300, 300),
        "net_income": (130, 130),
    }),
    ("Q3", 2026, "FMC Q3 2026 est", {
        "revenue": (4700, 4700),
        "ebitda": (310, 310),
        "net_income": (135, 135),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("0.44", "0.44"), "FMC Q2 2026 est"),
    ("Q3", 2026, ("0.46", "0.46"), "FMC Q3 2026 est"),
]
