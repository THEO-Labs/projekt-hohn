"""Merck KGaA. FY End Dec 31. Pharma/Chemistry.

FIX 2026-07-08: NI 2.608B ergaenzt, EPS Basic 6.00 (GAAP) ergaenzt zu Pre-EPS 8.34.
"""
from __future__ import annotations
TICKER = "MRK.DE"; COMPANY_NAME = "Merck KGaA"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "Merck KGaA FY 2025", {
        "revenue": (21_100, 21_100),
        "ebitda": (6_100, 6_100),  # EBITDA Pre
        "net_income": (2_608, 2_608),  # NI attributable to shareholders
    }),
]
EPS_DATA = [("FY", 2025, ("6.00", "8.34"), "Merck KGaA FY 2025 Basic / EPS Pre")]
BS_DATA = {}
