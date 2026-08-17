"""Einzige Quelle der Wahrheit fuer Metrik-Definitionen ("Pinning"),
Adjusted-Keys und Domain-Allowlists der Perplexity-Abfragen.
Definitionen landen als Feldbeschreibung im JSON-Schema.
"""

# Praezise, so wie die Kennzahl im offiziellen US-GAAP-Bericht steht.
METRIC_DEFINITIONS: dict[str, str] = {
    "revenue": "Total revenue / net sales for the fiscal year, consolidated (GAAP income statement top line), in full reporting-currency units (all digits, not scaled).",
    "net_income": "Net income attributable to the company's shareholders (GAAP, after non-controlling interests), in full reporting-currency units (all digits, not scaled).",
    "eps_diluted": "Diluted earnings per share (GAAP), in currency units (not millions).",
    "operating_cash_flow": "Net cash provided by operating activities (consolidated cash flow statement), in full reporting-currency units (all digits, not scaled).",
    "capex": "Capital expenditures = purchases of property, plant and equipment (and capitalized software), absolute positive amount, in full reporting-currency units (all digits, not scaled).",
    "fcf": "Free cash flow = operating cash flow minus capital expenditures, in full reporting-currency units (all digits, not scaled).",
    "ebitda": "EBITDA = operating income plus depreciation, depletion and amortization (full D&A incl. amortization of intangibles), in full reporting-currency units (all digits, not scaled). NEVER a non-GAAP operating-income figure.",
    "sbc": "Stock-based compensation expense for the fiscal year (cash flow statement add-back), in full reporting-currency units (all digits, not scaled).",
    "buyback_volume": "Total cash used for repurchases of common stock during the fiscal year (financing activities), absolute amount, in full reporting-currency units (all digits, not scaled).",
    "dividends": "Total cash dividends paid to common shareholders during the fiscal year, absolute amount, in full reporting-currency units (all digits, not scaled).",
    "net_debt": "Net debt = total debt (short-term + long-term borrowings, excluding operating lease liabilities) minus cash & equivalents and short-term investments. Negative = net cash, in full reporting-currency units (all digits, not scaled).",
    "cash_and_equivalents": "Cash and cash equivalents at fiscal year end (balance sheet), in full reporting-currency units (all digits, not scaled).",
    "st_investments": "Short-term / marketable investments at fiscal year end (balance sheet), in full reporting-currency units (all digits, not scaled).",
    "st_debt": "Short-term borrowings + current portion of long-term debt at fiscal year end (excluding operating leases), in full reporting-currency units (all digits, not scaled).",
    "lt_debt": "Long-term debt at fiscal year end (excluding operating lease liabilities), in full reporting-currency units (all digits, not scaled).",
}

# Kennzahlen, fuer die die Firma zusaetzlich einen Non-GAAP/adjusted-Wert
# berichtet. Perplexity liefert dann auch das `<key>_adjusted`-Feld.
ADJUSTED_KEYS: set[str] = {"net_income", "ebitda", "fcf", "revenue", "operating_cash_flow"}

# Reported-Abfragen: nur offizielle Filings (8-K-Exhibits/10-K liegen auf sec.gov).
PERIOD_DOMAIN_ALLOWLIST: list[str] = ["sec.gov"]
