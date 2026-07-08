"""Deutsche Boerse AG. FY End Dec 31. Financial Services (Exchange)."""
from __future__ import annotations
TICKER = "DB1.DE"; COMPANY_NAME = "Deutsche Boerse AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("Q2", 2025, "Deutsche Boerse Q2 2025", {
        "revenue": (1_298, 1_298), "ebitda": (684, 684), "net_income": (509, 509),
    }),
    ("Q3", 2025, "Deutsche Boerse Q3 2025", {
        "revenue": (1_237, 1_237), "ebitda": (639, 639), "net_income": (473, 473),
    }),
    ("FY", 2025, "Deutsche Boerse FY 2025", {
        "revenue": (5_200, 5_200), "ebitda": (2_675, 2_675), "net_income": (1_995, 1_995),
    }),
    ("Q1", 2026, "Deutsche Boerse Q1 2026", {
        "revenue": (1_434, 1_434),  # Net rev without treasury
        "ebitda": (803, 1_007),  # GAAP: EBITDA excl treasury 803 / Adj: EBITDA incl treasury 1007
        "net_income": (585, 585),
    }),
]
EPS_DATA = [
    ("Q2", 2025, ("2.78", "2.96"), "DB1 Q2 2025 (Basic / Cash EPS)"),
    ("Q3", 2025, ("2.58", "2.78"), "DB1 Q3 2025 (Basic / pre-PPA)"),
    ("FY", 2025, ("10.90", "11.65"), "DB1 FY 2025 (Basic / Cash EPS)"),
    ("Q1", 2026, ("3.19", "3.19"), "DB1 Q1 2026 est (NI 585M / ~183M shares)"),
]
BS_DATA = {}

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Deutsche Boerse Q2 2026 est", {
        "revenue": (1400, 1400),
        "ebitda": (800, 800),
        "net_income": (570, 570),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("3.10", "3.10"), "Deutsche Boerse Q2 2026 est"),
]
