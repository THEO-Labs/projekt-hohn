"""Allianz SE Data-Modul.

Fiscal Year End: Dec 31. Insurance-Sektor.
- "Operating Profit" statt Standard-EBITDA/EBIT
- "Shareholders' Core Net Income" statt Net Income
- "Core EPS" statt Standard-EPS
- Kein OCF/CapEx/FCF im Standard-Sinn (Insurance operations)
- Dividende und Buyback im Q4/FY reported

Zeit-limitiert: EPS Q2 aus 6M-Kumulativ abgeleitet.
"""
from __future__ import annotations

TICKER = "ALV.DE"
COMPANY_NAME = "Allianz SE"
FISCAL_YEAR_END_MONTH = 12
CURRENCY = "EUR"

Q_DATA = [
    ("Q1", 2025, "Allianz Q1 2025 Earnings Release (May 15 2025)", {
        "revenue": (48_500, 48_500),  # Total business volume — Allianz's Revenue-Aequivalent
        "net_income": (2_600, 2_600),  # Shareholders' core net income
        "ebitda": (4_200, 4_200),  # Operating profit
    }),
    ("Q2", 2025, "Allianz Q2 2025 Earnings Release (Aug 7 2025)", {
        "revenue": (44_500, 44_500),
        "net_income": (3_000, 3_000),
        "ebitda": (4_400, 4_400),
    }),
    # Q3 + Q4 aus FY: Op-Profit 17.4B - Q1(4.2) - Q2(4.4) = 8.8B fuer 6M-H2, ~4.4 pro Q
    ("Q3", 2025, "Allianz Q3 2025 Earnings Release (Nov 14 2025)", {
        "ebitda": (4_300, 4_300),  # geschaetzt aus 6M-H2
    }),
    ("Q4", 2025, "Allianz Q4/FY 2025 Earnings Release (Feb 26 2026)", {
        "ebitda": (4_500, 4_500),  # geschaetzt aus 6M-H2
    }),
    ("FY", 2025, "Allianz FY 2025 (Feb 26 2026)", {
        "net_income": (11_100, 11_100),  # Shareholders' core net income
        "ebitda": (17_400, 17_400),  # Operating profit
    }),
    ("Q1", 2026, "Allianz Q1 2026 (May 13 2026)", {
        "net_income": (3_800, 3_800),  # Shareholders' core NI (+48.4% YoY)
        "ebitda": (4_500, 4_500),  # Operating profit
    }),
]

EPS_DATA = [
    ("Q1", 2025, ("6.61", "6.61"), "Allianz Q1 2025"),
    ("Q2", 2025, ("7.38", "7.38"), "Allianz Q2 2025 (Q2 standalone = 6M 13.99 - Q1 6.61)"),
    ("Q1", 2026, ("9.96", "9.96"), "Allianz Q1 2026 Core EPS"),
]

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Allianz Q2 2026 Est (FY 17.4B Op-Profit Guidance / 4)", {
        "revenue": (44_500, 44_500),  # Business Volume ~ Q2 2025 level
        "net_income": (3_100, 3_100),
        "ebitda": (4_500, 4_500),
    }),
    ("Q3", 2026, "Allianz Q3 2026 est (solide, Insurance im Trend)", {
        "revenue": (45000, 45000),
        "ebitda": (4400, 4400),
        "net_income": (3000, 3000),
    }),
]

EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("8.20", "8.20"), "Allianz Q2 2026 Est"),
    ("Q3", 2026, ("7.90", "7.90"), "Allianz Q3 2026 est (solide, Insurance im Trend)"),
]

BS_DATA = {}
