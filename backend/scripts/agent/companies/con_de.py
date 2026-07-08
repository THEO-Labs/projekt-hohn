"""Continental AG. FY End Dec 31. Auto Parts.

FIX 2026-07-08: NI-Korrektur. GAAP Net Income to Common (excl Extra Items) = -414M.
"Net Income before non-cash Special Effects" = 1.1B ist adjusted / Non-GAAP.
EPS 0.22 basiert auf GAAP (nach Special Effects).
"""
from __future__ import annotations
TICKER = "CON.DE"; COMPANY_NAME = "Continental AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("FY", 2025, "Continental FY 2025 (Mar 4 2026)", {
        "revenue": (19_700, 19_700),
        "ebitda": (2_000, 2_000),
        "net_income": (-414, 1_100),
        "dividends": (2_700, 2_700),
    }),
    ("Q1", 2026, "Continental Q1 2026 (May 2026)", {
        "revenue": (4_400, 4_400),
        "ebitda": (522, 522),  # Adjusted EBIT (11.9% margin)
    }),
]
EPS_DATA = [("FY", 2025, ("0.22", "5.50"), "Continental FY 2025 GAAP / Adjusted")]
BS_DATA = {}
