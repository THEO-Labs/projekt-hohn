"""Porsche Automobil Holding SE (PAH3). FY End Dec 31.

Holding, hauptsaechlich Beteiligung an VW (~31.9%) und Porsche AG (~12.5%).
Revenue ist minimal, NI ist Beteiligungsergebnis (Equity Method).

FIX 2026-07-08: NI 2.749B EUR ergaenzt ($2.999B * 0.916).
"""
from __future__ import annotations
TICKER = "PAH3.DE"; COMPANY_NAME = "Porsche Automobil Holding SE"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

Q_DATA = [
    ("FY", 2025, "Porsche SE FY 2025 Annual Report (Mar 2026)", {
        "net_income": (2_749, 2_749),
    }),
    ("Q1", 2026, "Porsche SE Q1 2026 (Apr 30 2026)", {
        "net_income": (-900, 400),  # Reported / Adjusted (Impairment -1.3B VW + 39M Porsche AG)
    }),
]
EPS_DATA = [
    ("FY", 2025, ("9.17", "9.17"), "Porsche SE FY 2025 (TTM ~NI 2.749B / 305M shares)"),
]
BS_DATA = {}

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "Porsche SE Q2 2026 est (Beteiligungsergebnis)", {
        "revenue": (0, 0),
        "ebitda": (0, 0),
        "net_income": (500, 500),
    }),
]
EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("1.60", "1.60"), "Porsche SE Q2 2026 est (Beteiligungsergebnis)"),
]
