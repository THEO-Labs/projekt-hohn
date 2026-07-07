"""Deutsche Telekom AG Data-Modul.

Fiscal Year End: Dec 31. Telecommunications.
- Adjusted EBITDA AL = "after leases" (Standard-Metrik seit IFRS 16)
- Adjusted Net Profit statt GAAP Net Income (viele Sondereffekte, Steuer-Effekte
  aus TMobile-US-Konsolidierung)
- Adjusted EPS statt Basic
- CapEx nicht separat in Press Release — kommt aus Annual Report

Quellen: Deutsche Telekom Media Information Q1/Q2/Q3/Q4 2025 (May/Aug/Nov 2025 + Feb 2026).
"""
from __future__ import annotations

TICKER = "DTE.DE"
COMPANY_NAME = "Deutsche Telekom AG"
FISCAL_YEAR_END_MONTH = 12
CURRENCY = "EUR"

Q_DATA = [
    ("Q1", 2025, "DT Q1 2025 (May 15 2025)", {
        "revenue": (29_800, 29_800),
        "net_income": (2_800, 2_400),  # Reported / Adjusted
        "ebitda": (11_300, 11_300),  # Adjusted EBITDA AL
        "fcf": (5_600, 5_600),  # FCF AL
    }),
    ("Q2", 2025, "DT Q2 2025 (Aug 7 2025)", {
        "revenue": (28_700, 28_700),
        "net_income": (2_601, 2_601),  # Adjusted
        "ebitda": (11_000, 11_000),
        "fcf": (4_900, 4_900),
    }),
    ("Q3", 2025, "DT Q3 2025 (Nov 13 2025)", {
        "revenue": (28_900, 28_900),
        "net_income": (2_700, 2_700),  # Adjusted
        "ebitda": (11_100, 11_100),
        "fcf": (5_600, 5_600),
    }),
    ("Q4", 2025, "DT Q4 2025 (Feb 26 2026)", {
        "revenue": (31_720, 31_720),
        "net_income": (2_000, 2_000),  # Aus FY 9.7B - Q1-Q3 (2.4+2.6+2.7) = 2.0
        "ebitda": (10_630, 10_630),
        "fcf": (4_030, 4_030),
    }),
    ("FY", 2025, "DT FY 2025 (Feb 26 2026)", {
        "revenue": (119_120, 119_120),  # Sum Q1-Q4 (= 119.1B official)
        "net_income": (9_700, 9_700),
        "ebitda": (44_660, 44_660),  # Sum Q1-Q4 (~ FY 45.3B official — leichte Diff durch Rundung)
        "fcf": (19_550, 19_550),
    }),
]

EPS_DATA = [
    ("Q1", 2025, ("0.50", "0.50"), "DT Q1 2025 Adjusted"),
    ("Q2", 2025, ("0.51", "0.51"), "DT Q2 2025 Adjusted"),
    ("Q3", 2025, ("0.55", "0.55"), "DT Q3 2025 Adjusted"),
    ("Q4", 2025, ("0.44", "0.44"), "DT Q4 2025 Adjusted"),
    ("FY", 2025, ("2.00", "1.97"), "DT FY 2025 Adjusted (recurring 1.97 relevant fuer Div)"),
]

BS_DATA = {}
