from decimal import Decimal


STAMMDATEN_CALC_KEYS = {
    "stock_price_eur",
    "market_cap_calc",
    "net_debt",
    "ev",
}

FY_CALC_KEYS = {
    "ni_growth",
    "op_cf_change",
    "fcf",
    "fcf_change",
    "ev_op_cf",
    "pe_ltm_adj",
    "pe_target",
    "fcf_yield",
    "dividend_yield",
    "peg",
    "hohn_return",
}

CALCULATED_KEYS = STAMMDATEN_CALC_KEYS | FY_CALC_KEYS


def _safe_div(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def calculate_stammdaten(values: dict[str, Decimal | None]) -> dict[str, Decimal | None]:
    """Pure snapshot calcs — no year context."""
    results: dict[str, Decimal | None] = {}

    stock_price = values.get("stock_price")
    exchange_rate = values.get("exchange_rate")
    shares = values.get("shares_outstanding")
    market_cap = values.get("market_cap")
    debt = values.get("debt")
    cash = values.get("cash")

    results["stock_price_eur"] = (
        stock_price * exchange_rate
        if stock_price is not None and exchange_rate is not None
        else None
    )
    results["market_cap_calc"] = (
        shares * stock_price
        if shares is not None and stock_price is not None
        else None
    )
    results["net_debt"] = (
        debt - cash
        if debt is not None and cash is not None
        else None
    )
    net_debt = results["net_debt"]
    results["ev"] = (
        market_cap + net_debt
        if market_cap is not None and net_debt is not None
        else None
    )

    return results


def calculate_fy(
    current: dict[str, Decimal | None],
    previous: dict[str, Decimal | None] | None,
    stammdaten: dict[str, Decimal | None],
) -> dict[str, Decimal | None]:
    """Per-FY calcs + Ratios. `stammdaten` already includes calculated stammdaten values."""
    results: dict[str, Decimal | None] = {}

    op_cf = current.get("op_cash_flow")
    capex = current.get("capex")
    ni = current.get("net_income")
    eps_adj = current.get("eps_adj")
    dividends = current.get("dividends")

    results["fcf"] = (
        op_cf - capex
        if op_cf is not None and capex is not None
        else None
    )
    fcf = results["fcf"]

    if previous:
        ni_prev = previous.get("net_income")
        op_cf_prev = previous.get("op_cash_flow")
        capex_prev = previous.get("capex")
        eps_adj_prev = previous.get("eps_adj")

        if ni is not None and ni_prev is not None and ni_prev != 0:
            results["ni_growth"] = (ni / ni_prev - Decimal("1")) * Decimal("100")
        else:
            results["ni_growth"] = None

        if op_cf is not None and op_cf_prev is not None and op_cf_prev != 0:
            results["op_cf_change"] = (op_cf / op_cf_prev - Decimal("1")) * Decimal("100")
        else:
            results["op_cf_change"] = None

        fcf_prev = (
            op_cf_prev - capex_prev
            if op_cf_prev is not None and capex_prev is not None
            else None
        )
        if fcf is not None and fcf_prev is not None and fcf_prev != 0:
            results["fcf_change"] = (fcf / fcf_prev - Decimal("1")) * Decimal("100")
        else:
            results["fcf_change"] = None
    else:
        results["ni_growth"] = None
        results["op_cf_change"] = None
        results["fcf_change"] = None

    stock_price = stammdaten.get("stock_price")
    market_cap = stammdaten.get("market_cap")
    ev = stammdaten.get("ev")
    sbc = stammdaten.get("sbc")

    results["ev_op_cf"] = _safe_div(ev, op_cf)

    results["pe_ltm_adj"] = (
        _safe_div(stock_price, previous.get("eps_adj")) if previous else None
    )
    results["pe_target"] = _safe_div(stock_price, eps_adj)

    results["fcf_yield"] = (
        _safe_div(fcf, market_cap) * Decimal("100")
        if _safe_div(fcf, market_cap) is not None
        else None
    )
    results["dividend_yield"] = (
        _safe_div(dividends, market_cap) * Decimal("100")
        if _safe_div(dividends, market_cap) is not None
        else None
    )

    pe_target = results["pe_target"]
    ni_growth = results.get("ni_growth")
    results["peg"] = _safe_div(pe_target, ni_growth)

    fcf_yield = results["fcf_yield"]
    sbc_yield = (
        sbc / market_cap * Decimal("100")
        if sbc is not None and market_cap is not None and market_cap != 0
        else None
    )
    if fcf_yield is not None and ni_growth is not None and sbc_yield is not None:
        results["hohn_return"] = fcf_yield + ni_growth - sbc_yield
    elif fcf_yield is not None and ni_growth is not None:
        results["hohn_return"] = fcf_yield + ni_growth
    else:
        results["hohn_return"] = None

    return results
