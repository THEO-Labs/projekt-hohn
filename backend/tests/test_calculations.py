from decimal import Decimal

from app.calculations.engine import calculate_fy, calculate_stammdaten


def test_stammdaten_all_calcs():
    result = calculate_stammdaten({
        "stock_price": Decimal("100"),
        "exchange_rate": Decimal("0.92"),
        "shares_outstanding": Decimal("1000"),
        "market_cap": Decimal("100000"),
        "debt": Decimal("30000"),
        "cash": Decimal("10000"),
    })
    assert result["stock_price_eur"] == Decimal("92.00")
    assert result["market_cap_calc"] == Decimal("100000")
    assert result["net_debt"] == Decimal("20000")
    assert result["ev"] == Decimal("120000")


def test_stammdaten_missing_inputs_yield_none():
    result = calculate_stammdaten({})
    assert result["stock_price_eur"] is None
    assert result["market_cap_calc"] is None
    assert result["net_debt"] is None
    assert result["ev"] is None


def test_fcf_from_op_cf_and_capex():
    result = calculate_fy(
        {"op_cash_flow": Decimal("500"), "capex": Decimal("100")},
        None,
        {},
    )
    assert result["fcf"] == Decimal("400")


def test_ni_growth_requires_previous():
    current = {"net_income": Decimal("120")}
    assert calculate_fy(current, None, {})["ni_growth"] is None
    assert calculate_fy(current, {"net_income": Decimal("100")}, {})["ni_growth"] == Decimal("20")


def test_fcf_change_uses_reconstructed_previous_fcf():
    current = {"op_cash_flow": Decimal("200"), "capex": Decimal("50")}  # fcf=150
    previous = {"op_cash_flow": Decimal("150"), "capex": Decimal("50")}  # fcf=100
    result = calculate_fy(current, previous, {})
    assert result["fcf_change"] == Decimal("50")


def test_ratios_with_full_stammdaten():
    current = {
        "op_cash_flow": Decimal("5000"),
        "capex": Decimal("1000"),
        "net_income": Decimal("3000"),
        "eps_adj": Decimal("5"),
        "dividends": Decimal("500"),
    }
    previous = {
        "net_income": Decimal("2500"),
        "eps_adj": Decimal("4"),
    }
    stammdaten = {
        "stock_price": Decimal("100"),
        "market_cap": Decimal("100000"),
        "ev": Decimal("120000"),
        "sbc": Decimal("1000"),
    }
    result = calculate_fy(current, previous, stammdaten)

    assert result["fcf"] == Decimal("4000")
    assert result["ni_growth"] == Decimal("20")
    assert result["ev_op_cf"] == Decimal("24")
    assert result["pe_ltm_adj"] == Decimal("25")
    assert result["pe_target"] == Decimal("20")
    assert result["fcf_yield"] == Decimal("4")
    assert result["dividend_yield"] == Decimal("0.5")
    assert result["peg"] == Decimal("1")
    # hohn_return = fcf_yield(4) + ni_growth(20) - sbc/mcap*100(1) = 23
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
    assert result["hohn_return"] == Decimal("24")  # 4 + 20


def test_division_by_zero_safe():
    stammdaten = {"stock_price": Decimal("100"), "market_cap": Decimal("0"), "ev": Decimal("0")}
    current = {"eps_adj": Decimal("0"), "op_cash_flow": Decimal("0")}
    result = calculate_fy(current, None, stammdaten)
    assert result["pe_target"] is None
    assert result["ev_op_cf"] is None
    assert result["fcf_yield"] is None
