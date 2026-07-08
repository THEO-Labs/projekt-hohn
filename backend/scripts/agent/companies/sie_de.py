"""Siemens AG Data-Modul.

Fiscal Year End: Sept 30. FY2025 = Oct 2024 - Sept 2025.
Q1 FY2025 endete Dec 31 2024, Q2: Mar 31 2025, Q3: Jun 30 2025, Q4: Sep 30 2025.

Siemens berichtet IFRS. Non-IFRS = "EPS pre PPA" (Purchase Price Allocation).
Q1 FY2025 hatte Sondereffekt: 2.1B Gain aus Innomotics-Sale.

Zeit-limitiert: nur Basis-Werte (Revenue, NI, EPS, FCF). CapEx nicht in Quarterly
gezeigt — Siemens gibt es nur in Annual Report als "additions to fixed assets".
"""
from __future__ import annotations

TICKER = "SIE.DE"
COMPANY_NAME = "Siemens AG"
FISCAL_YEAR_END_MONTH = 9
CURRENCY = "EUR"

Q_DATA = [
    ("Q1", 2025, "Siemens Q1 FY2025 Earnings Release (Feb 13 2025)", {
        "revenue": (18_400, 18_400),
        "net_income": (3_900, 3_900),  # inkl. 2.1B Innomotics-Gain
        "fcf": (1_600, 1_600),
    }),
    ("Q2", 2025, "Siemens Q2 FY2025 Earnings Release (May 8 2025)", {
        "revenue": (19_800, 19_800),
        "net_income": (2_400, 2_400),
        "fcf": (1_000, 1_000),
    }),
    ("Q3", 2025, "Siemens Q3 FY2025 Earnings Release (Aug 7 2025)", {
        "revenue": (19_400, 19_400),
        "net_income": (2_200, 2_200),
        "fcf": (2_900, 2_900),
    }),
    ("Q4", 2025, "Siemens Q4 FY2025 Earnings Release (Nov 13 2025)", {
        "revenue": (21_400, 21_400),
        "net_income": (1_800, 1_800),
        "fcf": (5_300, 5_300),
    }),
    ("FY", 2025, "Siemens FY2025 Annual (Nov 13 2025)", {
        "revenue": (78_900, 78_900),
        "net_income": (10_400, 10_400),
        "fcf": (10_800, 10_800),
    }),
    ("Q1", 2026, "Siemens Q1 FY2026 Earnings Release (Feb 2026)", {
        "revenue": (19_100, 19_100),
        "net_income": (2_200, 2_200),
        "fcf": (700, 700),  # Group FCF
    }),
    ("Q2", 2026, "Siemens Q2 FY2026 Earnings Release (May 2026)", {
        "revenue": (19_800, 19_800),
        "net_income": (2_200, 2_200),
        "fcf": (1_700, 1_700),  # Group FCF
        "ebitda": (3_000, 3_000),  # Profit Industrial Business (analog Op Profit)
    }),
]

EPS_DATA = [
    ("Q1", 2025, ("4.71", "4.86"), "Siemens Q1 FY2025"),
    ("Q2", 2025, ("2.86", "3.00"), "Siemens Q2 FY2025"),
    ("Q3", 2025, ("2.61", "2.78"), "Siemens Q3 FY2025"),
    ("Q4", 2025, ("2.07", "2.31"), "Siemens Q4 FY2025 (pre-PPA aus FY-Diff)"),
    ("FY", 2025, ("12.25", "12.95"), "Siemens FY2025"),
    ("Q1", 2026, ("2.60", "2.80"), "Siemens Q1 FY2026 Basic / Pre-PPA"),
    ("Q2", 2026, ("2.60", "2.81"), "Siemens Q2 FY2026 Basic / Pre-PPA"),
]

BS_DATA = {}  # TODO Annual Report

Q_DATA_ESTIMATE = [
    ("Q3", 2026, "Siemens Q3 FY2026 est (Apr-Jun 2026)", {
        "revenue": (20000, 20000),
        "ebitda": (3100, 3100),
        "net_income": (2300, 2300),
    }),
]

EPS_DATA_ESTIMATE = [
    ("Q3", 2026, ("2.70", "2.70"), "Siemens Q3 FY2026 est (Apr-Jun 2026)"),
]
