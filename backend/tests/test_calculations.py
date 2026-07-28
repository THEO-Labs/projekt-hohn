from decimal import Decimal

from app.calculations.engine import calculate_fy, calculate_stammdaten


def test_stammdaten_market_cap_calc():
    result = calculate_stammdaten({
        "stock_price": Decimal("100"),
        "shares_outstanding": Decimal("1036"),
    })
    assert result["market_cap_calc"] == Decimal("103600")


def test_net_buyback():
    result, _adj = calculate_fy(
        {"buyback_volume": Decimal("1840"), "sbc": Decimal("1955")},
        None,
        {"market_cap": Decimal("101100")},
    )
    assert result["net_buyback"] == Decimal("-115")


def test_sbc_yield_and_net_buyback_yield():
    result, _adj = calculate_fy(
        {"sbc": Decimal("1900"), "buyback_volume": Decimal("3000")},
        None,
        {"market_cap": Decimal("101100")},
    )
    assert abs(result["sbc_yield"] - Decimal("1.879")) < Decimal("0.01")
    # net_buyback = 3000 - 1900 = 1100; 1100 / 101100 * 100 ≈ 1.088
    assert abs(result["net_buyback_yield"] - Decimal("1.088")) < Decimal("0.01")


def test_fcf_yield():
    result, _adj = calculate_fy(
        {"fcf": Decimal("4636")},
        None,
        {"market_cap": Decimal("101100")},
    )
    assert abs(result["fcf_yield"] - Decimal("4.587")) < Decimal("0.01")


def test_ni_growth():
    result, _adj = calculate_fy(
        {"net_income": Decimal("1748")},
        {"net_income": Decimal("1425")},
        {},
    )
    assert abs(result["ni_growth"] - Decimal("22.67")) < Decimal("0.1")


def test_ni_growth_negative_prev_correct_sign_and_capped():
    """Turnaround: NI war negativ, jetzt positiv → Growth POSITIV, aber bei
    ±100% gekappt — sonst dominieren Turnarounds (Bayer +178%-H-Return-Klasse)
    jedes Ranking."""
    result, _adj = calculate_fy(
        {"net_income": Decimal("764")},
        {"net_income": Decimal("-75")},
        {},
    )
    assert result["ni_growth"] == Decimal("100")


def test_ni_growth_capped_negative():
    result, _adj = calculate_fy(
        {"net_income": Decimal("-500")},
        {"net_income": Decimal("100")},
        {},
    )
    assert result["ni_growth"] == Decimal("-100")


def test_ni_growth_uncapped_inside_range():
    result, _adj = calculate_fy(
        {"net_income": Decimal("150")},
        {"net_income": Decimal("100")},
        {},
    )
    assert result["ni_growth"] == Decimal("50")


def test_financial_excludes_net_debt_change_from_hohn():
    """Banken/Versicherer: net_debt_change_pct fliegt aus der H-Return raus
    (DBK -2036%-Klasse) — der Wert selbst bleibt aber gespeichert."""
    result, _adj = calculate_fy(
        {"net_income": Decimal("150"), "net_debt": Decimal("50000"), "dividends": Decimal("20")},
        {"net_income": Decimal("100"), "net_debt": Decimal("10000")},
        {"market_cap": Decimal("1000")},
        exclude_net_debt_change=True,
    )
    assert result["net_debt_change_pct"] is not None
    # detailed = div_yield 2 + ni_growth 50 (OHNE nd-Komponente -4000)
    assert result["hohn_return_detailed"] == Decimal("52")


def test_is_financial_ticker_set():
    from app.calculations.engine import is_financial
    assert is_financial("DBK.DE") and is_financial("ALV.DE")
    assert not is_financial("SAP.DE")


def test_dividend_yield():
    result, _adj = calculate_fy(
        {"dividends": Decimal("200")},
        None,
        {"market_cap": Decimal("100000")},
    )
    assert result["dividend_yield"] == Decimal("0.2")


