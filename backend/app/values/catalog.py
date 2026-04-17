SEED_VALUES = [
    # TRANSACTION
    {"key": "stock_price", "label_de": "Aktueller Aktienkurs", "label_en": "Actual Stock Price", "category": "TRANSACTION", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 1},
    {"key": "next_earnings", "label_de": "Nächste Earnings", "label_en": "Next Earnings", "category": "TRANSACTION", "source_type": "API", "data_type": "TEXT", "unit": None, "sort_order": 2},
    {"key": "dividends", "label_de": "Dividenden", "label_en": "Dividends", "category": "TRANSACTION", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 3},
    {"key": "dividend_return", "label_de": "Dividendenrendite", "label_en": "Dividend Return", "category": "TRANSACTION", "source_type": "API", "data_type": "NUMERIC", "unit": "%", "sort_order": 4},
    {"key": "analysts_target", "label_de": "Analysteneinschätzung", "label_en": "Analysts Target", "category": "TRANSACTION", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 5},

    # BASIC_COMPANY
    {"key": "market_cap", "label_de": "Marktkapitalisierung", "label_en": "Market Cap", "category": "BASIC_COMPANY", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 10},
    {"key": "shares_outstanding", "label_de": "Ausstehende Aktien", "label_en": "Shares Outstanding", "category": "BASIC_COMPANY", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 11},
    {"key": "sales", "label_de": "Umsatz", "label_en": "Sales", "category": "BASIC_COMPANY", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 12},
    {"key": "sales_growth", "label_de": "Umsatzwachstum", "label_en": "Sales Growth", "category": "BASIC_COMPANY", "source_type": "API", "data_type": "NUMERIC", "unit": "%", "sort_order": 13},
    {"key": "op_margin", "label_de": "Operative Marge", "label_en": "Op. Margin", "category": "BASIC_COMPANY", "source_type": "API", "data_type": "NUMERIC", "unit": "%", "sort_order": 14},
    {"key": "op_profit", "label_de": "Operativer Gewinn", "label_en": "Op. Profit", "category": "BASIC_COMPANY", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 15},
    {"key": "net_profit", "label_de": "Nettogewinn", "label_en": "Net Profit", "category": "BASIC_COMPANY", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 16},
    {"key": "op_cash_flow", "label_de": "Operativer Cashflow", "label_en": "Op. Cash Flow", "category": "BASIC_COMPANY", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 17},
    {"key": "free_cash_flow", "label_de": "Freier Cashflow", "label_en": "Free Cash Flow", "category": "BASIC_COMPANY", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 18},
    {"key": "cash", "label_de": "Cash", "label_en": "Cash", "category": "BASIC_COMPANY", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 19},
    {"key": "debt", "label_de": "Schulden", "label_en": "Debt", "category": "BASIC_COMPANY", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 20},
    {"key": "net_debt", "label_de": "Nettoverschuldung", "label_en": "Net Debt", "category": "BASIC_COMPANY", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 21},
    {"key": "ev", "label_de": "Unternehmenswert", "label_en": "EV", "category": "BASIC_COMPANY", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 22},
    {"key": "ebitda", "label_de": "EBITDA", "label_en": "EBITDA", "category": "BASIC_COMPANY", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 23},

    # HOHN_BASIC_1
    {"key": "eps_ttm", "label_de": "EPS TTM", "label_en": "EPS TTM", "category": "HOHN_BASIC_1", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 30},
    {"key": "eps_forward", "label_de": "EPS Forward", "label_en": "EPS Forward", "category": "HOHN_BASIC_1", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 31},
    {"key": "eps_growth", "label_de": "EPS Wachstum", "label_en": "EPS Growth", "category": "HOHN_BASIC_1", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 32},
    {"key": "buybacks", "label_de": "Aktienrückkäufe", "label_en": "Buybacks", "category": "HOHN_BASIC_1", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 33},
    {"key": "buyback_return", "label_de": "Buyback-Rendite", "label_en": "Buyback Return", "category": "HOHN_BASIC_1", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 34},
    {"key": "hohn_rendite_basic_1", "label_de": "Hohn-Rendite (Basic 1)", "label_en": "Hohn Return (Basic 1)", "category": "HOHN_BASIC_1", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 35},

    # HOHN_BASIC_2
    {"key": "fcf_yield", "label_de": "FCF Yield", "label_en": "FCF Yield", "category": "HOHN_BASIC_2", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 40},
    {"key": "hohn_rendite_basic_2", "label_de": "Hohn-Rendite (Basic 2)", "label_en": "Hohn Return (Basic 2)", "category": "HOHN_BASIC_2", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 41},

    # VALUATION_ADJ
    {"key": "pe_ttm", "label_de": "KGV TTM", "label_en": "PE TTM", "category": "VALUATION_ADJ", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 50},
    {"key": "pe_forward", "label_de": "KGV Forward", "label_en": "PE Forward", "category": "VALUATION_ADJ", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 51},
    {"key": "pe_target_analysts", "label_de": "KGV Ziel Analysten", "label_en": "PE Target Analysts", "category": "VALUATION_ADJ", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": None, "sort_order": 52},
    {"key": "upside_potential", "label_de": "Aufwärtspotenzial", "label_en": "Upside Potential", "category": "VALUATION_ADJ", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 53},
    {"key": "ev_ebitda", "label_de": "EV/EBITDA", "label_en": "EV/EBITDA", "category": "VALUATION_ADJ", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 54},
    {"key": "peg", "label_de": "PEG Ratio", "label_en": "PEG", "category": "VALUATION_ADJ", "source_type": "API", "data_type": "NUMERIC", "unit": None, "sort_order": 55},
    {"key": "judgement", "label_de": "Bewertungsurteil", "label_en": "Judgement", "category": "VALUATION_ADJ", "source_type": "QUALITATIVE", "data_type": "TEXT", "unit": None, "sort_order": 56},

    # RISK_ADJ
    {"key": "risk_business_model", "label_de": "Geschäftsmodell-Risiko", "label_en": "Business Model", "category": "RISK_ADJ", "source_type": "QUALITATIVE", "data_type": "FACTOR", "unit": None, "sort_order": 60},
    {"key": "risk_regulatory", "label_de": "Regulatorisches Risiko", "label_en": "Regulatory", "category": "RISK_ADJ", "source_type": "QUALITATIVE", "data_type": "FACTOR", "unit": None, "sort_order": 61},
    {"key": "risk_macro", "label_de": "Makro-Risiko", "label_en": "Macro", "category": "RISK_ADJ", "source_type": "QUALITATIVE", "data_type": "FACTOR", "unit": None, "sort_order": 62},
    {"key": "risk_factor", "label_de": "Risikofaktor", "label_en": "Risk Factor", "category": "RISK_ADJ", "source_type": "CALCULATED", "data_type": "FACTOR", "unit": None, "sort_order": 63},

    # MGMT_ADJ
    {"key": "mgmt_participation", "label_de": "Management-Beteiligung", "label_en": "Participation", "category": "MGMT_ADJ", "source_type": "QUALITATIVE", "data_type": "FACTOR", "unit": None, "sort_order": 70},
    {"key": "insider_transactions", "label_de": "Insider-Transaktionen", "label_en": "Insider Transactions", "category": "MGMT_ADJ", "source_type": "API", "data_type": "TEXT", "unit": None, "sort_order": 71},
    {"key": "mgmt_age", "label_de": "Managementalter", "label_en": "Age", "category": "MGMT_ADJ", "source_type": "QUALITATIVE", "data_type": "TEXT", "unit": None, "sort_order": 72},
    {"key": "mgmt_factor", "label_de": "Managementfaktor", "label_en": "Mgt. Factor", "category": "MGMT_ADJ", "source_type": "CALCULATED", "data_type": "FACTOR", "unit": None, "sort_order": 73},

    # TOTAL_ADJ
    {"key": "total_adjustment_factor", "label_de": "Gesamtanpassungsfaktor", "label_en": "Total Adjustment Factor", "category": "TOTAL_ADJ", "source_type": "CALCULATED", "data_type": "FACTOR", "unit": None, "sort_order": 80},
    {"key": "hohn_rendite_adjusted", "label_de": "Hohn-Rendite (adjustiert)", "label_en": "Hohn Return (Adjusted)", "category": "TOTAL_ADJ", "source_type": "CALCULATED", "data_type": "NUMERIC", "unit": "%", "sort_order": 81},
]
