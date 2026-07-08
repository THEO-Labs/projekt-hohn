"""Munich Re (Muenchener Rueckversicherungs AG) Data-Modul.

Fiscal Year End: Dec 31. Reinsurance/Insurance.
- Revenue = Insurance revenue from contracts issued (IFRS 17)
- Kein OCF/CapEx/FCF im Standard-Sinn (Reinsurance operations)
- EPS aus Annual Report (nur FY offiziell im Financial Highlights)

Quellen: Munich Re Quarterly Statements Q1-Q3 2025 + Annual Report 2025.
"""
from __future__ import annotations

TICKER = "MUV2.DE"
COMPANY_NAME = "Muenchener Rueckversicherungs AG"
FISCAL_YEAR_END_MONTH = 12
CURRENCY = "EUR"

Q_DATA = [
    ("Q1", 2025, "Munich Re Q1 2025 Quarterly Statement (May 2025)", {
        "revenue": (15_811, 15_811),  # Insurance revenue
        "net_income": (1_094, 1_094),
    }),
    ("Q2", 2025, "Munich Re Q2 2025 (Aug 2025)", {
        "revenue": (14_775, 14_775),
        "net_income": (2_085, 2_085),
    }),
    ("Q3", 2025, "Munich Re Q3 2025 (Nov 2025)", {
        "revenue": (14_575, 14_575),
        "net_income": (1_997, 1_997),
    }),
    ("Q4", 2025, "Munich Re Q4 2025 Implied from FY - Sigma Q1-Q3", {
        "revenue": (15_239, 15_239),  # FY 60.4 - Sigma 45.161
        "net_income": (945, 945),  # FY 6.121 - Sigma 5.176
    }),
    ("FY", 2025, "Munich Re FY 2025 (Feb 26 2026)", {
        "revenue": (60_400, 60_400),
        "net_income": (6_121, 6_121),
    }),
    ("Q1", 2026, "Munich Re Q1 2026 (May 12 2026)", {
        "revenue": (15_018, 15_018),
        "net_income": (1_714, 1_714),
        "ebitda": (2_230, 2_230),  # Operating result
    }),
]

EPS_DATA = [
    ("FY", 2025, ("47.15", "47.15"), "Munich Re FY 2025"),
    ("Q1", 2026, ("13.00", "13.00"), "Munich Re Q1 2026 est (NI 1.714B / ~132M shares)"),
]

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Munich Re Q2 2026 Est (FY 6.3B Guidance, Q2 ~1.5-1.7B)", {
        "revenue": (15_000, 15_000),
        "net_income": (1_600, 1_600),
        "ebitda": (2_100, 2_100),  # Operating profit est (Q1 2026 was 2230)
    }),
]

EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("12.10", "12.10"), "Munich Re Q2 2026 Est"),
]

BS_DATA = {}
