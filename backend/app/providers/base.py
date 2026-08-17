from dataclasses import dataclass
from decimal import Decimal


@dataclass
class ProviderResult:
    value: Decimal | str | None
    source_name: str
    source_link: str | None = None
    currency: str | None = None
    extras: dict | None = None
