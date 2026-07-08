"""Zalando SE. FY End Dec 31. E-Commerce.

FIX 2026-07-08: NI + EPS ergaenzt aus Annual Report Daten.
"""
from __future__ import annotations
TICKER = "ZAL.DE"; COMPANY_NAME = "Zalando SE"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "Zalando FY 2025 (Mar 2026)", {
        "revenue": (12_300, 12_300), "ebitda": (591, 591), "net_income": (139, 139),
    }),
    ("Q1", 2026, "Zalando Q1 2026 (May 2026)", {
        "revenue": (3_000, 3_000),
        "ebitda": (-80, 65),  # GAAP EBIT -79.7M (Loss) / Adjusted EBIT 65M
        "net_income": (-37, -37),  # Loss (EPS 0.14 negative)
    }),
]
EPS_DATA = [
    ("FY", 2025, ("0.52", "0.52"), "Zalando FY 2025 Basic (TTM Sep 2025)"),
    ("Q1", 2026, ("0.14", "0.14"), "Zalando Q1 2026"),
]
BS_DATA = {}

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Zalando Q2 2026 est", {
        "revenue": (3300, 3300),
        "ebitda": (200, 200),
        "net_income": (90, 90),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("0.34", "0.34"), "Zalando Q2 2026 est"),
]
