"""Commerzbank AG. FY End Dec 31. Bank."""
from __future__ import annotations
TICKER = "CBK.DE"; COMPANY_NAME = "Commerzbank AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("Q1", 2025, "Commerzbank Q1 2025", {
        "revenue": (3_100, 3_100),
        "net_income": (834, 834),
    }),
    ("Q2", 2025, "Commerzbank Q2 2025", {
        "revenue": (3_019, 3_019),
    }),
    ("FY", 2025, "Commerzbank FY 2025", {
        "revenue": (12_171, 12_171),
        "net_income": (2_600, 2_600),
    }),
]
EPS_DATA = [("Q1", 2025, ("0.73", "0.73"), "Commerzbank Q1 2025")]
BS_DATA = {}
