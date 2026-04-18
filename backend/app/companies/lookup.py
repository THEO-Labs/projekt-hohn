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

EXCH_TO_YAHOO_SUFFIX = {
    "GR": ".DE", "GY": ".DE", "GF": ".DE", "GM": ".DE",
    "FP": ".PA", "NA": ".AS", "BB": ".BR", "IM": ".MI", "SM": ".MC",
    "LN": ".L", "LI": ".L",
    "HK": ".HK", "JP": ".T", "JT": ".T",
    "KS": ".KS", "TT": ".TW",
    "AU": ".AX", "CN": ".TO",
    "SI": ".SI", "SS": ".SS", "SZ": ".SZ",
    "SW": ".SW", "VX": ".SW",
    "SE": ".ST", "HE": ".HE",
    "ID": ".JK", "AT": ".VI", "IS": ".IS",
    "NO": ".OL", "CO": ".CO", "WA": ".WA",
    "LS": ".LS",
}

ISIN_COUNTRY_TO_YAHOO_SUFFIX = {
    "DE": ".DE", "FR": ".PA", "NL": ".AS", "BE": ".BR",
    "IT": ".MI", "ES": ".MC", "GB": ".L", "IE": ".L",
    "CH": ".SW", "AT": ".VI", "SE": ".ST", "NO": ".OL",
    "DK": ".CO", "FI": ".HE", "PT": ".LS", "PL": ".WA",
    "HK": ".HK", "JP": ".T", "AU": ".AX", "CA": ".TO",
    "SG": ".SI", "KR": ".KS", "TW": ".TW",
    "BR": ".SA", "MX": ".MX", "ZA": ".JO",
    "IN": ".NS", "CN": ".SS",
}


def _to_yahoo_ticker(ticker: str, exch_code: str | None, isin: str | None) -> str:
    if "." in ticker:
        return ticker

    if exch_code:
        if exch_code in ("US", "UN", "UW", "UQ", "UA", "UR", "UP"):
            return ticker
        suffix = EXCH_TO_YAHOO_SUFFIX.get(exch_code)
        if suffix:
            return ticker + suffix

    if isin and len(isin) >= 2:
        country = isin[:2].upper()
        if country == "US":
            return ticker
        suffix = ISIN_COUNTRY_TO_YAHOO_SUFFIX.get(country)
        if suffix:
            return ticker + suffix

    return ticker


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


ISIN_COUNTRY_TO_HOME_EXCHANGES = {
    "US": {"US", "UN", "UW", "UQ", "UA", "UR", "UP"},
    "DE": {"GR", "GY", "GF", "GM"},
    "FR": {"FP"}, "NL": {"NA"}, "BE": {"BB"}, "IT": {"IM"}, "ES": {"SM"},
    "GB": {"LN", "LI"}, "IE": {"LN", "LI"},
    "CH": {"SW", "VX"}, "AT": {"AT"},
    "SE": {"SE"}, "NO": {"NO"}, "DK": {"CO"}, "FI": {"HE"}, "PT": {"LS"}, "PL": {"WA"},
    "HK": {"HK"}, "JP": {"JP", "JT"}, "AU": {"AU"}, "CA": {"CN"},
    "SG": {"SI"}, "KR": {"KS"}, "TW": {"TT"},
    "BR": {"BS"}, "MX": {"MM"}, "ZA": {"SJ"},
    "IN": {"II", "IB"}, "CN": {"SS", "SZ"},
}


def _parse_first_equity(results: list[dict], isin: str | None = None) -> LookupResult | None:
    if not results:
        return None
    first = results[0]
    if "warning" in first or "error" in first:
        return None
    data = first.get("data", [])
    equities = [d for d in data if d.get("marketSector") == "Equity" and d.get("securityType") == "Common Stock"]
    if not equities:
        equities = data
    if not equities:
        return None

    item = equities[0]
    if isin and len(isin) >= 2:
        home_exchanges = ISIN_COUNTRY_TO_HOME_EXCHANGES.get(isin[:2].upper(), set())
        for d in equities:
            if d.get("exchCode") in home_exchanges:
                item = d
                break
    exch = item.get("exchCode")
    raw_ticker = item.get("ticker")
    yahoo_ticker = _to_yahoo_ticker(raw_ticker, exch, isin) if raw_ticker else raw_ticker
    return LookupResult(
        name=item.get("name"),
        ticker=yahoo_ticker,
        isin=isin,
        currency=exchange_to_currency(exch) if exch else None,
    )


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
