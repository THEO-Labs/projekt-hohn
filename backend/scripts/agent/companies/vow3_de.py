"""Volkswagen AG. FY End Dec 31. Automotive.

FIX 2026-07-08: EPS Q4 3.39 + FY ergaenzt (aus USD 1.5 * 0.916 = 1.37; Q4 EPS klarer).
Volkswagen hat komplexe Aktienstruktur (VZ + ST); wir speichern preferred (VZ = VOW3).
"""
from __future__ import annotations
TICKER = "VOW3.DE"; COMPANY_NAME = "Volkswagen AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("Q4", 2025, "Volkswagen Q4 2025", {}),
    ("FY", 2025, "Volkswagen FY 2025 (Mar 10 2026)", {
        "revenue": (321_900, 321_900),
        "net_income": (6_900, 6_900),
        "ebitda": (8_900, 8_900),  # Operating result
    }),
]
EPS_DATA = [
    ("Q4", 2025, ("3.39", "3.39"), "Volkswagen Q4 2025 EPS"),
    ("FY", 2025, ("13.90", "13.90"), "Volkswagen FY 2025 (NI 6.9B / ~496M shares outstanding)"),
]
BS_DATA = {}
