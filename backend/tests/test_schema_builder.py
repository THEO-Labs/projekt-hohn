from app.values.schema_builder import build_period_schema, fundamental_keys
from app.values.always_current import ALWAYS_CURRENT_KEYS


def test_fundamental_keys_exclude_stammdaten():
    keys = fundamental_keys()
    assert not (set(keys) & ALWAYS_CURRENT_KEYS)
    assert "operating_cash_flow" in keys


def test_schema_has_value_and_adjusted_fields():
    schema = build_period_schema()["json_schema"]["schema"]
    props = schema["properties"]
    assert "net_income" in props
    assert props["net_income"]["description"]
    assert "net_income_adjusted" in props
    assert "stock_price" not in props


def test_schema_no_url_fields():
    schema = build_period_schema()["json_schema"]["schema"]
    for name in schema["properties"]:
        assert "url" not in name and "source" not in name
