"""Vonovia SE. FY End Dec 31. Real Estate."""
from __future__ import annotations
TICKER = "VNA.DE"; COMPANY_NAME = "Vonovia SE"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "Vonovia FY 2025", {
        "revenue": (4_918, 4_918),  # Rental revenue
        "net_income": (3_723, 3_723),
        "ebitda": (2_801, 2_801),  # Adjusted EBITDA
    }),
]
EPS_DATA = [("FY", 2025, ("4.33", "1.85"), "Vonovia FY 2025 GAAP / Adjusted")]
BS_DATA = {}
