from decimal import Decimal
from unittest.mock import patch

import pandas as pd
import pytest

from app.providers.yahoo import YahooFinanceProvider


@pytest.fixture
def provider():
    return YahooFinanceProvider()


def _make_financials(year=2023):
    dates = [pd.Timestamp(f"{year}-12-31"), pd.Timestamp(f"{year - 1}-12-31")]
    return pd.DataFrame(
        {
            dates[0]: [100_000_000, 8_000_000],
            dates[1]: [90_000_000, 7_000_000],
        },
        index=["Total Revenue", "Net Income"],
    )


def test_snapshot_stock_price(provider):
    with patch.object(provider, "_get_info", return_value={"currentPrice": 189.5, "currency": "USD"}):
        result = provider.fetch("AAPL", "stock_price")
    assert result is not None
    assert result.value == Decimal("189.5")
    assert result.currency == "USD"


def test_snapshot_market_cap(provider):
    with patch.object(provider, "_get_info", return_value={"marketCap": 3_000_000_000_000, "currency": "USD"}):
        result = provider.fetch("AAPL", "market_cap")
    assert result is not None
    assert result.value == Decimal("3000000000000")


def test_snapshot_shares_outstanding_no_currency(provider):
    with patch.object(provider, "_get_info", return_value={"sharesOutstanding": 15_000_000_000, "currency": "USD"}):
        result = provider.fetch("AAPL", "shares_outstanding")
    assert result is not None
    assert result.currency is None


def test_historical_sales(provider):
    financials = _make_financials(2023)
    with patch.object(provider, "_get_financials", return_value=financials), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "sales", period_type="FY", period_year=2023)
    assert result is not None
    assert result.value == Decimal("100000000")
    assert result.currency == "EUR"


def test_historical_net_income(provider):
    financials = _make_financials(2023)
    with patch.object(provider, "_get_financials", return_value=financials), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "net_income", period_type="FY", period_year=2023)
    assert result is not None
    assert result.value == Decimal("8000000")


def test_historical_year_not_found_returns_none(provider):
    financials = _make_financials(2023)
    with patch.object(provider, "_get_financials", return_value=financials), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "sales", period_type="FY", period_year=2010)
    assert result is None


def test_always_current_key_ignores_period(provider):
    mock_info = {"currentPrice": 150.0, "currency": "EUR"}
    with patch.object(provider, "_get_info", return_value=mock_info):
        result = provider.fetch("AAPL", "stock_price", period_type="FY", period_year=2023)
    assert result is not None
    assert result.value == Decimal("150.0")


def test_fcf_margin_not_fetched_by_yahoo(provider):
    """Yahoo has no direct non-GAAP FCF margin field, so provider returns None."""
    with patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("AAPL", "fcf_margin_non_gaap", period_type="FY", period_year=2023)
    assert result is None


def test_sbc_not_fetched_by_yahoo(provider):
    with patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("AAPL", "sbc", period_type="FY", period_year=2023)
    assert result is None


def test_sanity_rejects_absurd_stock_price(provider):
    with patch.object(provider, "_get_info", return_value={"currentPrice": 5e11, "currency": "USD"}):
        result = provider.fetch("AAPL", "stock_price")
    assert result is None
