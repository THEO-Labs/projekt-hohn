"""Merck KGaA. FY End Dec 31. Pharma/Chemistry.

FIX 2026-07-08: NI 2.608B ergaenzt, EPS Basic 6.00 (GAAP) ergaenzt zu Pre-EPS 8.34.
"""
from __future__ import annotations
TICKER = "MRK.DE"; COMPANY_NAME = "Merck KGaA"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "Merck KGaA FY 2025", {
        "revenue": (21_100, 21_100),
        "ebitda": (6_100, 6_100),  # EBITDA Pre
        "net_income": (2_608, 2_608),
    }),
    ("Q1", 2026, "Merck KGaA Q1 2026 (May 13 2026)", {
        "revenue": (5_100, 5_100),
        "ebitda": (1_530, 1_530),  # EBITDA Pre
    }),
]
EPS_DATA = [
    ("FY", 2025, ("6.00", "8.34"), "Merck KGaA FY 2025 Basic / EPS Pre"),
    ("Q1", 2026, ("2.11", "2.11"), "Merck KGaA Q1 2026 EPS Pre"),
]
BS_DATA = {}
