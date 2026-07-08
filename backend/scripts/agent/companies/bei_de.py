"""Beiersdorf AG. FY End Dec 31. Consumer Goods."""
from __future__ import annotations
TICKER = "BEI.DE"; COMPANY_NAME = "Beiersdorf AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "Beiersdorf FY 2025 (Mar 2 2026)", {
        "revenue": (9_900, 9_900),
        "ebitda": (1_400, 1_400),  # EBIT excl special
        "net_income": (955, 955),
    }),
    ("Q1", 2026, "Beiersdorf Q1 2026 (Apr 21 2026)", {
        "revenue": (2_484, 2_484),
        "ebitda": (523, 523),  # EBIT (Operating income)
    }),
]
EPS_DATA = [("FY", 2025, ("4.25", "4.25"), "Beiersdorf FY 2025")]
BS_DATA = {}

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Beiersdorf Q2 2026 est (Q1 base + seasonal)", {
        "revenue": (2500, 2500),
        "ebitda": (520, 520),
        "net_income": (320, 320),
    }),
    ("Q3", 2026, "Beiersdorf Q3 2026 est", {
        "revenue": (2550, 2550),
        "ebitda": (530, 530),
        "net_income": (330, 330),
    }),
    ("Q4", 2026, "Beiersdorf Q4 2026 est", {
        "revenue": (2700, 2700),
        "ebitda": (550, 550),
        "net_income": (350, 350),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("1.42", "1.42"), "Beiersdorf Q2 2026 est (Q1 base + seasonal)"),
    ("Q3", 2026, ("1.46", "1.46"), "Beiersdorf Q3 2026 est"),
    ("Q4", 2026, ("1.55", "1.55"), "Beiersdorf Q4 2026 est"),
]
