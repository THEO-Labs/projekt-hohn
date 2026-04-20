import logging
import time

import httpx
from fastapi import APIRouter, Depends

from app.auth.deps import current_user
from app.auth.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fx", tags=["fx"])

FALLBACK_RATES: dict[str, float] = {
    "USD": 1.0,
    "EUR": 0.92,
    "GBP": 0.79,
    "CHF": 0.88,
    "JPY": 155.0,
    "KRW": 1390.0,
    "HKD": 7.8,
    "CNY": 7.2,
    "TWD": 32.0,
    "INR": 83.0,
    "CAD": 1.35,
    "AUD": 1.52,
    "NZD": 1.65,
    "SEK": 10.5,
    "NOK": 10.5,
    "DKK": 6.9,
    "PLN": 4.0,
    "SGD": 1.34,
    "BRL": 5.0,
    "MXN": 17.0,
    "ZAR": 18.5,
    "ILS": 3.7,
    "AED": 3.67,
    "SAR": 3.75,
    "THB": 35.0,
    "IDR": 15800.0,
    "PHP": 56.0,
    "MYR": 4.7,
    "TRY": 33.0,
    "CZK": 23.0,
    "HUF": 360.0,
    "RON": 4.6,
}

_TTL_SECONDS = 86400  # 24h
_cache: dict[str, object] = {"rates": None, "base": "USD", "date": None, "fetched_at": 0.0}


def _fetch_live_rates() -> dict | None:
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get("https://api.frankfurter.dev/v1/latest", params={"base": "USD"})
            resp.raise_for_status()
            payload = resp.json()
            rates = payload.get("rates")
            if not isinstance(rates, dict):
                return None
            rates = {k: float(v) for k, v in rates.items()}
            rates["USD"] = 1.0
            return {"rates": rates, "base": "USD", "date": payload.get("date")}
    except Exception as e:
        logger.warning("Failed to fetch live FX rates: %s", e)
        return None


@router.get("/rates")
def get_rates(_user: User = Depends(current_user)) -> dict:
    now = time.time()
    if _cache["rates"] is None or now - float(_cache["fetched_at"]) > _TTL_SECONDS:
        fresh = _fetch_live_rates()
        if fresh:
            _cache["rates"] = fresh["rates"]
            _cache["date"] = fresh["date"]
            _cache["fetched_at"] = now
            _cache["source"] = "frankfurter"
        elif _cache["rates"] is None:
            _cache["rates"] = dict(FALLBACK_RATES)
            _cache["date"] = None
            _cache["fetched_at"] = now
            _cache["source"] = "fallback"

    return {
        "base": _cache["base"],
        "date": _cache["date"],
        "rates": _cache["rates"],
        "source": _cache.get("source", "fallback"),
    }
