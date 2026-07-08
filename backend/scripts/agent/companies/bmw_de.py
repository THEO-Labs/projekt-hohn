"""BMW Group Data-Modul. FY End Dec 31. Automotive."""
from __future__ import annotations

TICKER = "BMW.DE"
COMPANY_NAME = "Bayerische Motoren Werke AG"
FISCAL_YEAR_END_MONTH = 12
CURRENCY = "EUR"

Q_DATA = [
    ("Q1", 2025, "BMW Q1 2025 Quarterly Statement (May 7 2025)", {
        "revenue": (33_800, 33_800),
        "ebitda": (3_100, 3_100),  # Group EBT (>3.1B inkl Financial Services)
    }),
    ("FY", 2025, "BMW FY 2025 (Mar 2026 Press Release)", {
        "revenue": (133_450, 133_450),
        "net_income": (7_450, 7_450),
        "ebitda": (10_240, 10_240),  # Group EBT
        "dividends": (2_672, 2_672),
    }),
    ("Q1", 2026, "BMW Q1 2026 (May 6 2026)", {
        "revenue": (31_007, 31_007),
        "ebitda": (2_348, 2_348),  # Group EBT
        "net_income": (1_672, 1_672),
    }),
]

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "BMW Q2 2026 Consensus (Rev 33.16B, EPS 2.14 via Yahoo)", {
        "revenue": (33_160, 33_160),
        "ebitda": (1_500, 1_500),  # Group EBT est (~4.5% margin on Rev)
        "net_income": (1_334, 1_334),
    }),
]

EPS_DATA = [
    ("FY", 2025, ("11.89", "11.89"), "BMW FY 2025 Ordinary Share EPS"),
    ("Q1", 2026, ("2.68", "2.68"), "BMW Q1 2026"),
]

EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("2.14", "2.14"), "BMW Q2 2026 Consensus"),
]

BS_DATA = {}
