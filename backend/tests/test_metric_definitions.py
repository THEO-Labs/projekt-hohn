from app.values.metric_definitions import METRIC_DEFINITIONS, ADJUSTED_KEYS, PERIOD_DOMAIN_ALLOWLIST
from app.values.always_current import ALWAYS_CURRENT_KEYS

def test_definitions_cover_key_fundamentals():
    for k in ("operating_cash_flow", "capex", "net_income", "revenue",
              "ebitda", "net_debt", "sbc", "buyback_volume", "dividends",
              "eps_diluted", "fcf"):
        assert k in METRIC_DEFINITIONS and METRIC_DEFINITIONS[k].strip()

def test_no_stammdaten_in_definitions():
    assert not (set(METRIC_DEFINITIONS) & ALWAYS_CURRENT_KEYS)

def test_adjusted_keys_subset():
    assert ADJUSTED_KEYS <= set(METRIC_DEFINITIONS)
    assert {"net_income", "ebitda", "fcf"} <= ADJUSTED_KEYS

def test_period_allowlist_is_official():
    assert "sec.gov" in PERIOD_DOMAIN_ALLOWLIST
