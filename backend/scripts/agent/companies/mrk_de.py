"""Merck KGaA. FY End Dec 31. Pharma/Chemistry."""
from __future__ import annotations
TICKER = "MRK.DE"; COMPANY_NAME = "Merck KGaA"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "Merck KGaA FY 2025", {
        "revenue": (21_100, 21_100),
        "ebitda": (6_100, 6_100),  # EBITDA Pre
    }),
]
EPS_DATA = [("FY", 2025, ("8.34", "8.34"), "Merck KGaA FY 2025 EPS Pre")]
BS_DATA = {}
