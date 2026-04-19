from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.providers.yahoo import YahooFinanceProvider


def _make_financials(year=2023):
    dates = [pd.Timestamp(f"{year}-12-31"), pd.Timestamp(f"{year - 1}-12-31")]
    return pd.DataFrame(
        {
            dates[0]: [100_000_000, 15_000_000, 8_000_000, 20_000_000, Decimal("5.00")],
            dates[1]: [90_000_000, 13_000_000, 7_000_000, 18_000_000, Decimal("4.50")],
        },
        index=["Total Revenue", "Operating Income", "Net Income", "EBITDA", "Diluted EPS"],
    )


def _make_balance_sheet(year=2023):
    dates = [pd.Timestamp(f"{year}-12-31")]
    return pd.DataFrame(
        {dates[0]: [5_000_000, 12_000_000]},
        index=["Cash And Cash Equivalents", "Total Debt"],
    )


def _make_cashflow(year=2023):
    dates = [pd.Timestamp(f"{year}-12-31")]
    return pd.DataFrame(
        {dates[0]: [18_000_000, 10_000_000, -3_000_000, -2_000_000]},
        index=["Operating Cash Flow", "Free Cash Flow", "Repurchase Of Capital Stock", "Cash Dividends Paid"],
    )


@pytest.fixture
def provider():
    return YahooFinanceProvider()


def test_insider_transactions_buy_sell_summary(provider):
    df = pd.DataFrame([
        {"Text": "Purchase", "Shares": 1000},
        {"Text": "Purchase", "Shares": 500},
        {"Text": "Sale", "Shares": -200},
    ])

    mock_ticker = MagicMock()
    mock_ticker.insider_transactions = df
    mock_ticker.info = {}

    with patch.object(provider, "_get_ticker", return_value=mock_ticker):
        result = provider.fetch("AAPL", "insider_transactions")

    assert result is not None
    assert "2 Käufe" in result.value
    assert "1 Verkauf" in result.value
    assert "letzte 6 Monate" in result.value


def test_insider_transactions_none_returns_none(provider):
    mock_ticker = MagicMock()
    mock_ticker.insider_transactions = None
    mock_ticker.info = {}

    with patch.object(provider, "_get_ticker", return_value=mock_ticker):
        result = provider.fetch("AAPL", "insider_transactions")

    assert result is None


def test_insider_transactions_empty_df_returns_none(provider):
    mock_ticker = MagicMock()
    mock_ticker.insider_transactions = pd.DataFrame()
    mock_ticker.info = {}

    with patch.object(provider, "_get_ticker", return_value=mock_ticker):
        result = provider.fetch("AAPL", "insider_transactions")

    assert result is None


def test_insider_transactions_single_buy(provider):
    df = pd.DataFrame([
        {"Text": "Purchase", "Shares": 1000},
    ])

    mock_ticker = MagicMock()
    mock_ticker.insider_transactions = df
    mock_ticker.info = {}

    with patch.object(provider, "_get_ticker", return_value=mock_ticker):
        result = provider.fetch("AAPL", "insider_transactions")

    assert result is not None
    assert "1 Kauf" in result.value
    assert result.source_name == "Yahoo Finance"


# Historical FY tests

def test_historical_sales(provider):
    financials = _make_financials(2023)
    with patch.object(provider, "_get_financials", return_value=financials), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "sales", period_type="FY", period_year=2023)

    assert result is not None
    assert result.value == Decimal("100000000")
    assert result.currency == "EUR"
    assert result.source_name == "Yahoo Finance"


def test_historical_net_profit(provider):
    financials = _make_financials(2023)
    with patch.object(provider, "_get_financials", return_value=financials), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "net_profit", period_type="FY", period_year=2023)

    assert result is not None
    assert result.value == Decimal("8000000")


def test_historical_ebitda(provider):
    financials = _make_financials(2023)
    with patch.object(provider, "_get_financials", return_value=financials), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "ebitda", period_type="FY", period_year=2023)

    assert result is not None
    assert result.value == Decimal("20000000")


def test_historical_eps(provider):
    financials = _make_financials(2023)
    with patch.object(provider, "_get_financials", return_value=financials), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "eps_ttm", period_type="FY", period_year=2023)

    assert result is not None
    assert result.value == Decimal("5.00")


def test_historical_cash(provider):
    balance_sheet = _make_balance_sheet(2023)
    with patch.object(provider, "_get_balance_sheet", return_value=balance_sheet), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "cash", period_type="FY", period_year=2023)

    assert result is not None
    assert result.value == Decimal("5000000")


def test_historical_debt(provider):
    balance_sheet = _make_balance_sheet(2023)
    with patch.object(provider, "_get_balance_sheet", return_value=balance_sheet), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "debt", period_type="FY", period_year=2023)

    assert result is not None
    assert result.value == Decimal("12000000")


def test_historical_op_cash_flow(provider):
    cashflow = _make_cashflow(2023)
    with patch.object(provider, "_get_cashflow", return_value=cashflow), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "op_cash_flow", period_type="FY", period_year=2023)

    assert result is not None
    assert result.value == Decimal("18000000")


def test_historical_free_cash_flow(provider):
    cashflow = _make_cashflow(2023)
    with patch.object(provider, "_get_cashflow", return_value=cashflow), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "free_cash_flow", period_type="FY", period_year=2023)

    assert result is not None
    assert result.value == Decimal("10000000")


def test_historical_buybacks_absolute_value(provider):
    cashflow = _make_cashflow(2023)
    with patch.object(provider, "_get_cashflow", return_value=cashflow), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "buybacks", period_type="FY", period_year=2023)

    assert result is not None
    assert result.value == Decimal("3000000")


def test_historical_dividends_returns_none(provider):
    cashflow = _make_cashflow(2023)
    with patch.object(provider, "_get_cashflow", return_value=cashflow), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "dividends", period_type="FY", period_year=2023)

    assert result is None


def test_snapshot_dividends_from_info(provider):
    with patch.object(provider, "_get_info", return_value={"dividendRate": 4.88, "currency": "EUR"}):
        result = provider.fetch("ALV.DE", "dividends", period_type="SNAPSHOT")

    assert result is not None
    assert result.value == Decimal("4.88")


def test_historical_op_margin(provider):
    financials = _make_financials(2023)
    with patch.object(provider, "_get_financials", return_value=financials), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "op_margin", period_type="FY", period_year=2023)

    assert result is not None
    assert result.value == Decimal("15")


def test_historical_sales_growth(provider):
    financials = _make_financials(2023)
    with patch.object(provider, "_get_financials", return_value=financials), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "sales_growth", period_type="FY", period_year=2023)

    assert result is not None
    expected = (Decimal("100000000") - Decimal("90000000")) / Decimal("90000000") * Decimal("100")
    assert abs(result.value - expected) < Decimal("0.01")


def test_historical_year_not_found_returns_none(provider):
    financials = _make_financials(2023)
    with patch.object(provider, "_get_financials", return_value=financials), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "sales", period_type="FY", period_year=2010)

    assert result is None


def test_always_current_keys_not_routed_to_historical(provider):
    mock_info = {"currentPrice": 150.0, "currency": "EUR"}
    with patch.object(provider, "_get_info", return_value=mock_info):
        result = provider.fetch("AAPL", "stock_price", period_type="FY", period_year=2023)

    assert result is not None
    assert result.value == Decimal("150.0")


def test_historical_op_profit(provider):
    financials = _make_financials(2023)
    with patch.object(provider, "_get_financials", return_value=financials), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("ALV.DE", "op_profit", period_type="FY", period_year=2023)

    assert result is not None
    assert result.value == Decimal("15000000")
