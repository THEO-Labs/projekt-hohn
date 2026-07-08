"""Volkswagen AG. FY End Dec 31. Automotive.
VW hat komplexe Aktienstruktur (VOW = Ordinary, VOW3 = Preferred).
Total outstanding: ~295M ordinary + ~206M preferred = ~501M.
"""
from __future__ import annotations
TICKER = "VOW3.DE"; COMPANY_NAME = "Volkswagen AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("FY", 2025, "Volkswagen FY 2025 (Mar 10 2026)", {
        "revenue": (321_900, 321_900),
        "net_income": (6_900, 6_900),
        "ebitda": (8_900, 8_900),  # Operating result
    }),
    ("Q1", 2026, "Volkswagen Q1 2026 (Apr 30 2026)", {
        "revenue": (75_700, 75_700),
        "ebitda": (2_500, 2_500),  # Operating result
        "net_income": (1_278, 1_278),  # ~EPS 2.55 * 501M shares
    }),
]
EPS_DATA = [
    ("Q4", 2025, ("3.39", "3.39"), "Volkswagen Q4 2025 EPS"),
    ("FY", 2025, ("13.77", "13.77"), "Volkswagen FY 2025 (NI 6.9B / 501M shares Basic)"),
    ("Q1", 2026, ("2.55", "2.55"), "Volkswagen Q1 2026"),
]

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "VW Q2 2026 Est (Q1 base + slight Rev growth)", {
        "revenue": (78_000, 78_000),
        "ebitda": (2_800, 2_800),
        "net_income": (1_400, 1_400),
    }),
]

EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("2.79", "2.79"), "VW Q2 2026 Est"),
]
BS_DATA = {}
