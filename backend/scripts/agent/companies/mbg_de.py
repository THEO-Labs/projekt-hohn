"""Mercedes-Benz Group AG. FY End Dec 31. Automotive."""
from __future__ import annotations
TICKER = "MBG.DE"; COMPANY_NAME = "Mercedes-Benz Group AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("Q1", 2025, "Mercedes-Benz Q1 2025 (Apr 29 2025)", {
        "revenue": (33_200, 33_200),
        "ebitda": (2_300, 2_300),  # EBIT
    }),
    ("FY", 2025, "Mercedes-Benz FY 2025 (Feb 12 2026)", {
        "revenue": (132_200, 132_200),
        "ebitda": (8_200, 8_200),  # Adjusted EBIT
        "fcf": (5_400, 5_400),  # FCF Industrial
    }),
]
EPS_DATA = []
BS_DATA = {}
