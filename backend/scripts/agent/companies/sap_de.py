"""SAP SE Data-Modul.

Fiscal Year = Calendar Year (Dec 31). IFRS statt US-GAAP.
- Non-IFRS entspricht "Adjusted"
- EPS wird bei SAP nur als BASIC berichtet (kaum Dilution)
- CapEx = OCF - FCF (impliziert)
- SBC (share-based compensation) nur im Annual Report separat ausgewiesen
- Dividends/Buybacks im Quarterly nicht immer angegeben — meist bei Full-Year

Quellen: SAP Quarterly Statements Q1-Q4 2025 + Q1 2026 (jeweils PR Newswire),
SAP Annual Report 2025 Form 20-F.
"""
from __future__ import annotations

TICKER = "SAP.DE"
COMPANY_NAME = "SAP SE"
FISCAL_YEAR_END_MONTH = 12
CURRENCY = "EUR"


def _capex(ocf: int, fcf: int) -> int:
    return ocf - fcf


Q_DATA = [
    # ==== FY2025 ====
    ("Q1", 2025, "SAP Quarterly Statement Q1 2025 (Apr 22 2025)", {
        "revenue": (9_013, 9_013),
        "net_income": (1_796, 1_796),
        "ebitda": (2_333, 2_455),  # Operating profit IFRS / Non-IFRS
        "operating_cash_flow": (3_780, 3_780),
        "capex": (_capex(3_780, 3_583), _capex(3_780, 3_583)),  # 197
        "fcf": (3_583, 3_583),
    }),
    ("Q2", 2025, "SAP Quarterly Statement Q2 2025 (Jul 22 2025)", {
        "revenue": (9_027, 9_027),
        "net_income": (1_749, 1_749),
        "ebitda": (2_456, 2_568),
        "operating_cash_flow": (2_577, 2_577),
        "capex": (_capex(2_577, 2_357), _capex(2_577, 2_357)),  # 220
        "fcf": (2_357, 2_357),
    }),
    ("Q3", 2025, "SAP Quarterly Statement Q3 2025 (Oct 22 2025)", {
        "revenue": (9_076, 9_076),
        "net_income": (2_051, 2_051),
        "ebitda": (2_487, 2_566),
        "operating_cash_flow": (1_502, 1_502),
        "capex": (_capex(1_502, 1_266), _capex(1_502, 1_266)),  # 236
        "fcf": (1_266, 1_266),
    }),
    ("Q4", 2025, "SAP Quarterly Statement Q4 2025 (Jan 27 2026)", {
        "revenue": (9_684, 9_684),
        "net_income": (1_896, 1_896),
        "ebitda": (2_554, 2_829),
        "operating_cash_flow": (1_297, 1_297),
        "capex": (_capex(1_297, 1_034), _capex(1_297, 1_034)),  # 263
        "fcf": (1_034, 1_034),
    }),
    ("FY", 2025, "SAP Quarterly Statement Q4 2025 (Jan 27 2026)", {
        "revenue": (36_800, 36_800),
        "net_income": (7_492, 7_492),
        "ebitda": (9_830, 10_419),
        "operating_cash_flow": (9_156, 9_156),
        "capex": (_capex(9_156, 8_239), _capex(9_156, 8_239)),  # 917
        "fcf": (8_239, 8_239),
    }),
    # ==== FY2026 ====
    ("Q1", 2026, "SAP Quarterly Statement Q1 2026 (Apr 23 2026)", {
        "revenue": (9_555, 9_555),
        "net_income": (1_946, 2_002),
        "ebitda": (2_741, 2_867),
        "operating_cash_flow": (3_513, 3_513),
        "capex": (_capex(3_513, 3_248), _capex(3_513, 3_248)),  # 265
        "fcf": (3_248, 3_248),
    }),
    # Q2 2026 wird erst am 23. Juli 2026 released. TODO nach Erscheinen.
]

EPS_DATA = [
    # SAP reported BASIC EPS (kaum Dilution). Wir speichern als eps_diluted-Feld.
    # Format: (period_type, period_year, (ifrs_eur, non_ifrs_eur), source_ref)
    ("Q1", 2025, ("1.52", "1.44"), "SAP Q1 2025"),
    ("Q2", 2025, ("1.45", "1.50"), "SAP Q2 2025"),
    ("Q3", 2025, ("1.72", "1.59"), "SAP Q3 2025"),
    ("Q4", 2025, ("1.58", "1.62"), "SAP Q4 2025"),
    ("FY", 2025, ("6.28", "6.15"), "SAP FY 2025"),
    ("Q1", 2026, ("1.66", "1.72"), "SAP Q1 2026"),
]

BS_DATA = {
    # 2025 Balance Sheet TODO — Annual Report 20-F Position (nicht in Quarterly Statement).
    # Bekannt: Net Liquidity = 3.38B (implicit Cash - Debt).
    # Manual Fill spaeter aus dem Annual Report.
}

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "SAP Q2 2026 est (deceleration acknowledged)", {
        "revenue": (9700, 9700),
        "ebitda": (2700, 2700),
        "net_income": (1970, 1970),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("1.68", "1.68"), "SAP Q2 2026 est (deceleration acknowledged)"),
]
