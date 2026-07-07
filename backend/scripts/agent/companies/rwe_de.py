"""RWE AG. FY End Dec 31. Utility."""
from __future__ import annotations
TICKER = "RWE.DE"; COMPANY_NAME = "RWE AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("Q1", 2025, "RWE Q1 2025", {
        "ebitda": (1_310, 1_310),  # Adj EBITDA
    }),
    ("Q4", 2025, "RWE Q4 2025 implied FY-9M", {
        "ebitda": (1_600, 1_600),  # FY 5.1B - 9M 3.5B
        "net_income": (500, 500),  # FY 1.8B - 9M 1.3B
    }),
    ("FY", 2025, "RWE FY 2025 (Mar 12 2026)", {
        "ebitda": (5_100, 5_100),  # Adjusted EBITDA
        "net_income": (1_800, 1_800),  # Adjusted NI
    }),
]
EPS_DATA = [("FY", 2025, ("2.48", "2.48"), "RWE FY 2025 Adjusted EPS")]
BS_DATA = {}
