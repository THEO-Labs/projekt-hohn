from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.providers.yahoo import YahooFinanceProvider


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
    assert "2 Kaeufe" in result.value
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
