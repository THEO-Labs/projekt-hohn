"""MTU Aero Engines AG. FY End Dec 31. Aerospace.

FIX 2026-07-08: EPS-Korrektur von 4.58 (Q4-Wert) auf 18.14 (FY Basic).
Berechnung: NI 968M / 53.34M shares outstanding = 18.14 EUR Basic EPS.
Reported diluted GAAP EPS = ~20.99 (mit Sonder-Effekten).
"""
from __future__ import annotations
TICKER = "MTX.DE"; COMPANY_NAME = "MTU Aero Engines AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("FY", 2025, "MTU FY 2025 (Feb 24 2026)", {
        "revenue": (8_700, 8_700),  # Adjusted revenue
        "ebitda": (1_350, 1_350),  # Adjusted EBIT
        "net_income": (968, 968),  # Adjusted NI
        "fcf": (378, 378),
    }),
]
EPS_DATA = [("FY", 2025, ("18.14", "20.99"), "MTU FY 2025 Basic (NI/Shares) / Reported")]
BS_DATA = {}
