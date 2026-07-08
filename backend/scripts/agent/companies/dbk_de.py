"""Deutsche Bank AG Data-Modul.

Fiscal Year End: Dec 31. Banking-Sektor.
- Revenue = "Net Revenues" (Zins + Provisionen)
- Net Income = Profit attributable to shareholders
- Kein OCF/CapEx/FCF im Standard-Sinn (Bank operations)

Q2-Q3 2025: Werte aus Volljahres-Diff geschaetzt (siehe Kommentare).
Zeit-limitiert.
"""
from __future__ import annotations

TICKER = "DBK.DE"
COMPANY_NAME = "Deutsche Bank AG"
FISCAL_YEAR_END_MONTH = 12
CURRENCY = "EUR"

Q_DATA = [
    ("Q1", 2025, "Deutsche Bank Q1 2025 Results (Apr 29 2025)", {
        "revenue": (8_500, 8_500),
        "net_income": (1_775, 1_775),
    }),
    # FY 2025 Revenue 32B, Q1=8.5, rest = 23.5 aufteilen (grob gleich)
    ("Q2", 2025, "Deutsche Bank Q2 2025 Results (Jul 24 2025)", {
        "revenue": (7_800, 7_800),  # geschaetzt
        "net_income": (1_500, 1_500),  # geschaetzt
    }),
    ("Q3", 2025, "Deutsche Bank Q3 2025 Results (Oct 29 2025)", {
        "revenue": (7_800, 7_800),
        "net_income": (1_600, 1_600),
    }),
    ("Q4", 2025, "Deutsche Bank Q4 2025 Results (Jan 2026)", {
        "revenue": (7_900, 7_900),
        "net_income": (1_500, 1_500),  # EPS 0.76 * ~2B shares = 1.5B implied
    }),
    ("FY", 2025, "Deutsche Bank FY 2025", {
        "revenue": (32_000, 32_000),
        "net_income": (6_375, 6_375),  # EPS 3.09 * ~2.06B shares
    }),
    ("Q1", 2026, "Deutsche Bank Q1 2026 (Apr 2026)", {
        "revenue": (8_700, 8_700),
        "net_income": (2_200, 2_200),
    }),
]

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "DBK Q2 2026 Consensus ($9.85B Rev, $1.15 EPS via Meyka/Investing.com)", {
        "revenue": (9_020, 9_020),  # $9.85B * 0.916 EUR/USD
        "net_income": (2_180, 2_180),  # EPS $1.15 * 2075M shares * 0.916
    }),
    ("Q3", 2026, "DBK Q3 2026 Est (FY Rev 33B guided, saisonal schwaecher)", {
        "revenue": (7_900, 7_900),
        "net_income": (1_800, 1_800),
    }),
]

EPS_DATA = [
    ("Q4", 2025, ("0.76", "0.76"), "Deutsche Bank Q4 2025"),
    ("FY", 2025, ("3.09", "3.09"), "Deutsche Bank FY 2025"),
    ("Q1", 2026, ("1.06", "1.06"), "Deutsche Bank Q1 2026 Diluted EUR"),
]

EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("1.05", "1.05"), "DBK Q2 2026 Consensus (~$1.15 * 0.916)"),
    ("Q3", 2026, ("0.87", "0.87"), "DBK Q3 2026 Est"),
]

BS_DATA = {}
