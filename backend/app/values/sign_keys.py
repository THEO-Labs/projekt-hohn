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
    # NOTE: net_debt absichtlich NICHT hier — kann legitim negativ sein
    # (Net Cash Position bei cash-rich Firmen wie Apple oder Allianz).
})
