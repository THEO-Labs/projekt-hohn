SEED_VALUES = [
    # STAMMDATEN (company-level, always current)
    {"key": "stock_price", "label_de": "Aktienkurs", "label_en": "Stock Price", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 1},
    {"key": "shares_outstanding", "label_de": "Ausstehende Aktien", "label_en": "Shares Outstanding", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 2},
    {"key": "market_cap", "label_de": "Marktkapitalisierung", "label_en": "Market Cap", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 3},

    # INPUTS (per FY)
    {"key": "sales", "label_de": "Umsatz", "label_en": "Sales", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 10},
    {"key": "net_income", "label_de": "Nettogewinn", "label_en": "Net Income", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 11},
    {"key": "fcf_margin_non_gaap", "label_de": "FCF-Marge (non-GAAP)", "label_en": "FCF Margin (non-GAAP)", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": "%", "sort_order": 12},
    {"key": "sbc", "label_de": "Stock Based Compensation", "label_en": "Stock Based Compensation", "category": "INPUTS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 13},

    # CALCULATED (per FY)
    {"key": "fcf", "label_de": "Free Cash Flow", "label_en": "FCF", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 20},
    {"key": "fcf_yield", "label_de": "FCF-Rendite", "label_en": "FCF Yield", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 21},
    {"key": "ni_growth", "label_de": "NI-Wachstum (aus Sales)", "label_en": "NI Growth (from Sales)", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 22},
    {"key": "sbc_yield", "label_de": "SBC / Market Cap", "label_en": "SBC / Market Cap", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 23},
    {"key": "hohn_return", "label_de": "Hohn Return", "label_en": "Hohn Return", "category": "CALCULATED", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 24},
]
