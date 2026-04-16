import pytest
import respx
import httpx
from app.companies.lookup import lookup_by_isin, lookup_by_ticker, exchange_to_currency


def test_exchange_to_currency_known():
    assert exchange_to_currency("US") == "USD"
    assert exchange_to_currency("LN") == "GBP"
    assert exchange_to_currency("GR") == "EUR"
    assert exchange_to_currency("SW") == "CHF"


def test_exchange_to_currency_unknown():
    assert exchange_to_currency("ZZ") is None


OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

APPLE_RESPONSE = [
    {
        "data": [
            {
                "figi": "BBG000B9XRY4",
                "name": "APPLE INC",
                "ticker": "AAPL",
                "exchCode": "US",
                "marketSector": "Equity",
                "securityType": "Common Stock",
            }
        ]
    }
]

NOT_FOUND_RESPONSE = [{"warning": "No identifier found."}]


@pytest.mark.asyncio
async def test_lookup_by_isin_found():
    with respx.mock:
        respx.post(OPENFIGI_URL).mock(
            return_value=httpx.Response(200, json=APPLE_RESPONSE)
        )
        result = await lookup_by_isin("US0378331005")

    assert result["name"] == "APPLE INC"
    assert result["ticker"] == "AAPL"
    assert result["isin"] == "US0378331005"
    assert result["currency"] == "USD"


@pytest.mark.asyncio
async def test_lookup_by_isin_not_found():
    with respx.mock:
        respx.post(OPENFIGI_URL).mock(
            return_value=httpx.Response(200, json=NOT_FOUND_RESPONSE)
        )
        result = await lookup_by_isin("US0000000000")

    assert result is None


@pytest.mark.asyncio
async def test_lookup_by_ticker_found():
    with respx.mock:
        respx.post(OPENFIGI_URL).mock(
            return_value=httpx.Response(200, json=APPLE_RESPONSE)
        )
        result = await lookup_by_ticker("AAPL")

    assert result["name"] == "APPLE INC"
    assert result["ticker"] == "AAPL"
    assert result["currency"] == "USD"


@pytest.mark.asyncio
async def test_lookup_network_error_returns_none():
    with respx.mock:
        respx.post(OPENFIGI_URL).mock(side_effect=httpx.ConnectError("timeout"))
        result = await lookup_by_isin("US0378331005")

    assert result is None


@pytest.mark.asyncio
async def test_lookup_unknown_exchange_currency_is_none():
    response_unknown_exch = [
        {
            "data": [
                {
                    "figi": "BBG000XXXXX",
                    "name": "SOME COMPANY",
                    "ticker": "SOME",
                    "exchCode": "ZZ",
                    "marketSector": "Equity",
                    "securityType": "Common Stock",
                }
            ]
        }
    ]
    with respx.mock:
        respx.post(OPENFIGI_URL).mock(
            return_value=httpx.Response(200, json=response_unknown_exch)
        )
        result = await lookup_by_isin("US0378331005")

    assert result["currency"] is None
    assert result["name"] == "SOME COMPANY"