def test_net_debt_change_pct_from_primary_net_debt():
    """Net Debt kommt jetzt direkt aus current/previous, nicht summiert."""
    current = {"net_debt": Decimal("-7764")}
    previous = {"net_debt": Decimal("-6023")}
    stammdaten = {"market_cap": Decimal("101100")}
    result, _adj = calculate_fy(current, previous, stammdaten)
    # change = prev - curr = -6023 - (-7764) = 1741 (positiv = Schulden-Abbau)
    assert result["net_debt_change"] == Decimal("1741")
    # 1741 / 101100 * 100 ≈ 1.722
    assert abs(result["net_debt_change_pct"] - Decimal("1.722")) < Decimal("0.01")


def test_net_debt_change_missing_prev():
    """Ohne Vorjahres-Net-Debt → kein change."""
    result, _adj = calculate_fy(
        {"net_debt": Decimal("100")},
        {},
        {"market_cap": Decimal("1000")},
    )
    assert result["net_debt_change"] is None
    assert result["net_debt_change_pct"] is None


def test_hohn_return_simple_servicenow():
    current = {
        "net_debt": Decimal("-7764"),  # Net Cash Position
        "fcf": Decimal("4636"),
        "net_income": Decimal("1748"),
        "sbc": Decimal("1900"),
    }
    previous = {
        "net_income": Decimal("1425"),
        "net_debt": Decimal("-6023"),
    }
    stammdaten = {"market_cap": Decimal("101100")}
    result, _adj = calculate_fy(current, previous, stammdaten)
    # fcf_yield ≈ 4.587, ni_growth ≈ 22.67, sbc_yield ≈ 1.879, nd_change_pct ≈ 1.722
    # simple = 4.587 + 22.67 - 1.879 + 1.722 ≈ 27.1
    assert result["hohn_return_simple"] is not None
    assert abs(result["hohn_return_simple"] - Decimal("27.1")) < Decimal("0.1")


def test_hohn_return_detailed_requires_dividends():
    current = {
        "fcf": Decimal("4636"),
        "net_income": Decimal("1748"),
        "sbc": Decimal("1900"),
        "buyback_volume": Decimal("1840"),
        "dividends": Decimal("0"),
    }
    previous = {"net_income": Decimal("1425")}
    stammdaten = {"market_cap": Decimal("101100")}
    result, _adj = calculate_fy(current, previous, stammdaten)
    # div_yield = 0, ni_growth ≈ 22.67, net_buyback = 1840-1900 = -60
    # net_buyback_yield = -60/101100*100 ≈ -0.06
    # detailed = 0 + 22.67 - 0.06 ≈ 22.6
    assert result["hohn_return_detailed"] is not None
    assert abs(result["hohn_return_detailed"] - Decimal("22.6")) < Decimal("0.1")


def test_zero_market_cap_safe():
    result, _adj = calculate_fy(
        {"fcf": Decimal("100"), "sbc": Decimal("10")},
        None,
        {"market_cap": Decimal("0")},
    )
    assert result["fcf_yield"] is None
    assert result["sbc_yield"] is None
    assert result["hohn_return_simple"] is None


def test_all_inputs_missing():
    assert calculate_fy({}, None, {})[0]["hohn_return_simple"] is None
    assert calculate_fy({}, None, {})[0]["hohn_return_detailed"] is None


def test_actual_return_from_next_year_mcap():
    result, _adj = calculate_fy(
        {"net_income": Decimal("100")},
        None,
        {"market_cap": Decimal("1000")},
        next_year_market_cap=Decimal("1200"),
    )
    # (1200 / 1000 - 1) * 100 = 20%
    assert result["actual_return"] == Decimal("20")


def test_net_buyback_with_only_sbc_defaults_buybacks_to_zero():
    """Firma ohne Buyback-Programm: statt Strich soll 0 - sbc stehen."""
    result, _adj = calculate_fy(
        {"sbc": Decimal("100")},
        None,
        {"market_cap": Decimal("10000")},
    )
    assert result["net_buyback"] == Decimal("-100")


def test_net_buyback_with_only_buybacks_defaults_sbc_to_zero():
    result, _adj = calculate_fy(
        {"buyback_volume": Decimal("500")},
        None,
        {"market_cap": Decimal("10000")},
    )
    assert result["net_buyback"] == Decimal("500")
