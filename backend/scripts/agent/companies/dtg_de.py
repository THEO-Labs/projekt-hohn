"""Daimler Truck Holding AG. FY End Dec 31. Trucks."""
from __future__ import annotations
TICKER = "DTG.DE"; COMPANY_NAME = "Daimler Truck Holding AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("FY", 2025, "Daimler Truck FY 2025", {
        "revenue": (49_400, 49_400),
        "ebitda": (3_800, 3_800),  # Adjusted EBIT
        "net_income": (2_000, 2_000),
        "fcf": (1_800, 1_800),  # FCF Industrial
    }),
]
EPS_DATA = [("FY", 2025, ("2.56", "2.56"), "Daimler Truck FY 2025")]
BS_DATA = {}
