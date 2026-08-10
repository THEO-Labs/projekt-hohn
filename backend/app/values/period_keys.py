"""Geteilte Perioden-Key-Sets (frueher in quarterly_estimates.py).

Klassifiziert Keys fuer die FY/Quartals-Aggregation:
  - SUMMABLE: FY = Q1 + Q2 + Q3 + Q4
  - POINT_IN_TIME: FY = Q4-Endstand (Bilanz-Snapshot)
"""

# Keys mit Quartals-Zeitreihe (FY-Roll-up via _refresh_fy_from_quarters).
# Shares Outstanding ist KEIN Estimate-Wert — bleibt Live-Snapshot.
# Balance-Sheet-Keys (cash_and_equivalents, st_investments, st_debt,
# lt_debt) sind bewusst NICHT hier: instant-facts, deren Q4/FY-Slots die
# Bilanz-Fortschreibung (derive_balance_carry_forward) pflegt.
QUARTERLY_ESTIMATE_KEYS = frozenset({
    # Income Statement
    "net_income", "revenue", "ebitda", "eps_diluted",
    # Cashflow
    "fcf", "operating_cash_flow", "capex", "sbc",
    "buyback_volume", "dividends",
    # Balance Sheet (point-in-time — historische Kompatibilitaet)
    "net_debt",
})

# Cumulative: FY = Sigma Q1+Q2+Q3+Q4 (Income/Cashflow-Werte).
# eps_diluted ist per-Q reported und Annual != exakt Sigma(Q) wegen
# Weighted-Average-Diluted-Shares (Buybacks veraendern Denominator);
# fuer die Detail-Page ist die Sigma-Approximation akzeptabel.
SUMMABLE_QUARTERLY_KEYS = frozenset({
    "net_income", "revenue", "ebitda", "eps_diluted",
    "fcf", "operating_cash_flow", "capex", "sbc",
    "buyback_volume", "dividends",
})

# Point-in-Time: FY = Q4-Endstand (Bilanz-Snapshot). Aktuell nur net_debt.
POINT_IN_TIME_QUARTERLY_KEYS = frozenset({"net_debt"})
