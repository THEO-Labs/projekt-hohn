"""Volkswagen AG. FY End Dec 31. Automotive.

VW hat komplexe Aktienstruktur (VOW = Ordinary, VOW3 = Preferred).
Total outstanding: ~295M ordinary + ~206M preferred = ~501M.

FIX 2026-07-08: EPS 13.90 ist NI/Total-Shares Basic. Preferred EPS meist leicht hoeher.
Q4 2025 EPS 3.39 aus Investing.com (Consensus verfehlt).
"""
from __future__ import annotations
TICKER = "VOW3.DE"; COMPANY_NAME = "Volkswagen AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("FY", 2025, "Volkswagen FY 2025 (Mar 10 2026)", {
        "revenue": (321_900, 321_900),
        "net_income": (6_900, 6_900),
        "ebitda": (8_900, 8_900),  # Operating result
    }),
]
EPS_DATA = [
    ("Q4", 2025, ("3.39", "3.39"), "Volkswagen Q4 2025 EPS"),
    ("FY", 2025, ("13.77", "13.77"), "Volkswagen FY 2025 (NI 6.9B / 501M shares Basic)"),
]
BS_DATA = {}
