TICKER = "BNR.DE"; COMPANY_NAME = "Brenntag SE"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "Brenntag FY 2025", {
        "revenue": (15_200, 15_200), "ebitda": (1_290, 1_290), "net_income": (265, 265), "fcf": (941, 941),
    }),
    ("Q1", 2026, "Brenntag Q1 2026 (May 2026)", {
        "revenue": (3_700, 3_700),
        "ebitda": (306, 306),  # Operating EBITDA
        "net_income": (98, 98),
    }),
]
EPS_DATA = [
    ("FY", 2025, ("1.83", "1.83"), "Brenntag FY 2025"),
    ("Q1", 2026, ("0.68", "0.68"), "Brenntag Q1 2026"),
]
BS_DATA = {}

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Brenntag Q2 2026 est", {
        "revenue": (3700, 3700),
        "ebitda": (300, 300),
        "net_income": (100, 100),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("0.69", "0.69"), "Brenntag Q2 2026 est"),
]
