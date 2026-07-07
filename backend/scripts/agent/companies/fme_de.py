"""Fresenius Medical Care AG. FY End Dec 31. Healthcare (Dialysis)."""
from __future__ import annotations
TICKER = "FME.DE"; COMPANY_NAME = "Fresenius Medical Care AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "FMC FY 2025", {
        "revenue": (19_600, 19_600),
        "ebitda": (1_830, 1_830),  # Operating income
        "net_income": (978, 978),
    }),
]
EPS_DATA = [("FY", 2025, ("3.36", "4.28"), "FMC FY 2025 Basic / Excl-Special")]
BS_DATA = {}
