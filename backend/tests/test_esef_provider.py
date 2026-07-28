"""ESEF-Provider: Concept-Map fuer capex und operating_cash_flow.

Gemocktes Facts-JSON (kein Netz) — Muster analog zu test_edgar_provider,
nur dass hier die ESEF-internen Fetch-Schichten (GLEIF, Filings-Liste,
Filing-JSON) gepatcht werden.
"""
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.providers.esef import ESEFProvider

YEAR = 2024


@pytest.fixture
def provider():
    return ESEFProvider()


def _fact(concept: str, value, period: str | None = None, unit: str = "iso4217:EUR") -> dict:
    return {
        "dimensions": {
            "concept": concept,
            "period": period or f"{YEAR}-01-01T00:00:00/{YEAR + 1}-01-01T00:00:00",
            "unit": unit,
        },
        "value": str(value),
    }


def _fetch(provider, facts: dict, key: str):
    filing = {
        "attributes": {
            "period_end": f"{YEAR}-12-31",
            "json_url": "/test/filing.json",
            "viewer_url": "/test/viewer",
        }
    }
    with patch.object(provider, "_resolve_isin_to_lei", return_value="LEI123"), \
         patch.object(provider, "_list_filings", return_value=[filing]), \
         patch.object(provider, "_load_filing_facts", return_value={"facts": facts}):
        return provider.fetch("ADS", key, "FY", YEAR, isin="DE000A1EWWW0")


def test_supported_keys_include_capex_and_ocf():
    keys = ESEFProvider.supported_keys
    assert "capex" in keys
    assert "operating_cash_flow" in keys


def test_operating_cash_flow_from_standard_concept(provider):
    facts = {
        "f1": _fact("ifrs-full:CashFlowsFromUsedInOperatingActivities", 5000),
    }
    result = _fetch(provider, facts, "operating_cash_flow")
    assert result is not None
    assert result.value == Decimal("5000")
    assert result.currency == "EUR"


def test_operating_cash_flow_matches_firm_extension_suffix(provider):
    """Suffix-Match ohne Namespace deckt Firm-Extensions ab."""
    facts = {
        "f1": _fact("adidas:CashFlowsFromUsedInOperatingActivities", 4200),
    }
    result = _fetch(provider, facts, "operating_cash_flow")
    assert result is not None
    assert result.value == Decimal("4200")


def test_capex_sums_ppe_and_intangibles(provider):
    """CapEx = PP&E-Kaeufe + Intangible-Kaeufe (IFRS taggt getrennt)."""
    facts = {
        "f1": _fact("ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", 500),
        "f2": _fact("ifrs-full:PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities", 100),
    }
    result = _fetch(provider, facts, "capex")
    assert result is not None
    assert result.value == Decimal("600")
    assert "CapEx" in result.source_name


def test_capex_ppe_only_when_intangibles_untagged(provider):
    facts = {
        "f1": _fact("ifrs-full:PurchaseOfPropertyPlantAndEquipment", 500),
    }
    result = _fetch(provider, facts, "capex")
    assert result is not None
    assert result.value == Decimal("500")


def test_capex_combined_concept_no_double_count(provider):
    """Kombiniertes Concept (PP&E + Intangibles in einem Tag) hat Vorrang —
    Einzel-Concepts duerfen dann nicht zusaetzlich addiert werden."""
    facts = {
        "f1": _fact("adidas:PurchasesOfIntangibleAssetsPropertyPlantAndEquipmentInvestmentProperty", 800),
        "f2": _fact("ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", 500),
        "f3": _fact("ifrs-full:PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities", 100),
    }
    result = _fetch(provider, facts, "capex")
    assert result is not None
    assert result.value == Decimal("800")


def test_capex_negative_facts_stored_positive(provider):
    """Manche Filer taggen Cash-Outflows negativ — CapEx-Ableitung nutzt
    abs(), Ergebnis ist immer positiv."""
    facts = {
        "f1": _fact("ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", -500),
        "f2": _fact("ifrs-full:PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities", -100),
    }
    result = _fetch(provider, facts, "capex")
    assert result is not None
    assert result.value == Decimal("600")


def test_capex_returns_none_without_ppe(provider):
    facts = {
        "f1": _fact("ifrs-full:ProfitLoss", 123),
    }
    result = _fetch(provider, facts, "capex")
    assert result is None


# --- fcf: MUSS dieselbe CapEx-Ableitung nutzen wie der capex-Key ----------

_OCF = "ifrs-full:CashFlowsFromUsedInOperatingActivities"
_PPE = "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"
_INTANG = "ifrs-full:PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities"
_COMBINED = "adidas:PurchasesOfIntangibleAssetsPropertyPlantAndEquipmentInvestmentProperty"


def test_fcf_subtracts_ppe_plus_intangibles(provider):
    """fcf = ocf - abs(capex) mit CapEx = PP&E + Intangibles — nicht nur
    PP&E, sonst weicht fcf strukturell vom capex-Key ab."""
    facts = {
        "f1": _fact(_OCF, 5000),
        "f2": _fact(_PPE, -500),
        "f3": _fact(_INTANG, -100),
    }
    result = _fetch(provider, facts, "fcf")
    assert result is not None
    assert result.value == Decimal("4400")


def test_fcf_uses_combined_capex_concept_first(provider):
    """Combined-Concept hat auch in der fcf-Ableitung Vorrang — die
    Einzel-Concepts duerfen nicht zusaetzlich abgezogen werden."""
    facts = {
        "f1": _fact(_OCF, 5000),
        "f2": _fact(_COMBINED, 800),
        "f3": _fact(_PPE, 500),
        "f4": _fact(_INTANG, 100),
    }
    result = _fetch(provider, facts, "fcf")
    assert result is not None
    assert result.value == Decimal("4200")


def test_fcf_structurally_consistent_with_capex_key(provider):
    """Identitaet fcf = ocf - capex haelt fuer identische Facts."""
    facts = {
        "f1": _fact(_OCF, 5000),
        "f2": _fact(_PPE, -500),
        "f3": _fact(_INTANG, -100),
    }
    fcf = _fetch(provider, facts, "fcf")
    capex = _fetch(provider, facts, "capex")
    ocf = _fetch(provider, facts, "operating_cash_flow")
    assert fcf.value == ocf.value - abs(capex.value)


def test_fcf_none_when_capex_underivable(provider):
    facts = {
        "f1": _fact(_OCF, 5000),
    }
    result = _fetch(provider, facts, "fcf")
    assert result is None
