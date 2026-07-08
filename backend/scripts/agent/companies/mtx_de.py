"""MTU Aero Engines AG. FY End Dec 31. Aerospace.

FIX 2026-07-08: EPS-Korrektur von 4.58 (Q4-Wert) auf 18.14 (FY Basic).
Berechnung: NI 968M / 53.34M shares outstanding = 18.14 EUR Basic EPS.
Reported diluted GAAP EPS = ~20.99 (mit Sonder-Effekten).
"""
from __future__ import annotations
TICKER = "MTX.DE"; COMPANY_NAME = "MTU Aero Engines AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("FY", 2025, "MTU FY 2025 (Feb 24 2026)", {
        "revenue": (8_700, 8_700), "ebitda": (1_350, 1_350), "net_income": (968, 968), "fcf": (378, 378),
    }),
    ("Q1", 2026, "MTU Q1 2026 (Apr 30 2026)", {
        "revenue": (2_200, 2_200),  # Adjusted revenue
        "ebitda": (320, 320),  # Adjusted EBIT
        "net_income": (229, 229),  # Adjusted NI
    }),
]
EPS_DATA = [
    ("FY", 2025, ("18.14", "20.99"), "MTU FY 2025 Basic (NI/Shares) / Reported"),
    ("Q1", 2026, ("4.30", "4.30"), "MTU Q1 2026 (NI 229M / 53.3M shares)"),
]

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "MTU Q2 2026 Est (Q1 Trend continue)", {
        "revenue": (2_300, 2_300),
        "ebitda": (340, 340),
        "net_income": (240, 240),
    }),
    ("Q3", 2026, "MTU Q3 2026 est (Aerospace-Cycle solide)", {
        "revenue": (2400, 2400),
        "ebitda": (350, 350),
        "net_income": (250, 250),
    }),
    ("Q4", 2026, "MTU Q4 2026 est (Aerospace-Cycle Peak Aftermarket)", {
        "revenue": (2500, 2500),
        "ebitda": (400, 400),
        "net_income": (285, 285),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("4.50", "4.50"), "MTU Q2 2026 Est"),
    ("Q3", 2026, ("4.70", "4.70"), "MTU Q3 2026 est (Aerospace-Cycle solide)"),
    ("Q4", 2026, ("5.35", "5.35"), "MTU Q4 2026 est (Aerospace-Cycle Peak Aftermarket)"),
]
BS_DATA = {}
