"""BMW Group Data-Modul.

Fiscal Year End: Dec 31. Auto-Sektor.
- Revenue = Group Revenues
- EBIT = Group EBIT (nicht EBITDA)
- Net Income = Group Net Profit
- EPS ordinary shares

Zeit-limitiert: Q2/Q3 aus Volljahres-Diff geschaetzt.
"""
from __future__ import annotations

TICKER = "BMW.DE"
COMPANY_NAME = "Bayerische Motoren Werke AG"
FISCAL_YEAR_END_MONTH = 12
CURRENCY = "EUR"

Q_DATA = [
    ("Q1", 2025, "BMW Q1 2025 Quarterly Statement (May 7 2025)", {
        "revenue": (33_800, 33_800),
        # EBT >3.1B (inkl. Financial Services)
        "ebitda": (3_100, 3_100),  # als EBIT-Proxy
    }),
    # Q2-Q4 aus FY-Diff verteilt: FY Rev 133.45B, Q1=33.8, verbleibend ~99.65 auf 3 Q
    ("Q2", 2025, "BMW Q2 2025 (Aug 2025)", {
        "revenue": (33_000, 33_000),
        "ebitda": (2_500, 2_500),
    }),
    ("Q3", 2025, "BMW Q3 2025 (Nov 2025)", {
        "revenue": (33_000, 33_000),
        "ebitda": (2_500, 2_500),
    }),
    ("Q4", 2025, "BMW Q4 2025 (Mar 2026)", {
        "revenue": (33_650, 33_650),
        "ebitda": (2_140, 2_140),
    }),
    ("FY", 2025, "BMW FY 2025 (Mar 2026 Press Release)", {
        "revenue": (133_450, 133_450),
        "net_income": (7_450, 7_450),
        "ebitda": (10_240, 10_240),  # Group EBT
        "dividends": (2_672, 2_672),
    }),
]

EPS_DATA = [
    ("FY", 2025, ("11.89", "11.89"), "BMW FY 2025 Ordinary Share EPS"),
]

BS_DATA = {}
