"""Deutsche Boerse AG. FY End Dec 31. Financial Services (Exchange)."""
from __future__ import annotations
TICKER = "DB1.DE"; COMPANY_NAME = "Deutsche Boerse AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("Q2", 2025, "Deutsche Boerse Q2 2025", {
        "revenue": (1_298, 1_298),  # Net revenue without treasury
        "ebitda": (684, 684),
        "net_income": (509, 509),
    }),
    ("Q3", 2025, "Deutsche Boerse Q3 2025", {
        "revenue": (1_237, 1_237),
        "ebitda": (639, 639),
        "net_income": (473, 473),
    }),
    ("FY", 2025, "Deutsche Boerse FY 2025", {
        "revenue": (5_200, 5_200),  # Net rev without treasury
        "ebitda": (2_675, 2_675),  # EBITDA
        "net_income": (1_995, 1_995),
    }),
]
EPS_DATA = [
    ("Q2", 2025, ("2.78", "2.96"), "DB1 Q2 2025 (Basic / Cash EPS)"),
    ("Q3", 2025, ("2.58", "2.78"), "DB1 Q3 2025 (Basic / pre-PPA)"),
    ("FY", 2025, ("10.90", "11.65"), "DB1 FY 2025 (Basic / Cash EPS)"),
]
BS_DATA = {}
