from decimal import Decimal


STAMMDATEN_CALC_KEYS: set[str] = set()

FY_CALC_KEYS = {
    "fcf",
    "net_debt",
    "ni_growth",
    "fcf_yield",
    "sbc_yield",
    "net_debt_change",
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
        "fcf": None,
        "net_debt": None,
        "ni_growth": None,
        "fcf_yield": None,
        "sbc_yield": None,
        "net_debt_change": None,
        "hohn_return": None,
    }

    op_cf = current.get("op_cash_flow")
    capex = current.get("capex")
    debt = current.get("debt")
    cash = current.get("cash")
    ni = current.get("net_income")

    if op_cf is not None and capex is not None:
        results["fcf"] = op_cf - capex

    if debt is not None and cash is not None:
        results["net_debt"] = debt - cash

    if previous:
        ni_prev = previous.get("net_income")
        if ni is not None and ni_prev is not None and ni_prev != 0:
            results["ni_growth"] = (ni / ni_prev - Decimal("1")) * Decimal("100")

    market_cap = stammdaten.get("market_cap")
    sbc = stammdaten.get("sbc")

    def _effective(key: str) -> Decimal | None:
        """Prefer freshly-computed value; fall back to stored value so a
        manually-overridden component still drives downstream ratios."""
        if results.get(key) is not None:
            return results[key]
        return current.get(key)

    eff_fcf = _effective("fcf")
    if eff_fcf is not None and market_cap is not None and market_cap != 0:
        results["fcf_yield"] = eff_fcf / market_cap * Decimal("100")

    if sbc is not None and market_cap is not None and market_cap != 0:
        results["sbc_yield"] = sbc / market_cap * Decimal("100")

    # Net Debt Change: debt reduction counts positive toward Hohn Return.
    # formula = (net_debt[Y-1] - net_debt[Y]) / market_cap * 100
    eff_net_debt = _effective("net_debt")
    if previous and eff_net_debt is not None and market_cap is not None and market_cap != 0:
        prev_debt = previous.get("debt")
        prev_cash = previous.get("cash")
        prev_net_debt = previous.get("net_debt")
        if prev_net_debt is None and prev_debt is not None and prev_cash is not None:
            prev_net_debt = prev_debt - prev_cash
        if prev_net_debt is not None:
            results["net_debt_change"] = (prev_net_debt - eff_net_debt) / market_cap * Decimal("100")

    fcf_yield = _effective("fcf_yield")
    ni_growth = _effective("ni_growth")
    sbc_yield = _effective("sbc_yield")
    net_debt_change = _effective("net_debt_change")

    terms = [fcf_yield, ni_growth, sbc_yield, net_debt_change]
    if fcf_yield is not None and ni_growth is not None:
        total = fcf_yield + ni_growth
        if sbc_yield is not None:
            total -= sbc_yield
        if net_debt_change is not None:
            total += net_debt_change
        results["hohn_return"] = total

    return results
