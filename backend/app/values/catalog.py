SEED_VALUES = [
    # STAMMDATEN — always-current, 1x per company (period_type=SNAPSHOT)
    {"key": "market_cap", "label_de": "Marktkapitalisierung", "label_en": "Market Cap", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 1},
    {"key": "shares_outstanding", "label_de": "Ausstehende Aktien", "label_en": "Shares Outstanding", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 2},
    {"key": "sbc", "label_de": "Stock Based Compensation", "label_en": "Stock Based Compensation", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 3},

    # INPUTS — per FY
    {"key": "net_income", "label_de": "Nettogewinn", "label_en": "Net Income", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 10},
    {"key": "op_cash_flow", "label_de": "Operativer Cashflow", "label_en": "Op. Cash Flow", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 11},
    {"key": "capex", "label_de": "Capex", "label_en": "Capex", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 12},
    {"key": "debt", "label_de": "Schulden", "label_en": "Debt", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 13},
    {"key": "cash", "label_de": "Cash", "label_en": "Cash", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 14},

    # CALCULATED — per FY
    {"key": "fcf", "label_de": "Free Cash Flow", "label_en": "Free Cash Flow", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 20},
    {"key": "net_debt", "label_de": "Nettoverschuldung", "label_en": "Net Debt", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 21},
    {"key": "ni_growth", "label_de": "NI-Wachstum", "label_en": "NI Growth", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 22},
    {"key": "fcf_yield", "label_de": "FCF-Rendite", "label_en": "FCF Yield", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 23},
    {"key": "sbc_yield", "label_de": "SBC / Market Cap", "label_en": "SBC / Market Cap", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 24},
    {"key": "net_debt_change", "label_de": "Net-Debt-Veraenderung", "label_en": "Net Debt Change", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 25},
    {"key": "hohn_return", "label_de": "Hohn Rendite", "label_en": "Hohn Return", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 26},
]
