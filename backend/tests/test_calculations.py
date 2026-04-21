from decimal import Decimal

from app.calculations.engine import calculate_fy, calculate_stammdaten


def test_stammdaten_is_noop():
    assert calculate_stammdaten({"market_cap": Decimal("100000")}) == {}


def test_fcf_and_net_debt_base():
    result = calculate_fy(
        {"op_cash_flow": Decimal("500"), "capex": Decimal("100"), "debt": Decimal("300"), "cash": Decimal("500")},
        None,
        {},
    )
    assert result["fcf"] == Decimal("400")
    assert result["net_debt"] == Decimal("-200")


def test_ni_growth_requires_previous():
    current = {"net_income": Decimal("120")}
    assert calculate_fy(current, None, {})["ni_growth"] is None
    assert calculate_fy(current, {"net_income": Decimal("100")}, {})["ni_growth"] == Decimal("20")


def test_sbc_per_fy():
    """SBC now lives in per-FY inputs, so sbc_yield uses current-year sbc."""
    result = calculate_fy(
        {"sbc": Decimal("1000")},
        None,
        {"market_cap": Decimal("100000")},
    )
    assert result["sbc_yield"] == Decimal("1")


def test_net_debt_change_absolute_and_pct():
    current = {"debt": Decimal("1000"), "cash": Decimal("2000")}  # net_debt = -1000
    previous = {"debt": Decimal("2000"), "cash": Decimal("1500")}  # net_debt = 500
    stammdaten = {"market_cap": Decimal("100000")}
    result = calculate_fy(current, previous, stammdaten)
    # net_debt_change = prev - curr = 500 - (-1000) = 1500 (debt reduction positive)
    assert result["net_debt_change"] == Decimal("1500")
    # 1500 / 100000 * 100 = 1.5 %
    assert result["net_debt_change_pct"] == Decimal("1.5")


def test_hohn_return_base_and_full():
    current = {
        "op_cash_flow": Decimal("5000"),
        "capex": Decimal("1000"),
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
    # fcf_yield=4, ni_growth=20, sbc_yield=1 => base = 23
    assert result["hohn_return_base"] == Decimal("23")
    # net_debt_change_pct = 1.5 => full = 24.5
    assert result["hohn_return"] == Decimal("24.5")


def test_hohn_return_without_previous_still_computes_base():
    current = {
        "op_cash_flow": Decimal("5000"),
        "capex": Decimal("1000"),
        "net_income": Decimal("120"),
        "ni_growth": Decimal("20"),
        "sbc": Decimal("1000"),
    }
    stammdaten = {"market_cap": Decimal("100000")}
    result = calculate_fy(current, None, stammdaten)
    assert result["hohn_return_base"] == Decimal("23")
    # no previous -> no ND change, full = base
    assert result["hohn_return"] == Decimal("23")


def test_zero_market_cap_safe():
    result = calculate_fy(
        {"op_cash_flow": Decimal("1000"), "capex": Decimal("100"), "sbc": Decimal("10")},
        None,
        {"market_cap": Decimal("0")},
    )
    assert result["fcf_yield"] is None
    assert result["sbc_yield"] is None
    assert result["hohn_return_base"] is None
    assert result["hohn_return"] is None


def test_all_inputs_missing():
    assert calculate_fy({}, None, {})["hohn_return"] is None
