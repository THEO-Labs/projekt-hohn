"""E.ON SE. FY End Dec 31. Utility."""
from __future__ import annotations
TICKER = "EOAN.DE"; COMPANY_NAME = "E.ON SE"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "E.ON FY 2025", {
        "revenue": (80_440, 80_440),
        "ebitda": (9_800, 9_800),  # Adjusted EBITDA
        "net_income": (3_000, 3_000),  # Adjusted Net Income
    }),
    ("Q1", 2026, "E.ON Q1 2026 (May 7 2026)", {
        "revenue": (21_800, 21_800),
        "ebitda": (3_300, 3_300),  # Adjusted EBITDA
        "net_income": (1_340, 1_340),  # Adjusted NI
    }),
]
EPS_DATA = [
    ("FY", 2025, ("1.14", "1.14"), "E.ON FY 2025 Adjusted"),
    ("Q1", 2026, ("0.51", "0.51"), "E.ON Q1 2026 Adjusted EPS"),
]

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "E.ON Q2 2026 Est (FY Guidance 9.4-9.6B EBITDA, 2.7-2.9B NI, 1.03-1.11 EPS)", {
        "revenue": (18_000, 18_000),
        "ebitda": (2_300, 2_300),
        "net_income": (630, 630),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("0.24", "0.24"), "E.ON Q2 2026 Est"),
]
BS_DATA = {}
