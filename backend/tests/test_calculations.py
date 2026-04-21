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


def test_hohn_return_uses_manual_ni_growth_fallback():
    """If previous-year net_income is missing, the auto ni_growth calc yields None.
    But if ni_growth is already stored (e.g. user set it manually), hohn_return
    must still compute using that stored value."""
    current = {
        "op_cash_flow": Decimal("5444000000"),
        "capex": Decimal("911000000"),
        "net_income": Decimal("1748000000"),
        "ni_growth": Decimal("35.3"),
    }
    stammdaten = {"market_cap": Decimal("104307122176"), "sbc": Decimal("1765000000")}
    result = calculate_fy(current, None, stammdaten)
    assert result["hohn_return"] is not None
    assert abs(result["hohn_return"] - Decimal("37.95")) < Decimal("0.01")


def test_zero_market_cap_safe():
    result = calculate_fy(
        {"op_cash_flow": Decimal("1000"), "capex": Decimal("100")},
        None,
        {"market_cap": Decimal("0"), "sbc": Decimal("10")},
    )
    assert result["fcf_yield"] is None
    assert result["sbc_yield"] is None
    assert result["hohn_return"] is None
