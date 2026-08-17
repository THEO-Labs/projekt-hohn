from app.providers.edgar import EdgarProvider
from app.providers.yahoo import YahooFinanceProvider

_edgar = EdgarProvider()
_yahoo = YahooFinanceProvider()

PROVIDER_REGISTRY: dict[str, list] = {}

# Order per key: EDGAR (US authoritative) > Yahoo (market fallback).
# Provider.fetch() returns None bei nicht-passendem Filer, also ist die
# Reihenfolge sicher: jeder Provider wird der Reihe nach probiert.
for _key in _edgar.supported_keys:
    PROVIDER_REGISTRY.setdefault(_key, []).append(_edgar)

for _key in _yahoo.supported_keys:
    PROVIDER_REGISTRY.setdefault(_key, []).append(_yahoo)


def get_providers(key: str) -> list:
    return PROVIDER_REGISTRY.get(key, [])
