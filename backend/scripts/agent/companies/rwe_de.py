"""RWE AG. FY End Dec 31. Utility."""
from __future__ import annotations
TICKER = "RWE.DE"; COMPANY_NAME = "RWE AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("Q1", 2025, "RWE Q1 2025", {"ebitda": (1_310, 1_310)}),
    ("Q4", 2025, "RWE Q4 2025 implied FY-9M", {"ebitda": (1_600, 1_600), "net_income": (500, 500)}),
    ("FY", 2025, "RWE FY 2025 (Mar 12 2026)", {"ebitda": (5_100, 5_100), "net_income": (1_800, 1_800)}),
    ("Q1", 2026, "RWE Q1 2026 (May 13 2026)", {
        "ebitda": (1_631, 1_631),  # Adjusted EBITDA
        "net_income": (608, 608),  # Adjusted NI
    }),
]
EPS_DATA = [
    ("FY", 2025, ("2.48", "2.48"), "RWE FY 2025 Adjusted EPS"),
    ("Q1", 2026, ("0.85", "0.85"), "RWE Q1 2026 Adjusted EPS"),
]
BS_DATA = {}
