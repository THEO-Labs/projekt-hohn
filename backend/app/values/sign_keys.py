"""Vorzeichen-Konventionen fuer value_keys.

ALWAYS_POSITIVE_KEYS: Werte die per Bilanz-/Cashflow-Konvention immer
positiv gespeichert werden (Cash-Outflows als Betrag, Bilanz-Positionen).
Sign-Normalisierung in Persistenz-Pfaden (Provider + Web + Calc).
"""
ALWAYS_POSITIVE_KEYS = frozenset({
    # cash outflow events (always reported as positive amount paid)
    "dividends",
    "buyback_volume",
    "sbc",
    # balance sheet items (always positive when shown)
    "shares_outstanding",
    "revenue",
    "cash_and_equivalents",
    "st_investments",
    "st_debt",
    "lt_debt",
    # NOTE: net_debt, operating_cash_flow, capex, eps_diluted absichtlich NICHT
    # hier — koennen legitim negativ sein (Net Cash Position, Cash-Outflow-Q,
    # EPS-Verlust). CapEx wird typischerweise negativ als Cash-Outflow
    # gespeichert; wir schreiben roh wie im Bericht.
})
