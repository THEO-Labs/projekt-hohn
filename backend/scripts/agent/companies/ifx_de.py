"""Infineon Technologies AG. FY End Sep 30. Semiconductors."""
from __future__ import annotations
TICKER = "IFX.DE"; COMPANY_NAME = "Infineon Technologies AG"; FISCAL_YEAR_END_MONTH = 9; CURRENCY = "EUR"

Q_DATA = [
    ("Q4", 2025, "Infineon Q4 FY 2025 (Nov 12 2025)", {
        "revenue": (3_943, 3_943),
        "ebitda": (717, 717),  # Segment Result
    }),
    ("FY", 2025, "Infineon FY 2025 (Nov 12 2025)", {
        "revenue": (14_662, 14_662),
        "ebitda": (2_560, 2_560),  # Segment Result
    }),
    ("Q2", 2026, "Infineon Q2 FY2026 (May 2026, Jan-Mar 2026)", {
        "revenue": (3_812, 3_812),
        "ebitda": (653, 653),  # Segment Result
        "net_income": (403, 403),  # ~EPS 0.31 * 1300M shares
    }),
]
EPS_DATA = [
    ("FY", 2025, ("1.39", "1.39"), "Infineon FY 2025 Adjusted EPS"),
    ("Q2", 2026, ("0.31", "0.31"), "Infineon Q2 FY2026 (~$0.34 * 0.916)"),
]
BS_DATA = {}

Q_DATA_ESTIMATE = [
    ("Q3", 2026, "Infineon Q3 FY2026 est (Apr-Jun 2026, AI acceleration)", {
        "revenue": (3900, 3900),
        "ebitda": (700, 700),
        "net_income": (430, 430),
    }),
]

EPS_DATA_ESTIMATE = [
    ("Q3", 2026, ("0.33", "0.33"), "Infineon Q3 FY2026 est (Apr-Jun 2026, AI acceleration)"),
]
