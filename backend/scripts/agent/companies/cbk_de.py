"""Commerzbank AG. FY End Dec 31. Bank."""
from __future__ import annotations
TICKER = "CBK.DE"; COMPANY_NAME = "Commerzbank AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("Q1", 2025, "Commerzbank Q1 2025", {"revenue": (3_100, 3_100), "net_income": (834, 834)}),
    ("Q2", 2025, "Commerzbank Q2 2025", {"revenue": (3_019, 3_019)}),
    ("FY", 2025, "Commerzbank FY 2025", {"revenue": (12_171, 12_171), "net_income": (2_600, 2_600)}),
    ("Q1", 2026, "Commerzbank Q1 2026", {
        "revenue": (3_200, 3_200),
        "net_income": (913, 913),  # Record NI
    }),
]
EPS_DATA = [
    ("Q1", 2025, ("0.73", "0.73"), "Commerzbank Q1 2025"),
    ("Q1", 2026, ("0.80", "0.80"), "Commerzbank Q1 2026 (~$0.87 * 0.916)"),
]

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Commerzbank Q2 2026 Est (FY Guidance 3.4B NI raised)", {
        "revenue": (3_100, 3_100),
        "net_income": (870, 870),  # ~3.4B/4
    }),
    ("Q3", 2026, "Commerzbank Q3 2026 Est (FY 3.4B NI guidance / saisonal schwaecher)", {
        "revenue": (3_050, 3_050),
        "net_income": (830, 830),
    }),
    ("Q4", 2026, "Commerzbank Q4 2026 est (FY NI 3.4B guidance)", {
        "revenue": (3100, 3100),
        "net_income": (800, 800),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("0.76", "0.76"), "Commerzbank Q2 2026 Est"),
    ("Q3", 2026, ("0.73", "0.73"), "Commerzbank Q3 2026 Est"),
    ("Q4", 2026, ("0.70", "0.70"), "Commerzbank Q4 2026 est (FY NI 3.4B guidance)"),
]
BS_DATA = {}
