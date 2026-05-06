SEED_VALUES = [
    # STAMMDATEN
    {"key": "market_cap", "label_de": "Marktkapitalisierung", "label_en": "Market Cap", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 1},
    {"key": "market_cap_calc", "label_de": "Market Cap (Stock × Shares)", "label_en": "Market Cap Calculated", "category": "STAMMDATEN", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 2},
    {"key": "stock_price", "label_de": "Aktienkurs", "label_en": "Stock Price", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 3},
    {"key": "shares_outstanding", "label_de": "Ausstehende Aktien", "label_en": "Shares Outstanding", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 4},

    # SBC
    {"key": "sbc_yield", "label_de": "SBC / Market Cap", "label_en": "SBC / Market Cap", "category": "SBC", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 19},
    {"key": "sbc", "label_de": "Stock Based Compensation", "label_en": "Stock Based Compensation", "category": "SBC", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 20},

    # BUYBACKS
    {"key": "buyback_volume", "label_de": "Buyback-Volumen", "label_en": "Buyback Volume", "category": "BUYBACKS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 25},
    {"key": "net_buyback", "label_de": "Net Buyback", "label_en": "Net Buyback", "category": "BUYBACKS", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 26},
    {"key": "buyback_yield", "label_de": "Buyback / Market Cap", "label_en": "Buyback / Market Cap", "category": "BUYBACKS", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 27},
    {"key": "net_buyback_yield", "label_de": "Net Buyback / Market Cap", "label_en": "Net Buyback / Market Cap", "category": "BUYBACKS", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 28},

    # FCF
    {"key": "fcf_yield", "label_de": "FCF-Rendite", "label_en": "FCF Yield", "category": "FCF", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 30},
    {"key": "fcf", "label_de": "Free Cash Flow", "label_en": "Free Cash Flow", "category": "FCF", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 31},

    # NI GROWTH
    {"key": "ni_growth", "label_de": "NI-Wachstum", "label_en": "NI Growth", "category": "NI_GROWTH", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 40},
    {"key": "net_income", "label_de": "Nettogewinn", "label_en": "Net Income", "category": "NI_GROWTH", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 41},

    # DEBT (Net-Debt-fokussiert — keine Sub-Komponenten mehr)
    {"key": "net_debt_change_pct", "label_de": "ΔNet Debt / Market Cap", "label_en": "ΔNet Debt / Market Cap", "category": "DEBT", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 50},
    {"key": "net_debt_change", "label_de": "ΔNet Debt", "label_en": "ΔNet Debt", "category": "DEBT", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 51},
    {"key": "net_debt", "label_de": "Net Debt", "label_en": "Net Debt", "category": "DEBT", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 52},

    # DIVIDENDS
    {"key": "dividend_yield", "label_de": "Dividendenrendite", "label_en": "Dividend Yield", "category": "DIVIDENDS", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 60},
    {"key": "dividends", "label_de": "Dividenden", "label_en": "Dividends", "category": "DIVIDENDS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 61},

    # HOHN RETURN
    {"key": "hohn_return_simple", "label_de": "Hohn-Rendite (einfach)", "label_en": "Hohn Return (simple)", "category": "HOHN_RETURN", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 70},
    {"key": "hohn_return_detailed", "label_de": "Hohn-Rendite (detailed)", "label_en": "Hohn Return (detailed)", "category": "HOHN_RETURN", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 71},
    {"key": "actual_return", "label_de": "Tatsächliche Rendite (FY)", "label_en": "Actual Return (FY)", "category": "HOHN_RETURN", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 72},
]
