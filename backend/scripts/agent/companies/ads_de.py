"""adidas AG Data-Modul. FY End Dec 31. Consumer Goods (Sportswear)."""
from __future__ import annotations
TICKER = "ADS.DE"; COMPANY_NAME = "adidas AG"; FISCAL_YEAR_END_MONTH = 12; CURRENCY = "EUR"

# FY Sales sind aus Sigma Q1-Q3 + Q4 impliziert
# Q1+Q2+Q3 = 6153+5952+6630 = 18735
# adidas FY Sales laut Annual Report ~25.7B (aus vorheriger search); moeglich dass unterschiedliche Reportings

Q_DATA = [
    ("Q1", 2025, "adidas Q1 2025", {
        "revenue": (6_153, 6_153),
        "ebitda": (610, 610),  # Operating profit
    }),
    ("Q2", 2025, "adidas Q2 2025", {
        "revenue": (5_952, 5_952),
        "ebitda": (548, 548),  # 9.2% EBIT margin * 5.952 = ~548
    }),
    ("Q3", 2025, "adidas Q3 2025", {
        "revenue": (6_630, 6_630),
        "net_income": (482, 482),
        "ebitda": (736, 736),
    }),
    ("Q4", 2025, "adidas Q4 2025 (Mar 2026)", {
        "revenue": (6_946, 6_946),  # FY 25.681 - Sigma Q1-Q3 (18.735)
        "ebitda": (162, 162),  # FY 2.056 - Sigma Q1-Q3 (1.894)
    }),
    ("FY", 2025, "adidas FY 2025 Annual Report (Mar 2026)", {
        "revenue": (25_681, 25_681),
        "ebitda": (2_056, 2_056),  # Operating profit
        "net_income": (1_377, 1_377),
    }),
    ("Q1", 2026, "adidas Q1 2026 (Apr 2026)", {
        "revenue": (6_600, 6_600),
        "ebitda": (705, 705),  # Operating profit
        "net_income": (484, 484),
    }),
]

Q_DATA_ESTIMATE = [
    ("Q2", 2026, "adidas Q2 2026 Consensus (Rev 6.45B via MarketBeat)", {
        "revenue": (6_450, 6_450),
        "ebitda": (580, 580),  # ~9% margin (FY guidance 2.3B / 4)
        "net_income": (400, 400),  # ~1.6B FY, 25% Q2 weight
    }),
    ("Q3", 2026, "adidas Q3 2026 est (back-to-school + gross margin improve H2)", {
        "revenue": (6800, 6800),
        "ebitda": (620, 620),
        "net_income": (430, 430),
    }),
]

EPS_DATA = [
    ("Q3", 2025, ("2.57", "2.57"), "adidas Q3 2025 continuing"),
    ("FY", 2025, ("7.51", "7.51"), "adidas FY 2025"),
    ("Q1", 2026, ("2.70", "2.70"), "adidas Q1 2026"),
]

EPS_DATA_ESTIMATE = [
    ("Q2", 2026, ("2.25", "2.25"), "adidas Q2 2026 est (NI 400M / 178M shares)"),
    ("Q3", 2026, ("2.40", "2.40"), "adidas Q3 2026 est (back-to-school + gross margin improve H2)"),
]
BS_DATA = {}
