TICKER = "G24.DE"; COMPANY_NAME = "Scout24 SE"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "Scout24 FY 2025", {
        "revenue": (649, 649), "ebitda": (406, 406), "net_income": (240, 240),
        "operating_cash_flow": (285, 285), "fcf": (253, 253),
    }),
    ("Q1", 2026, "Scout24 Q1 2026 (Apr 29 2026)", {
        "revenue": (180, 180),  # 179.6
        "ebitda": (108, 108),  # 107.9
        "net_income": (69, 69),  # 68.5
    }),
]
EPS_DATA = [
    ("FY", 2025, ("3.33", "3.47"), "Scout24 FY 2025 Basic / Adjusted"),
    ("Q1", 2026, ("0.97", "0.95"), "Scout24 Q1 2026 Basic / Adjusted"),
]
BS_DATA = {}

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Scout24 Q2 2026 est", {
        "revenue": (185, 185),
        "ebitda": (112, 112),
        "net_income": (72, 72),
    }),
    ("Q3", 2026, "Scout24 Q3 2026 est", {
        "revenue": (192, 192),
        "ebitda": (118, 118),
        "net_income": (76, 76),
    }),
    ("Q4", 2026, "Scout24 Q4 2026 est", {
        "revenue": (200, 200),
        "ebitda": (125, 125),
        "net_income": (82, 82),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("1.01", "1.01"), "Scout24 Q2 2026 est"),
    ("Q3", 2026, ("1.07", "1.07"), "Scout24 Q3 2026 est"),
    ("Q4", 2026, ("1.15", "1.15"), "Scout24 Q4 2026 est"),
]
