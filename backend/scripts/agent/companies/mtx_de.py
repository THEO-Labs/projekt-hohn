"""MTU Aero Engines AG. FY End Dec 31. Aerospace."""
from __future__ import annotations
TICKER = "MTX.DE"; COMPANY_NAME = "MTU Aero Engines AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "MTU FY 2025", {
        "revenue": (8_700, 8_700),  # Adjusted revenue
        "ebitda": (1_350, 1_350),  # Adjusted EBIT
        "net_income": (968, 968),  # Adjusted NI
        "fcf": (378, 378),
    }),
]
EPS_DATA = [("FY", 2025, ("4.58", "4.58"), "MTU FY 2025")]
BS_DATA = {}
