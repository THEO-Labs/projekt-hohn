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
        {dates[0]: [8_000_000], dates[1]: [7_000_000]},
        index=["Net Income"],
    )


def _cashflow(year=2023):
    dates = [pd.Timestamp(f"{year}-12-31")]
    return pd.DataFrame(
        {dates[0]: [18_000_000, -3_000_000]},
        index=["Operating Cash Flow", "Capital Expenditure"],
    )


def test_market_cap_snapshot(provider):
    with patch.object(provider, "_get_info", return_value={"marketCap": 3_000_000_000_000, "currency": "USD"}):
        result = provider.fetch("AAPL", "market_cap")
    assert result is not None
    assert result.value == Decimal("3000000000000")
    assert result.currency == "USD"


def test_sbc_not_fetched_by_yahoo(provider):
    with patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("AAPL", "sbc")
    assert result is None


def test_historical_net_income(provider):
    with patch.object(provider, "_get_financials", return_value=_financials(2023)), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "net_income", period_type="FY", period_year=2023)
    assert result is not None
    assert result.value == Decimal("8000000")


def test_historical_op_cash_flow(provider):
    with patch.object(provider, "_get_cashflow", return_value=_cashflow(2023)), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "op_cash_flow", period_type="FY", period_year=2023)
    assert result is not None
    assert result.value == Decimal("18000000")


def test_historical_capex_abs(provider):
    with patch.object(provider, "_get_cashflow", return_value=_cashflow(2023)), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "capex", period_type="FY", period_year=2023)
    assert result is not None
    assert result.value == Decimal("3000000")


def test_year_not_found_returns_none(provider):
    with patch.object(provider, "_get_financials", return_value=_financials(2023)), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "net_income", period_type="FY", period_year=2010)
    assert result is None


def test_unsupported_key_returns_none(provider):
    with patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("AAPL", "stock_price")
    assert result is None
