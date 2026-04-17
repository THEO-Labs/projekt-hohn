import logging
from decimal import Decimal, InvalidOperation

import pandas as pd
import yfinance
from cachetools import TTLCache

from app.providers.base import ProviderResult

logger = logging.getLogger(__name__)

INFO_KEY_MAP = {
    "stock_price": "currentPrice",
    "market_cap": "marketCap",
    "shares_outstanding": "sharesOutstanding",
    "dividends": "dividendRate",
    "dividend_return": "dividendYield",
    "analysts_target": "targetMeanPrice",
    "eps_ttm": "trailingEps",
    "eps_forward": "forwardEps",
    "pe_ttm": "trailingPE",
    "pe_forward": "forwardPE",
    "ev": "enterpriseValue",
    "ebitda": "ebitda",
    "ev_ebitda": "enterpriseToEbitda",
    "peg": "pegRatio",
    "free_cash_flow": "freeCashflow",
    "op_cash_flow": "operatingCashflow",
    "cash": "totalCash",
    "debt": "totalDebt",
    "sales": "totalRevenue",
    "op_margin": "operatingMargins",
    "net_profit": "netIncomeToCommon",
}

PERCENT_KEYS = {"dividend_return", "op_margin"}

ALWAYS_CURRENT_KEYS = {
    "stock_price",
    "market_cap",
    "shares_outstanding",
    "analysts_target",
    "next_earnings",
    "pe_ttm",
    "pe_forward",
    "eps_forward",
    "ev",
    "ev_ebitda",
    "peg",
    "insider_transactions",
    "dividend_return",
}

