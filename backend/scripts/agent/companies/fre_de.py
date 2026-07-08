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
