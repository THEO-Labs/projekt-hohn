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


def test_net_debt_from_debt_and_cash():
    result = calculate_fy(
        {"debt": Decimal("300"), "cash": Decimal("500")},
        None,
        {},
    )
    assert result["net_debt"] == Decimal("-200")


def test_ni_growth_requires_previous():
    assert calculate_fy({"net_income": Decimal("120")}, None, {})["ni_growth"] is None
    assert calculate_fy(
        {"net_income": Decimal("120")},
        {"net_income": Decimal("100")},
        {},
    )["ni_growth"] == Decimal("20")


def test_net_debt_change_debt_reduction_positive():
    current = {"debt": Decimal("1000"), "cash": Decimal("2000")}  # net_debt = -1000
    previous = {"debt": Decimal("2000"), "cash": Decimal("1500")}  # net_debt = 500
    stammdaten = {"market_cap": Decimal("100000")}
    result = calculate_fy(current, previous, stammdaten)
    # (500 - (-1000)) / 100000 * 100 = 1.5
    assert result["net_debt_change"] == Decimal("1.5")


def test_net_debt_change_debt_increase_negative():
    current = {"debt": Decimal("3000"), "cash": Decimal("500")}  # net_debt = 2500
    previous = {"debt": Decimal("1000"), "cash": Decimal("500")}  # net_debt = 500
    stammdaten = {"market_cap": Decimal("100000")}
    result = calculate_fy(current, previous, stammdaten)
    # (500 - 2500) / 100000 * 100 = -2.0
    assert result["net_debt_change"] == Decimal("-2")


def test_hohn_return_four_terms():
    current = {
        "op_cash_flow": Decimal("5000"),
        "capex": Decimal("1000"),
        "net_income": Decimal("120"),
        "debt": Decimal("1000"),
        "cash": Decimal("2000"),
    }
    previous = {
        "net_income": Decimal("100"),
        "debt": Decimal("2000"),
        "cash": Decimal("1500"),
    }
    stammdaten = {"market_cap": Decimal("100000"), "sbc": Decimal("1000")}
    result = calculate_fy(current, previous, stammdaten)
    # fcf_yield = 4000/100000*100 = 4
    # ni_growth = 20
    # sbc_yield = 1
    # net_debt_change = (500 - (-1000))/100000*100 = 1.5
    # hohn_return = 4 + 20 - 1 + 1.5 = 24.5
    assert result["hohn_return"] == Decimal("24.5")


def test_hohn_return_uses_stored_ni_growth_fallback():
    current = {
        "op_cash_flow": Decimal("5444"),
        "capex": Decimal("911"),
        "net_income": Decimal("1748"),
        "ni_growth": Decimal("20.5"),
    }
    stammdaten = {"market_cap": Decimal("101100"), "sbc": Decimal("1900")}
    result = calculate_fy(current, None, stammdaten)
    # fcf_yield = 4533/101100*100 ~ 4.483
    # ni_growth = 20.5 (stored fallback)
    # sbc_yield = 1900/101100*100 ~ 1.879
    # no previous → net_debt_change None → hohn_return = 4.483 + 20.5 - 1.879 ~ 23.1
    assert result["hohn_return"] is not None
    assert abs(result["hohn_return"] - Decimal("23.1")) < Decimal("0.1")


def test_hohn_return_none_when_core_inputs_missing():
    assert calculate_fy({}, None, {})["hohn_return"] is None


def test_zero_market_cap_safe():
    result = calculate_fy(
        {"op_cash_flow": Decimal("1000"), "capex": Decimal("100")},
        None,
        {"market_cap": Decimal("0"), "sbc": Decimal("10")},
    )
    assert result["fcf_yield"] is None
    assert result["sbc_yield"] is None
    assert result["net_debt_change"] is None
    assert result["hohn_return"] is None
