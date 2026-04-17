from decimal import Decimal


CALCULATED_KEYS = {
    "net_debt",
    "eps_growth",
    "buyback_return",
    "hohn_rendite_basic_1",
    "fcf_yield",
    "hohn_rendite_basic_2",
    "pe_target_analysts",
    "upside_potential",
    "risk_factor",
    "mgmt_factor",
    "total_adjustment_factor",
    "hohn_rendite_adjusted",
}


def calculate_all(values: dict[str, Decimal | None]) -> dict[str, Decimal | None]:
    results: dict[str, Decimal | None] = {}

    def get(key: str) -> Decimal | None:
        v = values.get(key)
        if v is not None:
            return v
        return results.get(key)

    def safe_div(a: Decimal | None, b: Decimal | None) -> Decimal | None:
        if a is None or b is None or b == 0:
            return None
        return a / b

    def safe_pct(a: Decimal | None, b: Decimal | None) -> Decimal | None:
        if a is None or b is None or b == 0:
            return None
        return ((a - b) / abs(b)) * Decimal("100")

    debt = get("debt")
    cash = get("cash")
    results["net_debt"] = (debt - cash) if debt is not None and cash is not None else None

    results["eps_growth"] = safe_pct(get("eps_forward"), get("eps_ttm"))

    buybacks = get("buybacks")
    mcap = get("market_cap")
    if buybacks is not None and mcap is not None and mcap > 0:
        results["buyback_return"] = abs(buybacks) / mcap * Decimal("100")
    else:
        results["buyback_return"] = None

    div_ret = get("dividend_return")
    bb_ret = results.get("buyback_return")
    eps_g = results.get("eps_growth")
    if div_ret is not None or bb_ret is not None or eps_g is not None:
        results["hohn_rendite_basic_1"] = (
            (div_ret or Decimal(0)) + (bb_ret or Decimal(0)) + (eps_g or Decimal(0))
        )
    else:
        results["hohn_rendite_basic_1"] = None

    fcf = get("free_cash_flow")
    if fcf is not None and mcap is not None and mcap > 0:
        results["fcf_yield"] = fcf / mcap * Decimal("100")
    else:
        results["fcf_yield"] = None

    fcf_y = results.get("fcf_yield")
    eps_g2 = results.get("eps_growth")
    if fcf_y is not None or eps_g2 is not None:
        results["hohn_rendite_basic_2"] = (fcf_y or Decimal(0)) + (eps_g2 or Decimal(0))
    else:
        results["hohn_rendite_basic_2"] = None

    results["pe_target_analysts"] = safe_div(get("analysts_target"), get("eps_forward"))

    results["upside_potential"] = safe_pct(get("analysts_target"), get("stock_price"))

    risk_keys = ["risk_business_model", "risk_regulatory", "risk_macro"]
    risk_vals = [get(k) for k in risk_keys if get(k) is not None]
    results["risk_factor"] = (sum(risk_vals) / Decimal(len(risk_vals))) if risk_vals else None

    mgmt_keys = ["mgmt_participation"]
    mgmt_vals = [get(k) for k in mgmt_keys if get(k) is not None]
    results["mgmt_factor"] = (sum(mgmt_vals) / Decimal(len(mgmt_vals))) if mgmt_vals else None

    rf = results.get("risk_factor")
    mf = results.get("mgmt_factor")
    if rf is not None and mf is not None:
        results["total_adjustment_factor"] = rf * mf
    elif rf is not None:
        results["total_adjustment_factor"] = rf
    elif mf is not None:
        results["total_adjustment_factor"] = mf
    else:
        results["total_adjustment_factor"] = None

    hohn1 = results.get("hohn_rendite_basic_1")
    taf = results.get("total_adjustment_factor")
    if hohn1 is not None and taf is not None:
        results["hohn_rendite_adjusted"] = hohn1 * taf
    elif hohn1 is not None:
        results["hohn_rendite_adjusted"] = hohn1
    else:
        results["hohn_rendite_adjusted"] = None

    return results
