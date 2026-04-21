from decimal import Decimal

from app.calculations.engine import calculate_fy, calculate_stammdaten


def test_stammdaten_is_noop():
    assert calculate_stammdaten({"market_cap": Decimal("100000")}) == {}


def test_capex_from_opcf_and_fcf():
    result = calculate_fy(
        {"op_cash_flow": Decimal("500"), "fcf": Decimal("400"), "debt": Decimal("300"), "cash": Decimal("500")},
        None,
        {},
    )
    assert result["capex"] == Decimal("100")
    assert result["net_debt"] == Decimal("-200")


def test_fcf_yield_uses_fcf_input():
    result = calculate_fy(
        {"op_cash_flow": Decimal("5000"), "fcf": Decimal("4636")},
        None,
        {"market_cap": Decimal("101100")},
    )
    # 4636/101100*100 ≈ 4.587
    assert abs(result["fcf_yield"] - Decimal("4.587")) < Decimal("0.01")


def test_ni_growth_requires_previous():
    current = {"net_income": Decimal("120")}
    assert calculate_fy(current, None, {})["ni_growth"] is None
    assert calculate_fy(current, {"net_income": Decimal("100")}, {})["ni_growth"] == Decimal("20")


def test_sbc_per_fy():
    result = calculate_fy(
        {"sbc": Decimal("1000")},
        None,
        {"market_cap": Decimal("100000")},
    )
    assert result["sbc_yield"] == Decimal("1")


def test_net_debt_change_absolute_and_pct():
    current = {"debt": Decimal("1000"), "cash": Decimal("2000")}
    previous = {"debt": Decimal("2000"), "cash": Decimal("1500")}
    stammdaten = {"market_cap": Decimal("100000")}
    result = calculate_fy(current, previous, stammdaten)
    assert result["net_debt_change"] == Decimal("1500")
    assert result["net_debt_change_pct"] == Decimal("1.5")


def test_hohn_return_base_and_full():
    current = {
        "op_cash_flow": Decimal("5000"),
        "fcf": Decimal("4000"),
        "net_income": Decimal("120"),
        "debt": Decimal("1000"),
        "cash": Decimal("2000"),
        "sbc": Decimal("1000"),
    }
    previous = {
        "net_income": Decimal("100"),
        "debt": Decimal("2000"),
        "cash": Decimal("1500"),
    }
    stammdaten = {"market_cap": Decimal("100000")}
    result = calculate_fy(current, previous, stammdaten)
    # fcf_yield=4, ni_growth=20, sbc_yield=1, nd_change_pct=1.5
    assert result["hohn_return_base"] == Decimal("23")
    assert result["hohn_return"] == Decimal("24.5")


def test_servicenow_fy2025_reference_numbers():
    """ServiceNow FY2025 matching ChatGPT reference (ignoring minor timing)."""
    current = {
        "op_cash_flow": Decimal("5444"),
        "fcf": Decimal("4636"),
        "net_income": Decimal("1748"),
        "debt": Decimal("2291"),
        "cash": Decimal("10055"),
        "sbc": Decimal("1900"),
    }
    previous = {
        "net_income": Decimal("1425"),
        "debt": Decimal("2239"),
        "cash": Decimal("8262"),
    }
    stammdaten = {"market_cap": Decimal("101100")}
    result = calculate_fy(current, previous, stammdaten)
    assert result["capex"] == Decimal("808")
    # fcf_yield ≈ 4.59
    assert abs(result["fcf_yield"] - Decimal("4.587")) < Decimal("0.01")
    # ni_growth ≈ 22.67
    assert abs(result["ni_growth"] - Decimal("22.67")) < Decimal("0.1")
    # sbc_yield ≈ 1.88
    assert abs(result["sbc_yield"] - Decimal("1.879")) < Decimal("0.01")
    # hohn_return_base ≈ 4.59 + 22.67 - 1.88 = 25.38
    assert abs(result["hohn_return_base"] - Decimal("25.38")) < Decimal("0.05")


def test_zero_market_cap_safe():
    result = calculate_fy(
        {"op_cash_flow": Decimal("1000"), "fcf": Decimal("900"), "sbc": Decimal("10")},
        None,
        {"market_cap": Decimal("0")},
    )
    assert result["fcf_yield"] is None
    assert result["sbc_yield"] is None
    assert result["hohn_return_base"] is None
    assert result["hohn_return"] is None


def test_all_inputs_missing():
    assert calculate_fy({}, None, {})["hohn_return"] is None
