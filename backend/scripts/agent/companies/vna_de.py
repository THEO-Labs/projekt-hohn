"""Vonovia SE. FY End Dec 31. Real Estate."""
from __future__ import annotations
TICKER = "VNA.DE"; COMPANY_NAME = "Vonovia SE"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("FY", 2025, "Vonovia FY 2025", {
        "revenue": (4_918, 4_918), "net_income": (3_723, 3_723), "ebitda": (2_801, 2_801),
    }),
    ("Q1", 2026, "Vonovia Q1 2026 (May 2026)", {
        "revenue": (1_460, 1_460),
        "net_income": (211, 211),
        "ebitda": (712, 712),
    }),
]
EPS_DATA = [
    ("FY", 2025, ("4.33", "1.85"), "Vonovia FY 2025 GAAP / Adjusted"),
    ("Q1", 2026, ("0.25", "0.39"), "Vonovia Q1 2026 GAAP / Adjusted"),
]

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Vonovia Q2 2026 Est (Q1 base + FY guidance)", {
        "revenue": (1_460, 1_460),
        "ebitda": (720, 720),
        "net_income": (200, 200),
    }),
    ("Q3", 2026, "Vonovia Q3 2026 est", {
        "revenue": (1450, 1450),
        "ebitda": (700, 700),
        "net_income": (220, 220),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("0.24", "0.37"), "Vonovia Q2 2026 Est GAAP / Adj"),
    ("Q3", 2026, ("0.26", "0.26"), "Vonovia Q3 2026 est"),
]
BS_DATA = {}
