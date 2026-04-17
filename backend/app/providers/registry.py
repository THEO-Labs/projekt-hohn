from app.providers.yahoo import YahooFinanceProvider

_yahoo = YahooFinanceProvider()

PROVIDER_REGISTRY: dict[str, list] = {}

for _key in _yahoo.supported_keys:
    PROVIDER_REGISTRY.setdefault(_key, []).append(_yahoo)


def get_providers(key: str) -> list:
    return PROVIDER_REGISTRY.get(key, [])
