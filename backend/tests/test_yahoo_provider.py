from decimal import Decimal
from unittest.mock import patch

import pandas as pd
import pytest

from app.providers.yahoo import YahooFinanceProvider


@pytest.fixture
def provider():
    return YahooFinanceProvider()


def _financials(year=2023):
    dates = [pd.Timestamp(f"{year}-12-31"), pd.Timestamp(f"{year - 1}-12-31")]
    return pd.DataFrame(
        {
            dates[0]: [8_000_000, Decimal("5.00")],
            dates[1]: [7_000_000, Decimal("4.50")],
        },
        index=["Net Income", "Diluted EPS"],
    )


def _cashflow(year=2023):
    dates = [pd.Timestamp(f"{year}-12-31")]
    return pd.DataFrame(
        {dates[0]: [18_000_000, -3_000_000, -2_000_000]},
        index=["Operating Cash Flow", "Capital Expenditure", "Cash Dividends Paid"],
    )


def test_stock_price_snapshot(provider):
    with patch.object(provider, "_get_info", return_value={"currentPrice": 189.5, "currency": "USD"}):
        result = provider.fetch("AAPL", "stock_price")
    assert result is not None
    assert result.value == Decimal("189.5")
    assert result.currency == "USD"


def test_currency_fetch(provider):
    with patch.object(provider, "_get_info", return_value={"currency": "usd"}):
        result = provider.fetch("AAPL", "currency")
    assert result is not None
    assert result.value == "USD"


def test_exchange_rate_usd_company(provider):
    with patch.object(provider, "_get_info", return_value={"currency": "USD"}), \
         patch("app.fx.routes._cache", {"rates": {"EUR": 0.92, "USD": 1.0}}):
        result = provider.fetch("AAPL", "exchange_rate")
    assert result is not None
    assert abs(result.value - Decimal("0.92")) < Decimal("0.001")


def test_exchange_rate_krw_company(provider):
    with patch.object(provider, "_get_info", return_value={"currency": "KRW"}), \
         patch("app.fx.routes._cache", {"rates": {"EUR": 0.92, "KRW": 1390.0, "USD": 1.0}}):
        result = provider.fetch("000660.KS", "exchange_rate")
    assert result is not None
    expected = Decimal("0.92") / Decimal("1390")
    assert abs(result.value - expected) < Decimal("0.0001")


def test_debt_from_info(provider):
    with patch.object(provider, "_get_info", return_value={"totalDebt": 5_000_000_000, "currency": "USD"}):
        result = provider.fetch("AAPL", "debt")
    assert result is not None
    assert result.value == Decimal("5000000000")


def test_cash_from_info(provider):
    with patch.object(provider, "_get_info", return_value={"totalCash": 30_000_000_000, "currency": "USD"}):
        result = provider.fetch("AAPL", "cash")
    assert result is not None
    assert result.value == Decimal("30000000000")


def test_historical_net_income(provider):
    with patch.object(provider, "_get_financials", return_value=_financials(2023)), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "net_income", period_type="FY", period_year=2023)
    assert result is not None
    assert result.value == Decimal("8000000")


def test_historical_eps(provider):
    with patch.object(provider, "_get_financials", return_value=_financials(2023)), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "eps", period_type="FY", period_year=2023)
    assert result is not None
    assert result.value == Decimal("5.00")


def test_historical_op_cash_flow(provider):
    with patch.object(provider, "_get_cashflow", return_value=_cashflow(2023)), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "op_cash_flow", period_type="FY", period_year=2023)
    assert result is not None
    assert result.value == Decimal("18000000")


def test_historical_capex_abs_value(provider):
    with patch.object(provider, "_get_cashflow", return_value=_cashflow(2023)), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "capex", period_type="FY", period_year=2023)
    assert result is not None
    assert result.value == Decimal("3000000")


def test_historical_dividends_abs_value(provider):
    with patch.object(provider, "_get_cashflow", return_value=_cashflow(2023)), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "dividends", period_type="FY", period_year=2023)
    assert result is not None
    assert result.value == Decimal("2000000")


def test_sbc_not_fetched_by_yahoo(provider):
    with patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("AAPL", "sbc")
    assert result is None


def test_year_not_found_returns_none(provider):
    with patch.object(provider, "_get_financials", return_value=_financials(2023)), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "net_income", period_type="FY", period_year=2010)
    assert result is None
