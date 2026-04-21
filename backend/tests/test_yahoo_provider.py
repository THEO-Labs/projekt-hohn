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


def _balance_sheet(year=2023):
    dates = [pd.Timestamp(f"{year}-12-31")]
    return pd.DataFrame(
        {dates[0]: [3_726_000_000, 2_558_000_000, 3_771_000_000, 2_291_000_000]},
        index=["Cash And Cash Equivalents", "Other Short Term Investments", "Long Term Investments", "Total Debt"],
    )


def test_market_cap_snapshot(provider):
    with patch.object(provider, "_get_info", return_value={"marketCap": 3_000_000_000_000, "currency": "USD"}):
        result = provider.fetch("AAPL", "market_cap")
    assert result is not None
    assert result.value == Decimal("3000000000000")


def test_shares_outstanding_snapshot(provider):
    with patch.object(provider, "_get_info", return_value={"sharesOutstanding": 1_036_000_000, "currency": "USD"}):
        result = provider.fetch("NOW", "shares_outstanding")
    assert result is not None
    assert result.value == Decimal("1036000000")
    assert result.currency is None  # count, not currency


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


def test_historical_debt(provider):
    with patch.object(provider, "_get_balance_sheet", return_value=_balance_sheet(2023)), \
         patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("NOW", "debt", period_type="FY", period_year=2023)
    assert result is not None
    assert result.value == Decimal("2291000000")


def test_cash_sums_three_components(provider):
    """Cash = Cash&Eq (3.726) + ST MktSec (2.558) + LT MktSec (3.771) = 10.055B"""
    with patch.object(provider, "_get_balance_sheet", return_value=_balance_sheet(2023)), \
         patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("NOW", "cash", period_type="FY", period_year=2023)
    assert result is not None
    assert result.value == Decimal("10055000000")


def test_cash_partial_components(provider):
    """If only cash-equivalents present, still return that (no LT/ST MktSec)."""
    df = pd.DataFrame(
        {pd.Timestamp("2023-12-31"): [5_000_000_000]},
        index=["Cash And Cash Equivalents"],
    )
    with patch.object(provider, "_get_balance_sheet", return_value=df), \
         patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("NOW", "cash", period_type="FY", period_year=2023)
    assert result is not None
    assert result.value == Decimal("5000000000")


def test_sbc_not_fetched_by_yahoo(provider):
    with patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("NOW", "sbc", period_type="FY", period_year=2025)
    assert result is None


def test_snapshot_sbc_no_longer_supported(provider):
    """SBC is per-FY now; snapshot request returns None."""
    with patch.object(provider, "_get_info", return_value={"currency": "USD"}):
        result = provider.fetch("NOW", "sbc")
    assert result is None
