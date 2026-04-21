from decimal import Decimal

from app.calculations.engine import calculate_fy, calculate_stammdaten


def test_stammdaten_is_noop():
    assert calculate_stammdaten({"market_cap": Decimal("100000"), "sbc": Decimal("1000")}) == {}


def test_fcf_from_op_cf_and_capex():
    result = calculate_fy(
        {"op_cash_flow": Decimal("500"), "capex": Decimal("100")},
        None,
        {},
    )
    assert result["fcf"] == Decimal("400")


def test_ni_growth_requires_previous():
    assert calculate_fy({"net_income": Decimal("120")}, None, {})["ni_growth"] is None
    assert calculate_fy(
        {"net_income": Decimal("120")},
        {"net_income": Decimal("100")},
        {},
    )["ni_growth"] == Decimal("20")


def test_fcf_yield_uses_stammdaten_market_cap():
    result = calculate_fy(
        {"op_cash_flow": Decimal("5000"), "capex": Decimal("1000")},
        None,
        {"market_cap": Decimal("100000")},
    )
    assert result["fcf_yield"] == Decimal("4")


def test_sbc_yield_from_stammdaten():
    result = calculate_fy(
        {},
        None,
        {"market_cap": Decimal("100000"), "sbc": Decimal("1000")},
    )
    assert result["sbc_yield"] == Decimal("1")


def test_hohn_return_full_formula():
    current = {
        "op_cash_flow": Decimal("5000"),
        "capex": Decimal("1000"),
        "net_income": Decimal("120"),
    }
    previous = {"net_income": Decimal("100")}
    stammdaten = {"market_cap": Decimal("100000"), "sbc": Decimal("1000")}
    result = calculate_fy(current, previous, stammdaten)
    # fcf_yield=4, ni_growth=20, sbc_yield=1  =>  hohn_return=23
    assert result["hohn_return"] == Decimal("23")


def test_hohn_return_without_sbc_still_computes():
    current = {
        "op_cash_flow": Decimal("5000"),
        "capex": Decimal("1000"),
        "net_income": Decimal("120"),
    }
    previous = {"net_income": Decimal("100")}
    stammdaten = {"market_cap": Decimal("100000")}
    result = calculate_fy(current, previous, stammdaten)
    assert result["hohn_return"] == Decimal("24")


def test_hohn_return_none_when_inputs_missing():
    assert calculate_fy({}, None, {})["hohn_return"] is None


def test_zero_market_cap_safe():
    result = calculate_fy(
        {"op_cash_flow": Decimal("1000"), "capex": Decimal("100")},
        None,
        {"market_cap": Decimal("0"), "sbc": Decimal("10")},
    )
    assert result["fcf_yield"] is None
    assert result["sbc_yield"] is None
    assert result["hohn_return"] is None
