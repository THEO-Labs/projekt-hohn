SEED_VALUES = [
    # STAMMDATEN (fix, 1x per company)
    {"key": "stock_price", "label_de": "Aktienkurs", "label_en": "Stock Price", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 1},
    {"key": "market_cap", "label_de": "Marktkapitalisierung", "label_en": "Market Cap", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 2},
    {"key": "shares_outstanding", "label_de": "Ausstehende Aktien", "label_en": "Shares Outstanding", "category": "STAMMDATEN", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 3},
    {"key": "market_cap_calc", "label_de": "Market Cap (Stock × Shares)", "label_en": "Market Cap Calculated", "category": "STAMMDATEN", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 4},

    # CASH & DEBT (per FY)
    {"key": "cash_and_equivalents", "label_de": "Cash & Equivalents", "label_en": "Cash and Cash Equivalents", "category": "CASH_DEBT", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 10},
    {"key": "marketable_securities_st", "label_de": "Marktwertpapiere (ST)", "label_en": "Marketable Securities (ST)", "category": "CASH_DEBT", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 11},
    {"key": "marketable_securities_lt", "label_de": "Marktwertpapiere (LT)", "label_en": "Long-term Marketable Securities", "category": "CASH_DEBT", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 12},
    {"key": "cash_sum", "label_de": "Cash Sum", "label_en": "Cash Sum", "category": "CASH_DEBT", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 13},
    {"key": "lease_liabilities", "label_de": "Lease Liabilities", "label_en": "Lease Liabilities", "category": "CASH_DEBT", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 14},
    {"key": "long_term_debt", "label_de": "Long-term Debt", "label_en": "Long-term Debt", "category": "CASH_DEBT", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 15},
    {"key": "debt_sum", "label_de": "Debt Sum", "label_en": "Debt Sum", "category": "CASH_DEBT", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 16},
    {"key": "net_debt", "label_de": "Net Debt", "label_en": "Net Debt", "category": "CASH_DEBT", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 17},
    {"key": "ev", "label_de": "Enterprise Value", "label_en": "Enterprise Value", "category": "CASH_DEBT", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 18},

    # BUYBACKS & SBC (per FY)
    {"key": "buyback_volume", "label_de": "Buyback-Volumen", "label_en": "Buyback Volume", "category": "BUYBACKS_SBC", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 20},
    {"key": "sbc", "label_de": "Stock Based Compensation", "label_en": "Stock Based Compensation", "category": "BUYBACKS_SBC", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 21},
    {"key": "net_buyback", "label_de": "Net Buyback", "label_en": "Net Buyback", "category": "BUYBACKS_SBC", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 22},
    {"key": "sbc_yield", "label_de": "SBC / Market Cap", "label_en": "SBC / Market Cap", "category": "BUYBACKS_SBC", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 23},
    {"key": "net_buyback_yield", "label_de": "Net Buyback / Market Cap", "label_en": "Net Buyback / Market Cap", "category": "BUYBACKS_SBC", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 24},

    # FCF (per FY)
    {"key": "fcf", "label_de": "Free Cash Flow", "label_en": "Free Cash Flow", "category": "FCF", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 30},
    {"key": "fcf_yield", "label_de": "FCF-Rendite", "label_en": "FCF Yield", "category": "FCF", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 31},

    # NI GROWTH (per FY)
    {"key": "net_income", "label_de": "Nettogewinn", "label_en": "Net Income", "category": "NI_GROWTH", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 40},
    {"key": "ni_growth", "label_de": "NI-Wachstum", "label_en": "NI Growth", "category": "NI_GROWTH", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 41},

    # CHANGES IN NET DEBT (per FY)
    {"key": "net_debt_change", "label_de": "ΔNet Debt", "label_en": "ΔNet Debt", "category": "DELTA_ND", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 50},
    {"key": "net_debt_change_pct", "label_de": "ΔNet Debt / Market Cap", "label_en": "ΔNet Debt / Market Cap", "category": "DELTA_ND", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 51},

    # DIVIDENDS (per FY)
    {"key": "dividends", "label_de": "Dividenden", "label_en": "Dividends", "category": "DIVIDENDS", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 60},
    {"key": "dividend_yield", "label_de": "Dividendenrendite", "label_en": "Dividend Yield", "category": "DIVIDENDS", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 61},

    # HOHN RETURN (per FY)
    {"key": "hohn_return_simple", "label_de": "Hohn-Rendite (einfach)", "label_en": "Hohn Return (simple)", "category": "HOHN_RETURN", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 70},
    {"key": "hohn_return_detailed", "label_de": "Hohn-Rendite (detailed)", "label_en": "Hohn Return (detailed)", "category": "HOHN_RETURN", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 71},
]
