from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass
class ProviderResult:
    value: Decimal | str | None
    source_name: str
    source_link: str | None = None
    currency: str | None = None


class ValueProvider(Protocol):
    name: str
    supported_keys: set[str]

    def fetch(
        self, ticker: str, key: str, period_type: str, period_year: int | None
    ) -> ProviderResult | None: ...
