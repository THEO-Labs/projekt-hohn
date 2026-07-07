"""Template fuer eine Company-Data-Datei.

Kopiere zu <ticker_lower>.py und fuelle die Werte aus.

Regeln:
- Alle Currency-Werte in Mio USD (fuer EUR-Filer entsprechend Mio EUR + currency-Anpassung in fill.py)
- EPS in raw $ (nicht Millionen)
- (gaap, adj) tuple pro Wert. adj == gaap wenn kein separates Non-GAAP.
- CapEx immer positiv (Absolutwert)
- source_ref: kurz + eindeutig, z.B. "Mar 6 2025 8-K" oder "Dec 11 2025 10-K"

Alle Rows die hier fehlen bleiben in DB unangetastet (existing pdf/manual
werden NIE ueberschrieben).
"""
from __future__ import annotations

TICKER = "XXX"
COMPANY_NAME = "Full Company Name"

# Nur informativ — company muss vorher in DB existieren
FISCAL_YEAR_END_MONTH = 12

# ---------------------------------------------------------------------------
# Quartals- und FY-Daten (Currency-Keys)
# ---------------------------------------------------------------------------
# Format: (period_type, period_year, source_ref, {key: (gaap_mio, adj_mio)})
# Wenn adj == gaap: kein separates Non-GAAP reported (Frontend zeigt GAAP als Fallback)

Q_DATA = [
    ("Q1", 2025, "Mar X 2025 8-K", {
        "revenue": (0, 0),
        "net_income": (0, 0),
        "ebitda": (0, 0),
        "operating_cash_flow": (0, 0),
        "capex": (0, 0),
        "fcf": (0, 0),
        "sbc": (0, 0),
        "dividends": (0, 0),
        "buyback_volume": (0, 0),
    }),
    ("Q2", 2025, "Jun X 2025 8-K", {}),
    ("Q3", 2025, "Sep X 2025 8-K", {}),
    ("Q4", 2025, "Feb X 2026 10-K", {}),
    ("FY", 2025, "Feb X 2026 10-K", {}),
    ("Q1", 2026, "Apr X 2026 8-K", {}),
    ("Q2", 2026, "Jul X 2026 8-K", {}),
]

# ---------------------------------------------------------------------------
# EPS-Daten (raw $, nicht Millionen)
# ---------------------------------------------------------------------------
# Format: (period_type, period_year, (gaap_dollar, adj_dollar), source_ref)

EPS_DATA = [
    # ("Q1", 2025, ("1.14", "1.60"), "Mar 6 2025 8-K"),
]

# ---------------------------------------------------------------------------
# Balance Sheet FY-Snapshots
# ---------------------------------------------------------------------------
# Format: {year: {key: (value_mio, source_ref)}}
# Werte gelten am Fiscal-Year-End (nicht Kalenderjahr-Ende bei non-Dec-Filern).

BS_DATA = {
    2025: {
        # "cash_and_equivalents": (0, "Feb X 2026 10-K"),
        # "st_investments": (0, "Feb X 2026 10-K"),
        # "st_debt": (0, "Feb X 2026 10-K"),
        # "lt_debt": (0, "Feb X 2026 10-K"),
    },
}
