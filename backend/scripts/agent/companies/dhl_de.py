"""DHL Group (Deutsche Post AG) Data-Modul.

Fiscal Year End: Dec 31. Logistics.
Zahlen aus offiziellen DHL Group Media Relations Press Releases 2025.
Q4 impliziert aus FY - Sigma(Q1-Q3).
"""
from __future__ import annotations

TICKER = "DHL.DE"
COMPANY_NAME = "Deutsche Post AG (DHL Group)"
FISCAL_YEAR_END_MONTH = 12
CURRENCY = "EUR"


def _capex(ocf: int | None, fcf: int | None) -> int | None:
    if ocf is None or fcf is None:
        return None
    return ocf - fcf


Q_DATA = [
    ("Q1", 2025, "DHL Q1 2025 (Apr 30 2025)", {
        "revenue": (20_809, 20_809),
        "net_income": (786, 786),
        "ebitda": (1_370, 1_370),  # EBIT (Op Profit)
        "fcf": (732, 732),  # FCF ex-M&A
    }),
    ("Q2", 2025, "DHL Q2 2025 (Aug 5 2025)", {
        "revenue": (19_800, 19_800),
        "net_income": (815, 815),
        "ebitda": (1_400, 1_400),
        "fcf": (329, 329),
    }),
    ("Q3", 2025, "DHL Q3 2025 (Nov 6 2025)", {
        "revenue": (20_128, 20_128),
        "net_income": (840, 840),
        "ebitda": (1_477, 1_477),
    }),
    ("Q4", 2025, "DHL Q4 2025 (Mar 5 2026) Implied FY-Sigma", {
        "revenue": (22_163, 22_163),  # FY 82.9 - Sigma Q1-Q3 (60.737)
        "net_income": (1_059, 1_059),  # FY 3.5 - 2.441
        "ebitda": (1_853, 1_853),  # FY 6.1 - 4.247
    }),
    ("FY", 2025, "DHL FY 2025 (Mar 5 2026)", {
        "revenue": (82_900, 82_900),
        "net_income": (3_500, 3_500),
        "ebitda": (6_100, 6_100),
        "fcf": (3_200, 3_200),
    }),
    ("Q1", 2026, "DHL Q1 2026 (Apr 30 2026)", {
        "revenue": (20_400, 20_400),
        "ebitda": (1_500, 1_500),  # EBIT
        "net_income": (812, 812),  # Attributable to DP shareholders
    }),
    ("Q2", 2026, "DHL Q2 2026 Preliminary (Jul 7 2026 pre-release, full Aug 5)", {
        "revenue": (22_440, 22_440),  # +10% YoY (Q2 2025 20.400 * 1.10)
        "ebitda": (1_850, 1_850),  # EBIT reported (+29% YoY)
        "net_income": (1_050, 1_050),  # est ~57% of EBIT (Q2 2025: 815/1400 = 58%)
    }),
]

EPS_DATA = [
    ("Q1", 2025, ("0.68", "0.68"), "DHL Q1 2025"),
    ("Q2", 2025, ("0.72", "0.72"), "DHL Q2 2025"),
    ("Q3", 2025, ("0.75", "0.75"), "DHL Q3 2025"),
    ("Q4", 2025, ("0.94", "0.94"), "DHL Q4 2025 (FY 3.09 - Sigma Q1-Q3)"),
    ("FY", 2025, ("3.09", "3.09"), "DHL FY 2025"),
    ("Q1", 2026, ("0.73", "0.72"), "DHL Q1 2026 Basic / Diluted"),
    ("Q2", 2026, ("0.94", "0.94"), "DHL Q2 2026 est (~NI 1050 / 1113M shares)"),
]

BS_DATA = {}
