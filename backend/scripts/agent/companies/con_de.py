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
        "ebitda": (2_000, 2_000),  # Adjusted EBIT
        "net_income": (-414, 1_100),  # GAAP / Adjusted (before special effects)
        "dividends": (2_700, 2_700),  # 2.70 * ~200M shares
    }),
]
EPS_DATA = [("FY", 2025, ("0.22", "5.50"), "Continental FY 2025 GAAP (0.22) / Adjusted (5.50 est from adj NI/shares)")]
BS_DATA = {}