FINANCIALS_ROWS = {
    "sales": ["Total Revenue", "Revenue"],
    "op_profit": ["Operating Income", "Operating Revenue"],
    "net_profit": ["Net Income", "Net Income Common Stockholders"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "eps_ttm": ["Diluted EPS", "Basic EPS"],
}

BALANCE_SHEET_ROWS = {
    "cash": ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments", "Cash And Short Term Investments"],
    "debt": ["Total Debt", "Long Term Debt", "Net Debt"],
}

CASHFLOW_ROWS = {
    "op_cash_flow": ["Operating Cash Flow", "Cash From Operating Activities"],
    "free_cash_flow": ["Free Cash Flow"],
    "buybacks": ["Repurchase Of Capital Stock", "Common Stock Repurchase", "Repurchase Of Common Stock"],
    "dividends": ["Cash Dividends Paid", "Common Stock Dividend Paid", "Payment Of Dividends"],
}


class YahooFinanceProvider:
    name = "Yahoo Finance"
    supported_keys = set(INFO_KEY_MAP.keys()) | {"next_earnings", "buybacks", "sales_growth", "op_profit", "insider_transactions"}

    def __init__(self) -> None:
        self._ticker_cache: TTLCache = TTLCache(maxsize=100, ttl=300)
        self._info_cache: TTLCache = TTLCache(maxsize=100, ttl=300)
        self._financials_cache: TTLCache = TTLCache(maxsize=100, ttl=300)
        self._balance_sheet_cache: TTLCache = TTLCache(maxsize=100, ttl=300)
        self._cashflow_cache: TTLCache = TTLCache(maxsize=100, ttl=300)

    def _get_ticker(self, ticker: str) -> yfinance.Ticker:
        if ticker not in self._ticker_cache:
            self._ticker_cache[ticker] = yfinance.Ticker(ticker)
        return self._ticker_cache[ticker]

    def _get_info(self, ticker: str) -> dict:
        if ticker not in self._info_cache:
            t = self._get_ticker(ticker)
            self._info_cache[ticker] = t.info or {}
        return self._info_cache[ticker]

    def _get_financials(self, ticker: str):
        if ticker not in self._financials_cache:
            t = self._get_ticker(ticker)
            self._financials_cache[ticker] = t.financials
        return self._financials_cache[ticker]

    def _get_balance_sheet(self, ticker: str):
        if ticker not in self._balance_sheet_cache:
            t = self._get_ticker(ticker)
            self._balance_sheet_cache[ticker] = t.balance_sheet
        return self._balance_sheet_cache[ticker]

    def _get_cashflow(self, ticker: str):
        if ticker not in self._cashflow_cache:
            t = self._get_ticker(ticker)
            self._cashflow_cache[ticker] = t.cashflow
        return self._cashflow_cache[ticker]

    def _to_decimal(self, value) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

    def _find_year_column(self, df, year: int):
        for col in df.columns:
            if hasattr(col, "year") and col.year == year:
                return col
        return None

    def _get_row_value(self, df, col, row_names: list[str]) -> Decimal | None:
        for name in row_names:
            matching = [idx for idx in df.index if name.lower() in str(idx).lower()]
            if matching:
                val = df.loc[matching[0], col]
                if pd.notna(val):
                    return self._to_decimal(val)
        return None

    def fetch(
        self,
        ticker: str,
        key: str,
        period_type: str = "SNAPSHOT",
        period_year: int | None = None,
    ) -> ProviderResult | None:
        source_link = f"https://finance.yahoo.com/quote/{ticker}"

        if period_type == "FY" and period_year is not None and key not in ALWAYS_CURRENT_KEYS:
            return self._fetch_historical(ticker, key, period_year, source_link)

        if key in INFO_KEY_MAP:
            return self._fetch_from_info(ticker, key, source_link)

        if key == "next_earnings":
            return self._fetch_next_earnings(ticker, source_link)

        if key == "buybacks":
            return self._fetch_buybacks(ticker, source_link)

        if key == "op_profit":
            return self._fetch_op_profit(ticker, source_link)

        if key == "sales_growth":
            return self._fetch_sales_growth(ticker, source_link)

        if key == "insider_transactions":
            return self._fetch_insider_transactions(ticker, source_link)

        return None

    def _fetch_historical(
        self,
        ticker: str,
        key: str,
        period_year: int,
        source_link: str,
    ) -> ProviderResult | None:
        info = self._get_info(ticker)
        currency = info.get("currency")

        if key in FINANCIALS_ROWS:
            return self._fetch_from_financials(ticker, key, period_year, source_link, currency)

        if key in BALANCE_SHEET_ROWS:
            return self._fetch_from_balance_sheet(ticker, key, period_year, source_link, currency)

        if key in CASHFLOW_ROWS:
            abs_value = key == "buybacks"
            return self._fetch_from_cashflow(ticker, key, period_year, source_link, currency, abs_value=abs_value)

        if key == "op_margin":
            return self._fetch_historical_op_margin(ticker, period_year, source_link)

        if key == "sales_growth":
            return self._fetch_historical_sales_growth(ticker, period_year, source_link)

        return None

    def _fetch_from_financials(
        self, ticker: str, key: str, period_year: int, source_link: str, currency: str | None
    ) -> ProviderResult | None:
        try:
            df = self._get_financials(ticker)
            if df is None or df.empty:
                return None
            col = self._find_year_column(df, period_year)
            if col is None:
                return None
            value = self._get_row_value(df, col, FINANCIALS_ROWS[key])
            if value is None:
                return None
            return ProviderResult(value=value, source_name=self.name, source_link=source_link, currency=currency)
        except Exception:
            return None

    def _fetch_from_balance_sheet(
        self, ticker: str, key: str, period_year: int, source_link: str, currency: str | None
    ) -> ProviderResult | None:
        try:
            df = self._get_balance_sheet(ticker)
            if df is None or df.empty:
                return None
            col = self._find_year_column(df, period_year)
            if col is None:
                return None
            value = self._get_row_value(df, col, BALANCE_SHEET_ROWS[key])
            if value is None:
                return None
            return ProviderResult(value=value, source_name=self.name, source_link=source_link, currency=currency)
        except Exception:
            return None

    def _fetch_from_cashflow(
        self, ticker: str, key: str, period_year: int, source_link: str, currency: str | None, abs_value: bool = False
    ) -> ProviderResult | None:
        row_names = CASHFLOW_ROWS.get(key)
        if row_names is None:
            return None
        try:
            df = self._get_cashflow(ticker)
            if df is None or df.empty:
                return None
            col = self._find_year_column(df, period_year)
            if col is None:
                return None
            value = self._get_row_value(df, col, row_names)
            if value is None:
                return None
            if abs_value:
                value = abs(value)
            return ProviderResult(value=value, source_name=self.name, source_link=source_link, currency=currency)
        except Exception:
            return None

    def _fetch_historical_op_margin(self, ticker: str, period_year: int, source_link: str) -> ProviderResult | None:
        try:
            df = self._get_financials(ticker)
            if df is None or df.empty:
                return None
            col = self._find_year_column(df, period_year)
            if col is None:
                return None
            sales = self._get_row_value(df, col, FINANCIALS_ROWS["sales"])
            op_profit = self._get_row_value(df, col, FINANCIALS_ROWS["op_profit"])
            if sales is None or op_profit is None or sales == 0:
                return None
            margin = op_profit / sales * Decimal("100")
            return ProviderResult(value=margin, source_name=self.name, source_link=source_link, currency=None)
        except Exception:
            return None

    def _fetch_historical_sales_growth(self, ticker: str, period_year: int, source_link: str) -> ProviderResult | None:
        try:
            df = self._get_financials(ticker)
            if df is None or df.empty:
                return None
            col_current = self._find_year_column(df, period_year)
            col_prev = self._find_year_column(df, period_year - 1)
            if col_current is None or col_prev is None:
                return None
            sales_current = self._get_row_value(df, col_current, FINANCIALS_ROWS["sales"])
            sales_prev = self._get_row_value(df, col_prev, FINANCIALS_ROWS["sales"])
            if sales_current is None or sales_prev is None or sales_prev == 0:
                return None
            growth = (sales_current - sales_prev) / sales_prev * Decimal("100")
            return ProviderResult(value=growth, source_name=self.name, source_link=source_link, currency=None)
        except Exception:
            return None

    def _fetch_from_info(self, ticker: str, key: str, source_link: str) -> ProviderResult | None:
        info = self._get_info(ticker)
        yf_key = INFO_KEY_MAP[key]
        raw = info.get(yf_key)
        if raw is None:
            return None

        currency = info.get("currency")
        value = self._to_decimal(raw)
        if value is None:
            return None

        if key in PERCENT_KEYS:
            value = value * Decimal("100")

        return ProviderResult(
            value=value,
            source_name=self.name,
            source_link=source_link,
            currency=currency,
        )

    def _fetch_next_earnings(self, ticker: str, source_link: str) -> ProviderResult | None:
        t = self._get_ticker(ticker)
        try:
            calendar = t.calendar
            if calendar is None:
                return None
            if isinstance(calendar, dict):
                date_val = calendar.get("Earnings Date")
                if date_val is None:
                    return None
                if isinstance(date_val, list) and len(date_val) > 0:
                    date_val = date_val[0]
                text = str(date_val)
            else:
                text = str(calendar)
        except Exception:
            return None

        return ProviderResult(
            value=text,
            source_name=self.name,
            source_link=source_link,
            currency=None,
        )

    def _fetch_buybacks(self, ticker: str, source_link: str) -> ProviderResult | None:
        t = self._get_ticker(ticker)
        info = self._get_info(ticker)
        currency = info.get("currency")
        try:
            cashflow = t.cashflow
            if cashflow is None or cashflow.empty:
                return None
            matching = [
                idx for idx in cashflow.index
                if "repurchase" in str(idx).lower() and "capital" in str(idx).lower()
            ]
            if not matching:
                matching = [
                    idx for idx in cashflow.index
                    if "repurchase" in str(idx).lower()
                ]
            if not matching:
                return None
            row = cashflow.loc[matching[0]]
            val = row.iloc[0] if hasattr(row, "iloc") else row
            value = self._to_decimal(val)
            if value is None:
                return None
            value = abs(value)
        except Exception:
            return None

        return ProviderResult(
            value=value,
            source_name=self.name,
            source_link=source_link,
            currency=currency,
        )

    def _fetch_op_profit(self, ticker: str, source_link: str) -> ProviderResult | None:
        info = self._get_info(ticker)
        currency = info.get("currency")
        revenue = info.get("totalRevenue")
        margin = info.get("operatingMargins")
        if revenue is None or margin is None:
            return None
        value = self._to_decimal(revenue)
        margin_dec = self._to_decimal(margin)
        if value is None or margin_dec is None:
            return None
        return ProviderResult(
            value=value * margin_dec,
            source_name=self.name,
            source_link=source_link,
            currency=currency,
        )

    def _fetch_sales_growth(self, ticker: str, source_link: str) -> ProviderResult | None:
        info = self._get_info(ticker)
        raw = info.get("revenueGrowth")
        if raw is None:
            return None
        value = self._to_decimal(raw)
        if value is None:
            return None
        return ProviderResult(
            value=value * Decimal("100"),
            source_name=self.name,
            source_link=source_link,
            currency=None,
        )

    def _fetch_insider_transactions(self, ticker: str, source_link: str) -> ProviderResult | None:
        t = self._get_ticker(ticker)
        try:
            df = t.insider_transactions
            if df is None or (hasattr(df, "empty") and df.empty):
                return None
            if not hasattr(df, "iterrows"):
                return None

            text_col = None
            for col in df.columns:
                if "text" in str(col).lower() or "transaction" in str(col).lower():
                    text_col = col
                    break

            shares_col = None
            for col in df.columns:
                if "shares" in str(col).lower():
                    shares_col = col
                    break

            buys = 0
            sales = 0
            for _, row in df.iterrows():
                if text_col is not None:
                    desc = str(row.get(text_col, "")).lower()
                    if "sale" in desc or "sold" in desc:
                        sales += 1
                    elif "purchase" in desc or "buy" in desc or "bought" in desc:
                        buys += 1
                elif shares_col is not None:
                    val = row.get(shares_col)
                    try:
                        if float(val) > 0:
                            buys += 1
                        elif float(val) < 0:
                            sales += 1
                    except (TypeError, ValueError):
                        pass

            if buys == 0 and sales == 0:
                summary = f"{len(df)} Transaktionen (letzte 6 Monate)"
            else:
                parts = []
                if buys:
                    parts.append(f"{buys} Kauf" if buys == 1 else f"{buys} Käufe")
                if sales:
                    parts.append(f"{sales} Verkauf" if sales == 1 else f"{sales} Verkäufe")
                summary = ", ".join(parts) + " (letzte 6 Monate)"
        except Exception:
            return None

        return ProviderResult(
            value=summary,
            source_name=self.name,
            source_link=source_link,
            currency=None,
        )
