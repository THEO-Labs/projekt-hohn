from decimal import Decimal

from app.calculations.engine import calculate_all


def test_service_now_example():
    """Reproduces the customer's reference sheet (ServiceNow FY2026):
    Hohn Return = FCF Yield + NI Growth - SBC/Mcap = 5.58 + 20.55 - 1.90 = 24.23%"""
    current = {
        "sales": Decimal("15530"),
        "fcf_margin_non_gaap": Decimal("36"),
        "sbc": Decimal("1900"),
        "market_cap": Decimal("100155"),
    }
    previous = {"sales": Decimal("12883")}

    result = calculate_all(current, previous)

    assert abs(result["fcf"] - Decimal("5590.80")) < Decimal("0.01")
    assert abs(result["fcf_yield"] - Decimal("5.58")) < Decimal("0.01")
    assert abs(result["ni_growth"] - Decimal("20.55")) < Decimal("0.01")
    assert abs(result["sbc_yield"] - Decimal("1.90")) < Decimal("0.01")
    assert abs(result["hohn_return"] - Decimal("24.23")) < Decimal("0.01")


def test_missing_inputs_return_none():
    result = calculate_all({})
    assert result["fcf"] is None
    assert result["fcf_yield"] is None
    assert result["ni_growth"] is None
    assert result["sbc_yield"] is None
    assert result["hohn_return"] is None


def test_ni_growth_requires_previous_sales():
    result = calculate_all({"sales": Decimal("100")})
    assert result["ni_growth"] is None

    result2 = calculate_all({"sales": Decimal("100")}, {"sales": Decimal("80")})
    assert result2["ni_growth"] == Decimal("25")


def test_fcf_requires_sales_and_margin():
    assert calculate_all({"sales": Decimal("100")})["fcf"] is None
    assert calculate_all({"fcf_margin_non_gaap": Decimal("20")})["fcf"] is None
    assert calculate_all({
        "sales": Decimal("100"),
        "fcf_margin_non_gaap": Decimal("20"),
    })["fcf"] == Decimal("20")


def test_hohn_return_requires_all_three_components():
    result = calculate_all({
        "sales": Decimal("100"),
        "fcf_margin_non_gaap": Decimal("10"),
        "market_cap": Decimal("1000"),
    })
    assert result["fcf_yield"] == Decimal("1")
    assert result["hohn_return"] is None


def test_division_by_zero_market_cap():
    result = calculate_all({
        "sales": Decimal("100"),
        "fcf_margin_non_gaap": Decimal("10"),
        "market_cap": Decimal("0"),
        "sbc": Decimal("5"),
    })
    assert result["fcf_yield"] is None
    assert result["sbc_yield"] is None
