from typing import TypedDict

import httpx

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
TIMEOUT = 5.0

EXCH_TO_CCY = {
    "US": "USD", "UN": "USD", "UW": "USD", "UQ": "USD", "UA": "USD", "UR": "USD", "UP": "USD",
    "GR": "EUR", "GY": "EUR", "GF": "EUR", "GM": "EUR",
    "FP": "EUR", "NA": "EUR", "BB": "EUR", "IM": "EUR", "SM": "EUR", "SE": "SEK", "HE": "EUR",
    "LN": "GBP", "LI": "GBP",
    "HK": "HKD", "JP": "JPY", "JT": "JPY", "KS": "KRW", "TT": "TWD", "AU": "AUD",
    "CN": "CAD", "SI": "SGD", "SS": "CNY", "SZ": "CNY",
    "SW": "CHF", "VX": "CHF",
}


class LookupResult(TypedDict):
    name: str | None
    ticker: str | None
    isin: str | None
    currency: str | None


def exchange_to_currency(exch_code: str) -> str | None:
    return EXCH_TO_CCY.get(exch_code)


async def _call_openfigi(payload: list[dict]) -> list[dict] | None:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(OPENFIGI_URL, json=payload)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, httpx.TimeoutException, Exception):
        return None


def _parse_first_equity(results: list[dict], isin: str | None = None) -> LookupResult | None:
    if not results:
        return None
    first = results[0]
    if "warning" in first or "error" in first:
        return None
    data = first.get("data", [])
    for item in data:
        if item.get("marketSector") == "Equity" and item.get("securityType") == "Common Stock":
            exch = item.get("exchCode")
            return LookupResult(
                name=item.get("name"),
                ticker=item.get("ticker"),
                isin=isin,
                currency=exchange_to_currency(exch) if exch else None,
            )
    if data:
        item = data[0]
        exch = item.get("exchCode")
        return LookupResult(
            name=item.get("name"),
            ticker=item.get("ticker"),
            isin=isin,
            currency=exchange_to_currency(exch) if exch else None,
        )
    return None


async def lookup_by_isin(isin: str) -> LookupResult | None:
    payload = [{"idType": "ID_ISIN", "idValue": isin}]
    results = await _call_openfigi(payload)
    if results is None:
        return None
    return _parse_first_equity(results, isin=isin)


async def lookup_by_ticker(ticker: str) -> LookupResult | None:
    payload = [{"idType": "TICKER", "idValue": ticker}]
    results = await _call_openfigi(payload)
    if results is None:
        return None
    return _parse_first_equity(results, isin=None)
