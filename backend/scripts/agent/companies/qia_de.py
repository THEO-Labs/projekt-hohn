TICKER = "QIA.DE"; COMPANY_NAME = "Qiagen NV"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "USD"
Q_DATA = [
    ("FY", 2025, "Qiagen FY 2025", {
        "revenue": (2_090, 2_090), "ebitda": (616, 616), "net_income": (522, 522),
    }),
    ("Q1", 2026, "Qiagen Q1 2026 (May 6 2026)", {
        "revenue": (492, 492),  # USD
        "ebitda": (135, 135),  # Adj Op Income (27.4% margin * 492)
        "net_income": (74, 122),  # ~EPS 0.33/0.54 * 225M USD
    }),
]
EPS_DATA = [
    ("FY", 2025, ("2.40", "2.40"), "Qiagen FY 2025 Adjusted Diluted EPS USD"),
    ("Q1", 2026, ("0.33", "0.54"), "Qiagen Q1 2026 Reported / Adjusted USD"),
]
BS_DATA = {}

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Qiagen Q2 2026 est USD", {
        "revenue": (510, 510),
        "ebitda": (140, 140),
        "net_income": (76, 76),
    }),
    ("Q3", 2026, "Qiagen Q3 2026 est USD", {
        "revenue": (520, 520),
        "ebitda": (145, 145),
        "net_income": (79, 79),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("0.34", "0.34"), "Qiagen Q2 2026 est USD"),
    ("Q3", 2026, ("0.35", "0.35"), "Qiagen Q3 2026 est USD"),
]
