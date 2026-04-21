SEED_VALUES = [
    # INPUTS FIX (1x per company — STAMMDATEN)
    {"key": "market_cap", "label_de": "Marktkapitalisierung", "label_en": "Market Cap", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 1},
    {"key": "shares_outstanding", "label_de": "Ausstehende Aktien", "label_en": "Shares Outstanding", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 2},

    # INPUTS per FY (Hardcodes + direkt-abhaengige Calcs, bleiben im Inputs-Block)
    {"key": "sbc", "label_de": "Stock Based Compensation", "label_en": "Stock Based Compensation", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 10},
    {"key": "net_income", "label_de": "Nettogewinn", "label_en": "Net Income", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 11},
    {"key": "op_cash_flow", "label_de": "Operativer Cashflow", "label_en": "Op. Cash Flow", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 12},
    {"key": "capex", "label_de": "Capex", "label_en": "Capex", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 13},
    {"key": "fcf", "label_de": "Free Cash Flow", "label_en": "Free Cash Flow", "category": "INPUTS", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 14},
    {"key": "debt", "label_de": "Schulden (LT + Leases)", "label_en": "Debt (LT + Leases)", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 15},
    {"key": "cash", "label_de": "Cash (+ Mkt Sec ST + LT)", "label_en": "Cash (+ Mkt Sec ST + LT)", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 16},
    {"key": "net_debt", "label_de": "Nettoverschuldung", "label_en": "Net Debt", "category": "INPUTS", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 17},

    # CALCULATED — ratios + Hohn Return
    {"key": "ni_growth", "label_de": "NI-Wachstum", "label_en": "NI Growth", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 30},
    {"key": "fcf_yield", "label_de": "FCF-Rendite", "label_en": "FCF Yield", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 31},
    {"key": "sbc_yield", "label_de": "SBC / Market Cap", "label_en": "SBC / Market Cap", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 32},
    {"key": "net_debt_change", "label_de": "Net-Debt-Aenderung", "label_en": "Net Debt Change", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 33},
    {"key": "net_debt_change_pct", "label_de": "Net-Debt-Aenderung / MCap", "label_en": "Net Debt Change / MCap", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 34},
    {"key": "hohn_return_base", "label_de": "Hohn Return (ohne ΔND)", "label_en": "Hohn Return (without ΔND)", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 35},
    {"key": "hohn_return", "label_de": "Hohn Return (mit ΔND)", "label_en": "Hohn Return (with ΔND)", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 36},
]
