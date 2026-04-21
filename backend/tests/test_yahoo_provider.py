from decimal import Decimal
from unittest.mock import patch

import pandas as pd
import pytest

from app.providers.yahoo import YahooFinanceProvider


@pytest.fixture
def provider():
    return YahooFinanceProvider()


def _balance_sheet(year=2025):
    dates = [pd.Timestamp(f"{year}-12-31")]
    return pd.DataFrame(
        {dates[0]: [3_726_000_000, 2_558_000_000, 3_771_000_000, 1_491_000_000, 800_000_000]},
        index=[
            "Cash And Cash Equivalents",
            "Other Short Term Investments",
            "Available For Sale Securities",
            "Long Term Debt",
            "Long Term Capital Lease Obligation",
        ],
    )


def _cashflow(year=2025):
    dates = [pd.Timestamp(f"{year}-12-31")]
    return pd.DataFrame(
        {dates[0]: [4_636_000_000, 1_955_000_000, -1_840_000_000, 0]},
        index=[
            "Free Cash Flow",
            "Stock Based Compensation",
            "Repurchase Of Capital Stock",
            "Cash Dividends Paid",
        ],
    )


def _financials(year=2025):
    dates = [pd.Timestamp(f"{year}-12-31")]
    return pd.DataFrame(
        {dates[0]: [1_748_000_000]},
        index=["Net Income"],
    )


def test_stock_price_snapshot(provider):
    with patch.object(provider, "_get_info", return_value={"currentPrice": 100.97, "currency": "USD"}):
        result = provider.fetch("NOW", "stock_price")
    assert result is not None
    assert result.value == Decimal("100.97")


def test_market_cap_snapshot(provider):
    with patch.object(provider, "_get_info", return_value={"marketCap": 101_100_000_000, "currency": "USD"}):
        result = provider.fetch("NOW", "market_cap")
    assert result.value == Decimal("101100000000")


def test_shares_outstanding_snapshot(provider):
    with patch.object(provider, "_get_info", return_value={"sharesOutstanding": 1_036_000_000, "currency": "USD"}):
        result = provider.fetch("NOW", "shares_outstanding")
    assert result.value == Decimal("1036000000")


def test_cash_and_equivalents_per_fy(provider):
    with patch.object(provider, "_get_balance_sheet", return_value=_balance_sheet()), \
         patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("NOW", "cash_and_equivalents", period_type="FY", period_year=2025)
    assert result.value == Decimal("3726000000")


def test_marketable_securities_st(provider):
    with patch.object(provider, "_get_balance_sheet", return_value=_balance_sheet()), \
         patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("NOW", "marketable_securities_st", period_type="FY", period_year=2025)
    assert result.value == Decimal("2558000000")


def test_marketable_securities_lt(provider):
    with patch.object(provider, "_get_balance_sheet", return_value=_balance_sheet()), \
         patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("NOW", "marketable_securities_lt", period_type="FY", period_year=2025)
    assert result.value == Decimal("3771000000")


def test_long_term_debt(provider):
    with patch.object(provider, "_get_balance_sheet", return_value=_balance_sheet()), \
         patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("NOW", "long_term_debt", period_type="FY", period_year=2025)
    assert result.value == Decimal("1491000000")


def test_lease_liabilities(provider):
    with patch.object(provider, "_get_balance_sheet", return_value=_balance_sheet()), \
         patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("NOW", "lease_liabilities", period_type="FY", period_year=2025)
    assert result.value == Decimal("800000000")


def test_fcf_per_fy(provider):
    with patch.object(provider, "_get_cashflow", return_value=_cashflow()), \
         patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("NOW", "fcf", period_type="FY", period_year=2025)
    assert result.value == Decimal("4636000000")


def test_sbc_per_fy(provider):
    with patch.object(provider, "_get_cashflow", return_value=_cashflow()), \
         patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("NOW", "sbc", period_type="FY", period_year=2025)
    assert result.value == Decimal("1955000000")


def test_buyback_volume_abs(provider):
    with patch.object(provider, "_get_cashflow", return_value=_cashflow()), \
         patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("NOW", "buyback_volume", period_type="FY", period_year=2025)
    assert result.value == Decimal("1840000000")


def test_net_income_per_fy(provider):
    with patch.object(provider, "_get_financials", return_value=_financials()), \
         patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("NOW", "net_income", period_type="FY", period_year=2025)
    assert result.value == Decimal("1748000000")


def test_year_not_found_returns_none(provider):
    with patch.object(provider, "_get_financials", return_value=_financials(2025)), \
         patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("NOW", "net_income", period_type="FY", period_year=2010)
    assert result is None
