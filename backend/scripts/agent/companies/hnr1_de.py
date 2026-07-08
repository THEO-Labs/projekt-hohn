"""Hannover Rueck SE. FY End Dec 31. Reinsurance."""
from __future__ import annotations
TICKER = "HNR1.DE"; COMPANY_NAME = "Hannover Rueck SE"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("Q1", 2025, "Hannover Re Q1 2025", {
        "net_income": (480, 480),
    }),
    ("FY", 2025, "Hannover Re FY 2025", {
        "revenue": (26_800, 26_800),  # Reinsurance revenue
        "net_income": (2_640, 2_640),  # Group NI
        "ebitda": (3_500, 3_500),  # Operating profit (EBIT)
    }),
    ("Q1", 2026, "Hannover Re Q1 2026 (May 2026)", {
        "revenue": (6_500, 6_500),
        "net_income": (711, 711),  # Group NI (+47.9% YoY)
    }),
]
EPS_DATA = [
    ("Q1", 2026, ("5.89", "5.89"), "Hannover Re Q1 2026"),
]
BS_DATA = {}
