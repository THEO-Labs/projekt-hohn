from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.providers.edgar import EdgarProvider


@pytest.fixture
def provider():
    p = EdgarProvider()
    p._ticker_to_cik = {"AAPL": "0000320193"}
    return p


def _facts_with(concepts: dict[str, dict], dei: dict | None = None) -> dict:
    return {"facts": {"us-gaap": concepts, "dei": dei or {}}}


def _entry(end: str, val: float, fy: int, fp: str = "FY", form: str = "10-K", accn: str = "0000320193-24-000123") -> dict:
    return {"end": end, "val": val, "fy": fy, "fp": fp, "form": form, "accn": accn}


def test_unsupported_period_type_returns_none(provider):
    assert provider.fetch("AAPL", "net_income", "SNAPSHOT", None) is None


def test_unknown_ticker_returns_none(provider):
    assert provider.fetch("UNKNOWN", "net_income", "FY", 2024) is None


def test_net_income_from_10k(provider):
    facts = _facts_with({
        "NetIncomeLoss": {
            "units": {"USD": [_entry("2024-09-28", 93736000000, 2024)]}
        }
    })
    with patch.object(provider, "_get_facts", return_value=facts):
        result = provider.fetch("AAPL", "net_income", "FY", 2024)
    assert result is not None
    assert result.value == Decimal("93736000000")
    assert result.currency == "USD"
    assert "EDGAR" in result.source_name


def test_picks_first_available_concept_alias(provider):
    facts = _facts_with({
        "ProfitLoss": {"units": {"USD": [_entry("2024-09-28", 50000, 2024)]}},
    })
    with patch.object(provider, "_get_facts", return_value=facts):
        result = provider.fetch("AAPL", "net_income", "FY", 2024)
    assert result is not None
    assert result.value == Decimal("50000")


def test_no_data_for_year_returns_none(provider):
    facts = _facts_with({
        "NetIncomeLoss": {"units": {"USD": [_entry("2023-09-30", 100, 2023)]}}
    })
    with patch.object(provider, "_get_facts", return_value=facts):
        result = provider.fetch("AAPL", "net_income", "FY", 2024)
    assert result is None


def test_fcf_computed_from_ocf_minus_capex(provider):
    facts = _facts_with({
        "NetCashProvidedByUsedInOperatingActivities": {
            "units": {"USD": [_entry("2024-09-28", 118254000000, 2024)]}
        },
        "PaymentsToAcquirePropertyPlantAndEquipment": {
            "units": {"USD": [_entry("2024-09-28", 9447000000, 2024)]}
        },
    })
    with patch.object(provider, "_get_facts", return_value=facts):
        result = provider.fetch("AAPL", "fcf", "FY", 2024)
    assert result is not None
    assert result.value == Decimal("108807000000")


def test_fcf_handles_negative_capex(provider):
    """CapEx is sometimes filed as negative cash outflow — we use abs()."""
    facts = _facts_with({
        "NetCashProvidedByUsedInOperatingActivities": {
            "units": {"USD": [_entry("2024-09-28", 100, 2024)]}
        },
        "PaymentsToAcquirePropertyPlantAndEquipment": {
            "units": {"USD": [_entry("2024-09-28", -10, 2024)]}
        },
    })
    with patch.object(provider, "_get_facts", return_value=facts):
        result = provider.fetch("AAPL", "fcf", "FY", 2024)
    assert result.value == Decimal("90")


def test_shares_outstanding_from_dei(provider):
    facts = _facts_with(
        concepts={},
        dei={
            "EntityCommonStockSharesOutstanding": {
                "units": {"shares": [
                    {"end": "2024-09-28", "val": 15115823000, "form": "10-K", "accn": "0000320193-24-000123"},
                ]}
            }
        },
    )
    with patch.object(provider, "_get_facts", return_value=facts):
        result = provider.fetch("AAPL", "shares_outstanding", "FY", 2024)
    assert result is not None
    assert result.value == Decimal("15115823000")


def test_returns_none_when_facts_unavailable(provider):
    with patch.object(provider, "_get_facts", return_value=None):
        result = provider.fetch("AAPL", "net_income", "FY", 2024)
    assert result is None


def test_provider_name():
    assert EdgarProvider.name == "SEC EDGAR"


def test_supported_keys_includes_fcf_and_shares():
    keys = EdgarProvider.supported_keys
    assert "fcf" in keys
    assert "shares_outstanding" in keys
    assert "net_income" in keys
    assert "stock_price" not in keys  # not in XBRL


def test_registry_lists_edgar_first_for_us_filer_keys():
    from app.providers.registry import get_providers
    providers = get_providers("net_income")
    assert len(providers) >= 1
    assert providers[0].name == "SEC EDGAR"
