"""Mercedes-Benz Group AG. FY End Dec 31. Automotive.

FIX 2026-07-08: NI + EPS ergaenzt (aus USD Werten konvertiert).
"""
from __future__ import annotations
TICKER = "MBG.DE"; COMPANY_NAME = "Mercedes-Benz Group AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("Q1", 2025, "Mercedes-Benz Q1 2025 (Apr 29 2025)", {
        "revenue": (33_200, 33_200),
        "ebitda": (2_300, 2_300),  # EBIT
    }),
    ("FY", 2025, "Mercedes-Benz FY 2025 (Feb 12 2026)", {
        "revenue": (132_200, 132_200),
        "ebitda": (8_200, 8_200),  # Adjusted EBIT
        "net_income": (5_328, 5_328),  # NI FY 2025 (~$5.817B * 0.916)
        "fcf": (5_400, 5_400),  # FCF Industrial
    }),
    ("Q1", 2026, "Mercedes-Benz Q1 2026 (Apr 30 2026)", {
        "revenue": (31_600, 31_600),
        "ebitda": (1_900, 1_900),  # Group EBIT
        "net_income": (1_430, 1_430),
    }),
]
EPS_DATA = [
    ("FY", 2025, ("5.53", "5.53"), "Mercedes-Benz FY 2025 EPS (~$6.04 * 0.916)"),
    ("Q1", 2026, ("1.34", "1.49"), "Mercedes-Benz Q1 2026 Basic (NI/Shares) / Adjusted"),
]

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Mercedes Q2 2026 Est (Q1 Trend flat, US Tariff Headwinds)", {
        "revenue": (33_000, 33_000),
        "ebitda": (2_100, 2_100),
        "net_income": (1_500, 1_500),
    }),
    ("Q3", 2026, "Mercedes Q3 2026 est (Cars ROS 3.5% mid-guidance)", {
        "revenue": (32500, 32500),
        "ebitda": (1700, 1700),
        "net_income": (1300, 1300),
    }),
    ("Q4", 2026, "Mercedes Q4 2026 est (Jahresende, Cars ROS ~4%)", {
        "revenue": (34500, 34500),
        "ebitda": (2200, 2200),
        "net_income": (1700, 1700),
    }),
]

EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("1.40", "1.55"), "Mercedes Q2 2026 Est Basic / Adjusted"),
    ("Q3", 2026, ("1.22", "1.22"), "Mercedes Q3 2026 est (Cars ROS 3.5% mid-guidance)"),
    ("Q4", 2026, ("1.60", "1.60"), "Mercedes Q4 2026 est (Jahresende, Cars ROS ~4%)"),
]
BS_DATA = {}
