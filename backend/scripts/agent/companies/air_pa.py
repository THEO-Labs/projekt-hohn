"""Airbus SE (AIR.PA on Paris). FY End Dec 31. Aerospace."""
from __future__ import annotations
TICKER = "AIR.PA"; COMPANY_NAME = "Airbus SE"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("Q2", 2025, "Airbus Q2 2025", {
        "ebitda": (1_144, 1_144),  # EBIT reported
        "net_income": (732, 732),
    }),
    ("Q3", 2025, "Airbus Q3 2025", {
        "ebitda": (1_748, 1_748),
        "net_income": (1_116, 1_116),
    }),
    ("Q4", 2025, "Airbus Q4 2025", {
        "ebitda": (2_717, 2_717),
        "net_income": (2_580, 2_580),
    }),
    ("FY", 2025, "Airbus FY 2025 (Feb 2026)", {
        "revenue": (73_400, 73_400),
        "ebitda": (6_100, 7_100),  # EBIT reported / Adjusted
        "net_income": (5_200, 5_200),
    }),
    ("Q1", 2026, "Airbus Q1 2026 (Apr 2026)", {
        "revenue": (12_700, 12_700),
        "ebitda": (224, 300),  # EBIT reported / Adjusted
        "net_income": (600, 600),
    }),
]
EPS_DATA = [
    ("FY", 2025, ("6.61", "6.61"), "Airbus FY 2025 EPS reported"),
    ("Q1", 2026, ("0.74", "0.33"), "Airbus Q1 2026 Reported / Adjusted"),
]
BS_DATA = {}
