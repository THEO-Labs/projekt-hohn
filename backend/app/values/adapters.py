"""Adapter zwischen den bestehenden Providern (EDGAR/Yahoo) und den
Orchestrator-Interfaces. EDGAR wird DIREKT genutzt (nicht die Provider-Chain
get_providers) — die Chain wuerde bei Fundamentals verbotenerweise auf den
Marktdaten-Feed zurueckfallen. Yahoo liefert nur die Stammdaten.
"""
import logging
from decimal import Decimal

from app.providers.edgar import EdgarProvider
from app.providers.yahoo import YahooFinanceProvider
from app.values.always_current import ALWAYS_CURRENT_KEYS
from app.values.orchestrator import AnchorValue
from app.values.schema_builder import fundamental_keys

logger = logging.getLogger(__name__)


def _as_decimal(v):
    if v is None:
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


def yahoo_stammdaten(company) -> dict:
    """Live-Stammdaten (stock_price, market_cap, shares_outstanding) aus dem
    Marktdaten-Feed. Rueckgabe: {key: (Decimal, currency)}."""
    out: dict = {}
    provider = YahooFinanceProvider()
    ticker = company.ticker
    for key in ALWAYS_CURRENT_KEYS:
        try:
            res = provider.fetch(ticker, key)
        except Exception as e:
            logger.warning("yahoo stammdaten %s/%s failed: %s", ticker, key, e)
            res = None
        val = _as_decimal(res.value) if res is not None else None
        if val is not None:
            out[key] = (val, res.currency)
    return out


def edgar_anchor(company, years) -> dict:
    """Exakte EDGAR-XBRL-Werte je (key, year). Nur EDGAR (kein Yahoo-Fallback
    fuer Fundamentals). Rueckgabe: {(key, year): AnchorValue}."""
    out: dict = {}
    provider = EdgarProvider()
    ticker = company.ticker
    fym = getattr(company, "fiscal_year_end_month", None)
    fyd = getattr(company, "fiscal_year_end_day", None)
    for key in fundamental_keys():
        if key not in provider.supported_keys:
            continue
        for year in years:
            try:
                res = provider.fetch(ticker, key, "FY", year, fym, fyd)
            except Exception as e:
                logger.warning("edgar %s/%s FY%s failed: %s", ticker, key, year, e)
                res = None
            val = _as_decimal(res.value) if res is not None else None
            if val is not None:
                out[(key, year)] = AnchorValue(val, res.source_name, res.source_link, res.currency)
    return out
