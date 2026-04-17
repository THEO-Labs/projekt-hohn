from decimal import Decimal

from app.calculations.engine import calculate_all


def test_net_debt():
    result = calculate_all({"debt": Decimal("500"), "cash": Decimal("200")})
    assert result["net_debt"] == Decimal("300")


def test_eps_growth():
    result = calculate_all({"eps_ttm": Decimal("5"), "eps_forward": Decimal("6")})
    assert result["eps_growth"] == Decimal("20")


def test_hohn_rendite_basic_1():
    result = calculate_all({
        "dividend_return": Decimal("2"),
        "buybacks": Decimal("-50000000"),
        "market_cap": Decimal("1000000000"),
        "eps_ttm": Decimal("5"),
        "eps_forward": Decimal("6"),
    })
    assert result["hohn_rendite_basic_1"] == Decimal("27")


def test_fcf_yield():
    result = calculate_all({
        "free_cash_flow": Decimal("100000000"),
        "market_cap": Decimal("1000000000"),
    })
    assert result["fcf_yield"] == Decimal("10")


def test_upside_potential():
    result = calculate_all({
        "stock_price": Decimal("100"),
        "analysts_target": Decimal("120"),
    })
    assert result["upside_potential"] == Decimal("20")


def test_missing_values_return_none():
    result = calculate_all({})
    assert result["net_debt"] is None
    assert result["hohn_rendite_basic_1"] is None
    assert result["hohn_rendite_adjusted"] is None


def test_net_debt_missing_cash():
    result = calculate_all({"debt": Decimal("500")})
    assert result["net_debt"] is None


def test_buyback_return():
    result = calculate_all({
        "buybacks": Decimal("100000000"),
        "market_cap": Decimal("2000000000"),
    })
    assert result["buyback_return"] == Decimal("5")


def test_hohn_rendite_basic_2():
    result = calculate_all({
        "free_cash_flow": Decimal("100000000"),
        "market_cap": Decimal("1000000000"),
        "eps_ttm": Decimal("5"),
        "eps_forward": Decimal("6"),
    })
    assert result["fcf_yield"] == Decimal("10")
    assert result["eps_growth"] == Decimal("20")
    assert result["hohn_rendite_basic_2"] == Decimal("30")


def test_pe_target_analysts():
    result = calculate_all({
        "analysts_target": Decimal("150"),
        "eps_forward": Decimal("10"),
    })
    assert result["pe_target_analysts"] == Decimal("15")


def test_risk_factor_average():
    result = calculate_all({
        "risk_business_model": Decimal("0.9"),
        "risk_regulatory": Decimal("0.8"),
        "risk_macro": Decimal("1.0"),
    })
    expected = (Decimal("0.9") + Decimal("0.8") + Decimal("1.0")) / Decimal("3")
    assert result["risk_factor"] == expected


def test_hohn_rendite_adjusted_with_factor():
    result = calculate_all({
        "dividend_return": Decimal("3"),
        "eps_ttm": Decimal("10"),
        "eps_forward": Decimal("11"),
        "buybacks": Decimal("0"),
        "market_cap": Decimal("1000000000"),
        "mgmt_participation": Decimal("0.9"),
    })
    assert result["hohn_rendite_adjusted"] is not None
    assert result["total_adjustment_factor"] == Decimal("0.9")


def test_hohn_rendite_adjusted_without_factor():
    result = calculate_all({
        "dividend_return": Decimal("3"),
    })
    assert result["hohn_rendite_adjusted"] == Decimal("3")
    assert result["total_adjustment_factor"] is None
