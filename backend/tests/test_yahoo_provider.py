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


def _make_insurance_financials(year=2023):
    """Insurance companies where Yahoo maps 'Operating Income' to revenue."""
    dates = [pd.Timestamp(f"{year}-12-31"), pd.Timestamp(f"{year - 1}-12-31")]
    return pd.DataFrame(
        {
            dates[0]: [62_312_000_000, 62_312_000_000, 6_118_000_000],
            dates[1]: [60_400_000_000, 60_400_000_000, 5_900_000_000],
        },
        index=["Total Revenue", "Operating Income", "Net Income"],
    )


def test_op_profit_rejected_when_equals_sales(provider):
    financials = _make_insurance_financials(2023)
    with patch.object(provider, "_get_financials", return_value=financials), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("MUV2.DE", "op_profit", period_type="FY", period_year=2023)
    assert result is None


def test_op_margin_rejected_when_equals_100_percent(provider):
    financials = _make_insurance_financials(2023)
    with patch.object(provider, "_get_financials", return_value=financials), \
         patch.object(provider, "_get_info", return_value={"currency": "EUR"}):
        result = provider.fetch("MUV2.DE", "op_margin", period_type="FY", period_year=2023)
    assert result is None


def test_snapshot_op_profit_rejected_when_margin_100_percent(provider):
    with patch.object(provider, "_get_info", return_value={
        "totalRevenue": 62_312_000_000, "operatingMargins": 1.0, "currency": "EUR"
    }):
        result = provider.fetch("MUV2.DE", "op_profit", period_type="SNAPSHOT")
    assert result is None


def test_cashflow_dividends_abs_applied(provider):
    cashflow = pd.DataFrame(
        {pd.Timestamp("2023-12-31"): [-2_613_000_000]},
        index=["Cash Dividends Paid"],
    )
    with patch.object(provider, "_get_cashflow", return_value=cashflow):
        result = provider._fetch_from_cashflow(
            "MUV2.DE", "dividends", 2023, "https://x", "EUR", abs_value=True
        )
    assert result is not None
    assert result.value == Decimal("2613000000")


def test_dividend_return_computed_from_rate_and_price(provider):
    with patch.object(provider, "_get_info", return_value={
        "dividendRate": 3000, "currentPrice": 280000, "currency": "KRW"
    }):
        result = provider.fetch("000660.KS", "dividend_return", period_type="SNAPSHOT")
    assert result is not None
    assert abs(result.value - Decimal("1.0714")) < Decimal("0.01")
    assert result.currency is None


def test_dividend_return_fallback_to_yield_when_no_rate(provider):
    with patch.object(provider, "_get_info", return_value={
        "dividendYield": 0.0438, "currency": "EUR"
    }):
        result = provider.fetch("ALV.DE", "dividend_return", period_type="SNAPSHOT")
    assert result is not None
    assert abs(result.value - Decimal("4.38")) < Decimal("0.01")


def test_pe_ttm_has_no_currency(provider):
    with patch.object(provider, "_get_info", return_value={
        "trailingPE": 11.98, "currency": "EUR"
    }):
        result = provider.fetch("MUV2.DE", "pe_ttm", period_type="SNAPSHOT")
    assert result is not None
    assert result.value == Decimal("11.98")
    assert result.currency is None


def test_shares_outstanding_has_no_currency(provider):
    with patch.object(provider, "_get_info", return_value={
        "sharesOutstanding": 127961889, "currency": "EUR"
    }):
        result = provider.fetch("MUV2.DE", "shares_outstanding", period_type="SNAPSHOT")
    assert result is not None
    assert result.currency is None


def test_op_margin_snapshot_has_no_currency(provider):
    with patch.object(provider, "_get_info", return_value={
        "operatingMargins": 0.107, "currency": "EUR"
    }):
        result = provider.fetch("MUV2.DE", "op_margin", period_type="SNAPSHOT")
    assert result is not None
    assert result.currency is None


def test_stock_price_keeps_currency(provider):
    with patch.object(provider, "_get_info", return_value={
        "currentPrice": 564.80, "currency": "EUR"
    }):
        result = provider.fetch("MUV2.DE", "stock_price", period_type="SNAPSHOT")
    assert result is not None
    assert result.currency == "EUR"
