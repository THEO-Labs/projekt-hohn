SEED_VALUES = [
    # STAMMDATEN — always-current, 1x per company (period_type=SNAPSHOT)
    {"key": "stock_price", "label_de": "Aktienkurs", "label_en": "Stock Price", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 1},
    {"key": "currency", "label_de": "Waehrung", "label_en": "Currency", "category": "STAMMDATEN", "source_type": "API", "data_type": "TEXT", "unit": None, "sort_order": 2},
    {"key": "exchange_rate", "label_de": "Wechselkurs (zu EUR)", "label_en": "Exchange Rate (to EUR)", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 3},
    {"key": "stock_price_eur", "label_de": "Aktienkurs (EUR)", "label_en": "Stock Price (EUR)", "category": "STAMMDATEN", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 4},
    {"key": "market_cap", "label_de": "Marktkapitalisierung", "label_en": "Market Cap", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 5},
    {"key": "market_cap_calc", "label_de": "Market Cap (Shares x Price)", "label_en": "Market Cap Calculated", "category": "STAMMDATEN", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 6},
    {"key": "shares_outstanding", "label_de": "Ausstehende Aktien", "label_en": "Shares Outstanding", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 7},
    {"key": "debt", "label_de": "Schulden", "label_en": "Debt", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 8},
    {"key": "cash", "label_de": "Cash", "label_en": "Cash", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 9},
    {"key": "net_debt", "label_de": "Nettoverschuldung", "label_en": "Net Debt", "category": "STAMMDATEN", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 10},
    {"key": "ev", "label_de": "Enterprise Value", "label_en": "Enterprise Value", "category": "STAMMDATEN", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 11},
    {"key": "sbc", "label_de": "Stock Based Compensation", "label_en": "Stock Based Compensation", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 12},

    # INPUTS — per FY (Yahoo fuer 2020-2025, KI fuer Guidance 2026e)
    {"key": "net_income", "label_de": "Nettogewinn", "label_en": "Net Income", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 20},
    {"key": "eps", "label_de": "EPS", "label_en": "EPS", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 21},
    {"key": "eps_adj", "label_de": "EPS (adj)", "label_en": "EPS (adj)", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 22},
    {"key": "op_cash_flow", "label_de": "Operativer Cashflow", "label_en": "Op. Cash Flow", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 23},
    {"key": "capex", "label_de": "Capex", "label_en": "Capex", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 24},
    {"key": "dividends", "label_de": "Dividenden", "label_en": "Dividends", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 25},

    # CALCULATED — per FY
    {"key": "ni_growth", "label_de": "NI-Wachstum", "label_en": "NI Growth", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 30},
    {"key": "op_cf_change", "label_de": "Op. CF Change", "label_en": "Op. CF Change", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 31},
    {"key": "fcf", "label_de": "Free Cash Flow", "label_en": "Free Cash Flow", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 32},
    {"key": "fcf_change", "label_de": "FCF Change", "label_en": "FCF Change", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 33},

    # CALCULATED — Ratios per FY
    {"key": "ev_op_cf", "label_de": "EV / Op. Cash Flow", "label_en": "EV / Op. Cash Flow", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 40},
    {"key": "pe_ltm_adj", "label_de": "PE (LTM adj)", "label_en": "PE (LTM adj)", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 41},
    {"key": "pe_target", "label_de": "PE (Ziel-FY)", "label_en": "PE (target FY)", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 42},
    {"key": "fcf_yield", "label_de": "FCF-Rendite", "label_en": "FCF Yield", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 43},
    {"key": "dividend_yield", "label_de": "Dividendenrendite", "label_en": "Dividend Yield", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 44},
    {"key": "peg", "label_de": "PEG Ratio", "label_en": "PEG Ratio", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 45},
    {"key": "hohn_return", "label_de": "Hohn Return", "label_en": "Hohn Return", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 46},
]
