import logging
from decimal import Decimal, InvalidOperation

import yfinance
from cachetools import TTLCache

from app.providers.base import ProviderResult
from app.values.always_current import ALWAYS_CURRENT_KEYS
from app.values.currency_keys import CURRENCY_KEYS

logger = logging.getLogger(__name__)


INFO_KEY_MAP = {
    "market_cap": "marketCap",
    "shares_outstanding": "sharesOutstanding",
    "debt": "totalDebt",
    "cash": "totalCash",
}

FINANCIALS_ROWS = {
    "net_income": ["Net Income", "Net Income Common Stockholders"],
}

BALANCE_SHEET_ROWS = {
    "debt": ["Total Debt", "Long Term Debt", "Net Debt"],
    "cash": ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments", "Cash And Short Term Investments"],
}

CASHFLOW_ROWS = {
    "op_cash_flow": ["Operating Cash Flow", "Cash From Operating Activities"],
    "capex": ["Capital Expenditure", "Capital Expenditures"],
}

VALUE_SANITY_CHECKS: dict[str, tuple[float, float]] = {
    "market_cap": (0, 1e16),
    "shares_outstanding": (0, 1e15),
    "sbc": (0, 1e15),
    "net_income": (-1e15, 1e15),
    "op_cash_flow": (-1e15, 1e15),
    "capex": (0, 1e15),
    "debt": (0, 1e15),
    "cash": (0, 1e15),
}


class YahooFinanceProvider:
    name = "Yahoo Finance"
    supported_keys = (
        set(INFO_KEY_MAP.keys())
        | set(FINANCIALS_ROWS.keys())
        | set(BALANCE_SHEET_ROWS.keys())
        | set(CASHFLOW_ROWS.keys())
    )

    def __init__(self) -> None:
        self._ticker_cache: TTLCache = TTLCache(maxsize=100, ttl=300)
        self._info_cache: TTLCache = TTLCache(maxsize=100, ttl=300)
        self._financials_cache: TTLCache = TTLCache(maxsize=100, ttl=300)
        self._balance_sheet_cache: TTLCache = TTLCache(maxsize=100, ttl=300)
        self._cashflow_cache: TTLCache = TTLCache(maxsize=100, ttl=300)
        self._isin_ticker_cache: TTLCache = TTLCache(maxsize=200, ttl=3600)

    def resolve_ticker_from_isin(self, isin: str) -> str | None:
        if isin in self._isin_ticker_cache:
            return self._isin_ticker_cache[isin]
        try:
            import httpx
            r = httpx.get(
                f"https://query2.finance.yahoo.com/v1/finance/search?q={isin}&quotesCount=3&newsCount=0",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=5.0,
            )
            data = r.json()
            for q in data.get("quotes", []):
                if q.get("quoteType") == "EQUITY":
                    symbol = q.get("symbol")
                    if symbol:
                        self._isin_ticker_cache[isin] = symbol
                        return symbol
        except Exception as e:
            logger.warning("Yahoo ISIN search failed for %s: %s", isin, e)
        return None

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
            import math
            if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                return None
            d = Decimal(str(value))
            if d.is_nan() or d.is_infinite():
                return None
            return d
        except (InvalidOperation, ValueError, TypeError):
            return None

    def _sanity_check(self, key: str, value: Decimal) -> Decimal | None:
        limits = VALUE_SANITY_CHECKS.get(key)
        if limits is None:
            return value
        lo, hi = limits
        try:
            fval = float(value)
        except (ValueError, OverflowError):
            return None
        if fval < lo or fval > hi:
            logger.warning("Sanity check failed %s=%s [%s,%s]", key, value, lo, hi)
            return None
        return value

    def _find_year_column(self, df, year: int):
        for col in df.columns:
            try:
                if hasattr(col, "year") and col.year == year:
                    return col
            except Exception:
                continue
        return None

    def _get_row_value(self, df, col, row_names: list[str]) -> Decimal | None:
        for row in row_names:
            if row in df.index:
                raw = df.loc[row, col]
                value = self._to_decimal(raw)
                if value is not None:
                    return value
        return None

    def fetch(
        self,
        ticker: str,
        key: str,
        period_type: str = "FY",
        period_year: int | None = None,
    ) -> ProviderResult | None:
        source_link = f"https://finance.yahoo.com/quote/{ticker}"

        if key in ALWAYS_CURRENT_KEYS and key in INFO_KEY_MAP:
            return self._fetch_snapshot_from_info(ticker, key, source_link)

        if period_type == "FY" and period_year is not None:
            if key in FINANCIALS_ROWS:
                return self._fetch_from_financials(ticker, key, period_year, source_link)
            if key in BALANCE_SHEET_ROWS:
                return self._fetch_from_balance_sheet(ticker, key, period_year, source_link)
            if key in CASHFLOW_ROWS:
                abs_value = key == "capex"
                return self._fetch_from_cashflow(ticker, key, period_year, source_link, abs_value=abs_value)

        return None

    def _fetch_from_balance_sheet(self, ticker: str, key: str, period_year: int, source_link: str) -> ProviderResult | None:
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
            value = self._sanity_check(key, value)
            if value is None:
                return None
            info = self._get_info(ticker)
            currency = info.get("currency") if key in CURRENCY_KEYS else None
            return ProviderResult(value=value, source_name=self.name, source_link=source_link, currency=currency)
        except Exception as e:
            logger.warning("Yahoo balance-sheet fetch failed %s/%s FY%s: %s", ticker, key, period_year, e)
            return None

    def _fetch_snapshot_from_info(self, ticker: str, key: str, source_link: str) -> ProviderResult | None:
        info = self._get_info(ticker)
        yf_key = INFO_KEY_MAP.get(key)
        if yf_key is None:
            return None
        raw = info.get(yf_key)
        if raw is None:
            return None
        value = self._to_decimal(raw)
        if value is None:
            return None
        value = self._sanity_check(key, value)
        if value is None:
            return None
        currency = info.get("currency") if key in CURRENCY_KEYS else None
        return ProviderResult(
            value=value,
            source_name=self.name,
            source_link=source_link,
            currency=currency,
        )

    def _fetch_from_financials(self, ticker: str, key: str, period_year: int, source_link: str) -> ProviderResult | None:
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
            value = self._sanity_check(key, value)
            if value is None:
                return None
            info = self._get_info(ticker)
            currency = info.get("currency") if key in CURRENCY_KEYS else None
            return ProviderResult(value=value, source_name=self.name, source_link=source_link, currency=currency)
        except Exception as e:
            logger.warning("Yahoo financials fetch failed %s/%s FY%s: %s", ticker, key, period_year, e)
            return None

    def _fetch_from_cashflow(self, ticker: str, key: str, period_year: int, source_link: str, abs_value: bool = False) -> ProviderResult | None:
        try:
            df = self._get_cashflow(ticker)
            if df is None or df.empty:
                return None
            col = self._find_year_column(df, period_year)
            if col is None:
                return None
            value = self._get_row_value(df, col, CASHFLOW_ROWS[key])
            if value is None:
                return None
            if abs_value:
                value = abs(value)
            value = self._sanity_check(key, value)
            if value is None:
                return None
            info = self._get_info(ticker)
            currency = info.get("currency") if key in CURRENCY_KEYS else None
            return ProviderResult(value=value, source_name=self.name, source_link=source_link, currency=currency)
        except Exception as e:
            logger.warning("Yahoo cashflow fetch failed %s/%s FY%s: %s", ticker, key, period_year, e)
            return None
