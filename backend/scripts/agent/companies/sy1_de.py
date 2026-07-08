TICKER = "SY1.DE"; COMPANY_NAME = "Symrise AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "Symrise FY 2025", {
        "revenue": (4_929, 4_929), "ebitda": (1_081, 1_081), "net_income": (513, 513), "fcf": (780, 780),
    }),
    ("Q1", 2026, "Symrise Q1 2026 (Apr 2026)", {
        "revenue": (1_249, 1_249),
        "ebitda": (272, 272),  # est ~21.8% adj EBITDA margin (mid guidance)
    }),
]
EPS_DATA = [("FY", 2025, ("1.78", "3.67"), "Symrise FY 2025 GAAP / Adjusted")]
BS_DATA = {}

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Symrise Q2 2026 est", {
        "revenue": (1260, 1260),
        "ebitda": (275, 275),
        "net_income": (130, 130),
    }),
    ("Q3", 2026, "Symrise Q3 2026 est", {
        "revenue": (1280, 1280),
        "ebitda": (285, 285),
        "net_income": (140, 140),
    }),
    ("Q4", 2026, "Symrise Q4 2026 est", {
        "revenue": (1320, 1320),
        "ebitda": (305, 305),
        "net_income": (155, 155),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("0.92", "0.92"), "Symrise Q2 2026 est"),
    ("Q3", 2026, ("0.99", "0.99"), "Symrise Q3 2026 est"),
    ("Q4", 2026, ("1.10", "1.10"), "Symrise Q4 2026 est"),
]
