"""Merck KGaA. FY End Dec 31. Pharma/Chemistry.

FIX 2026-07-08: NI 2.608B ergaenzt, EPS Basic 6.00 (GAAP) ergaenzt zu Pre-EPS 8.34.
"""
from __future__ import annotations
TICKER = "MRK.DE"; COMPANY_NAME = "Merck KGaA"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "Merck KGaA FY 2025", {
        "revenue": (21_100, 21_100),
        "ebitda": (6_100, 6_100),  # EBITDA Pre
        "net_income": (2_608, 2_608),
    }),
    ("Q1", 2026, "Merck KGaA Q1 2026 (May 13 2026)", {
        "revenue": (5_100, 5_100),
        "ebitda": (1_530, 1_530),  # EBITDA Pre
        "net_income": (918, 918),  # ~EPS Pre 2.11 * 435M shares
    }),
]
EPS_DATA = [
    ("FY", 2025, ("6.00", "8.34"), "Merck KGaA FY 2025 Basic / EPS Pre"),
    ("Q1", 2026, ("2.11", "2.11"), "Merck KGaA Q1 2026 EPS Pre"),
]

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Merck KGaA Q2 2026 Est (FY 5.7-6.1B EBITDA, 7.50-8.20 EPS Pre)", {
        "revenue": (5_200, 5_200),
        "ebitda": (1_450, 1_450),
        "net_income": (830, 830),
    }),
    ("Q3", 2026, "Merck KGaA Q3 2026 est", {
        "revenue": (5150, 5150),
        "ebitda": (1450, 1450),
        "net_income": (830, 830),
    }),
    ("Q4", 2026, "Merck KGaA Q4 2026 est", {
        "revenue": (5450, 5450),
        "ebitda": (1500, 1500),
        "net_income": (850, 850),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("1.90", "1.90"), "Merck KGaA Q2 2026 Est"),
    ("Q3", 2026, ("1.90", "1.90"), "Merck KGaA Q3 2026 est"),
    ("Q4", 2026, ("1.95", "1.95"), "Merck KGaA Q4 2026 est"),
]
BS_DATA = {}
