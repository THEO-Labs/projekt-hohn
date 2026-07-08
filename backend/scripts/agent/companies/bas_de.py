"""BASF SE Data-Modul. FY End Dec 31. Chemicals."""
from __future__ import annotations
TICKER = "BAS.DE"; COMPANY_NAME = "BASF SE"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("Q1", 2025, "BASF Q1 2025 (May 2 2025)", {
        "ebitda": (2_177, 2_177),
    }),
    ("Q2", 2025, "BASF Q2 2025 (Jul/Aug 2025)", {
        "revenue": (15_800, 15_800),
        "net_income": (79, 79),
        "ebitda": (1_800, 1_800),  # EBITDA before spec items
    }),
    ("Q3", 2025, "BASF Q3 2025 (Oct 29 2025)", {
        "revenue": (14_328, 14_328),
    }),
    ("FY", 2025, "BASF FY 2025 Preliminary (Jan 27 2026)", {
        "revenue": (59_700, 59_700),
        "net_income": (1_600, 1_600),
        "ebitda": (6_600, 6_600),  # EBITDA before spec items
    }),
    ("Q1", 2026, "BASF Q1 2026 (Apr 30 2026)", {
        "revenue": (16_020, 16_020),
        "ebitda": (2_356, 2_186),  # EBITDA before spec / EBITDA with special items
        "net_income": (927, 927),
    }),
]
EPS_DATA = [
    ("Q1", 2025, ("0.91", "0.91"), "BASF Q1 2025"),
    ("Q3", 2025, ("0.19", "0.19"), "BASF Q3 2025"),
    ("Q1", 2026, ("1.06", "1.06"), "BASF Q1 2026"),
]

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "BASF Q2 2026 Est (FY 6.2-7.0B EBITDA Guidance, Q2 similar to Q1)", {
        "revenue": (15_800, 15_800),  # ~Q2 2025 level
        "ebitda": (1_650, 1_650),  # ~6.6B / 4
        "net_income": (400, 400),
    }),
]

EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("0.45", "0.45"), "BASF Q2 2026 Est"),
]
BS_DATA = {}
