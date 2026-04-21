from decimal import Decimal


STAMMDATEN_CALC_KEYS: set[str] = set()

FY_CALC_KEYS = {
    "capex",
    "net_debt",
    "ni_growth",
    "fcf_yield",
    "sbc_yield",
    "net_debt_change",
    "net_debt_change_pct",
    "hohn_return_base",
    "hohn_return",
}

CALCULATED_KEYS = STAMMDATEN_CALC_KEYS | FY_CALC_KEYS


def calculate_stammdaten(values: dict[str, Decimal | None]) -> dict[str, Decimal | None]:
    return {}


def calculate_fy(
    current: dict[str, Decimal | None],
    previous: dict[str, Decimal | None] | None,
    stammdaten: dict[str, Decimal | None],
) -> dict[str, Decimal | None]:
    results: dict[str, Decimal | None] = {
        "capex": None,
        "net_debt": None,
        "ni_growth": None,
        "fcf_yield": None,
        "sbc_yield": None,
        "net_debt_change": None,
        "net_debt_change_pct": None,
        "hohn_return_base": None,
        "hohn_return": None,
    }

    op_cf = current.get("op_cash_flow")
    fcf_input = current.get("fcf")
    debt = current.get("debt")
    cash = current.get("cash")
    ni = current.get("net_income")
    sbc = current.get("sbc")

    if op_cf is not None and fcf_input is not None:
        results["capex"] = op_cf - fcf_input

    if debt is not None and cash is not None:
        results["net_debt"] = debt - cash

    if previous:
        ni_prev = previous.get("net_income")
        if ni is not None and ni_prev is not None and ni_prev != 0:
            results["ni_growth"] = (ni / ni_prev - Decimal("1")) * Decimal("100")

    market_cap = stammdaten.get("market_cap")

    def _effective(key: str) -> Decimal | None:
        if results.get(key) is not None:
            return results[key]
        return current.get(key)

    if fcf_input is not None and market_cap is not None and market_cap != 0:
        results["fcf_yield"] = fcf_input / market_cap * Decimal("100")

    if sbc is not None and market_cap is not None and market_cap != 0:
        results["sbc_yield"] = sbc / market_cap * Decimal("100")

    # Net Debt Change: prev minus curr (positive when debt decreased / cash grew).
    # This sign convention makes Hohn Return += ND_change_pct intuitive —
    # cash cushion growth contributes positively to shareholder return.
    eff_net_debt = _effective("net_debt")
    if previous and eff_net_debt is not None:
        prev_debt = previous.get("debt")
        prev_cash = previous.get("cash")
        prev_net_debt = previous.get("net_debt")
        if prev_net_debt is None and prev_debt is not None and prev_cash is not None:
            prev_net_debt = prev_debt - prev_cash
        if prev_net_debt is not None:
            results["net_debt_change"] = prev_net_debt - eff_net_debt
            if market_cap is not None and market_cap != 0:
                results["net_debt_change_pct"] = results["net_debt_change"] / market_cap * Decimal("100")

    fcf_yield = _effective("fcf_yield")
    ni_growth = _effective("ni_growth")
    sbc_yield = _effective("sbc_yield")
    nd_change_pct = _effective("net_debt_change_pct")

    if fcf_yield is not None and ni_growth is not None:
        base = fcf_yield + ni_growth
        if sbc_yield is not None:
            base -= sbc_yield
        results["hohn_return_base"] = base

        full = base
        if nd_change_pct is not None:
            full += nd_change_pct
        results["hohn_return"] = full

    return results
