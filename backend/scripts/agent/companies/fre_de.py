TICKER = "FRE.DE"; COMPANY_NAME = "Fresenius SE & Co KGaA"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"
Q_DATA = [
    ("FY", 2025, "Fresenius FY 2025", {
        "revenue": (22_554, 22_554),
        "ebitda": (2_595, 2_595),  # EBIT
        "net_income": (1_264, 1_264),
    }),
    ("Q1", 2026, "Fresenius Q1 2026 (May 2026)", {
        "revenue": (5_744, 5_744),
        "ebitda": (678, 678),  # EBIT
        "net_income": (460, 460),  # Core NI before Special Items
    }),
]
EPS_DATA = [
    ("FY", 2025, ("2.24", "2.87"), "Fresenius FY 2025 Reported / Core"),
    ("Q1", 2026, ("0.82", "0.82"), "Fresenius Q1 2026 Core EPS"),
]
BS_DATA = {}

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Fresenius Q2 2026 est", {
        "revenue": (5800, 5800),
        "ebitda": (690, 690),
        "net_income": (470, 470),
    }),
    ("Q3", 2026, "Fresenius Q3 2026 est", {
        "revenue": (5850, 5850),
        "ebitda": (700, 700),
        "net_income": (480, 480),
    }),
    ("Q4", 2026, "Fresenius Q4 2026 est", {
        "revenue": (6100, 6100),
        "ebitda": (750, 750),
        "net_income": (510, 510),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("0.84", "0.84"), "Fresenius Q2 2026 est"),
    ("Q3", 2026, ("0.86", "0.86"), "Fresenius Q3 2026 est"),
    ("Q4", 2026, ("0.91", "0.91"), "Fresenius Q4 2026 est"),
]
