TICKER = "G1A.DE"; COMPANY_NAME = "GEA Group AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "GEA Group FY 2025", {
        "revenue": (5_500, 5_500), "ebitda": (907, 907), "net_income": (414, 414),
    }),
    ("Q1", 2026, "GEA Group Q1 2026 (May 2026)", {
        "revenue": (1_273, 1_273),
        "ebitda": (206, 206),  # EBITDA before restructuring
        "net_income": (100, 100),  # ~99.7M
    }),
]
EPS_DATA = [
    ("FY", 2025, ("2.56", "2.56"), "GEA Group FY 2025 Diluted"),
    ("Q1", 2026, ("0.64", "0.64"), "GEA Group Q1 2026"),
]
BS_DATA = {}

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "GEA Q2 2026 est", {
        "revenue": (1400, 1400),
        "ebitda": (230, 230),
        "net_income": (110, 110),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("0.70", "0.70"), "GEA Q2 2026 est"),
]
