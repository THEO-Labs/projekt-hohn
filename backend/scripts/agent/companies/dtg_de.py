"""Daimler Truck Holding AG. FY End Dec 31. Trucks."""
from __future__ import annotations
TICKER = "DTG.DE"; COMPANY_NAME = "Daimler Truck Holding AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("FY", 2025, "Daimler Truck FY 2025", {
        "revenue": (49_400, 49_400),
        "ebitda": (3_800, 3_800),  # Adjusted EBIT
        "net_income": (2_000, 2_000),
        "fcf": (1_800, 1_800),  # FCF Industrial
    }),
    ("Q1", 2026, "Daimler Truck Q1 2026 (May 6 2026)", {
        "revenue": (9_100, 9_100),  # Industrial Business
        "ebitda": (498, 498),  # Adjusted Group EBIT
        "net_income": (160, 160),  # ~$175M * 0.916
    }),
]
EPS_DATA = [
    ("FY", 2025, ("2.56", "2.56"), "Daimler Truck FY 2025"),
    ("Q1", 2026, ("0.18", "0.18"), "Daimler Truck Q1 2026"),
]

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Daimler Truck Q2 2026 Est (Q1 base)", {
        "revenue": (10_500, 10_500),
        "ebitda": (750, 750),
        "net_income": (350, 350),
    }),
    ("Q3", 2026, "Daimler Truck Q3 2026 est (Order-Momentum)", {
        "revenue": (11000, 11000),
        "ebitda": (800, 800),
        "net_income": (380, 380),
    }),
    ("Q4", 2026, "Daimler Truck Q4 2026 est (Jahresende)", {
        "revenue": (12500, 12500),
        "ebitda": (1100, 1100),
        "net_income": (550, 550),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("0.43", "0.43"), "Daimler Truck Q2 2026 Est"),
    ("Q3", 2026, ("0.47", "0.47"), "Daimler Truck Q3 2026 est (Order-Momentum)"),
    ("Q4", 2026, ("0.68", "0.68"), "Daimler Truck Q4 2026 est (Jahresende)"),
]
BS_DATA = {}
