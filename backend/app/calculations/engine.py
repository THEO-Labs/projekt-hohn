from decimal import Decimal


STAMMDATEN_CALC_KEYS: set[str] = set()

FY_CALC_KEYS = {
    "fcf",
    "ni_growth",
    "fcf_yield",
    "sbc_yield",
    "hohn_return",
}

CALCULATED_KEYS = STAMMDATEN_CALC_KEYS | FY_CALC_KEYS


def calculate_stammdaten(values: dict[str, Decimal | None]) -> dict[str, Decimal | None]:
    """Nothing to derive at stammdaten level for the minimal Hohn-Return model."""
    return {}


def calculate_fy(
    current: dict[str, Decimal | None],
    previous: dict[str, Decimal | None] | None,
    stammdaten: dict[str, Decimal | None],
) -> dict[str, Decimal | None]:
    results: dict[str, Decimal | None] = {
        "fcf": None,
        "ni_growth": None,
        "fcf_yield": None,
        "sbc_yield": None,
        "hohn_return": None,
    }

    op_cf = current.get("op_cash_flow")
    capex = current.get("capex")
    ni = current.get("net_income")

    if op_cf is not None and capex is not None:
        results["fcf"] = op_cf - capex

    if previous:
        ni_prev = previous.get("net_income")
        if ni is not None and ni_prev is not None and ni_prev != 0:
            results["ni_growth"] = (ni / ni_prev - Decimal("1")) * Decimal("100")

    market_cap = stammdaten.get("market_cap")
    sbc = stammdaten.get("sbc")

    def _effective(key: str) -> Decimal | None:
        """Prefer freshly-computed value; fall back to the stored value
        (e.g. manual override) so downstream ratios see an effective number."""
        if results.get(key) is not None:
            return results[key]
        return current.get(key)

    eff_fcf = _effective("fcf")
    if eff_fcf is not None and market_cap is not None and market_cap != 0:
        results["fcf_yield"] = eff_fcf / market_cap * Decimal("100")

    if sbc is not None and market_cap is not None and market_cap != 0:
        results["sbc_yield"] = sbc / market_cap * Decimal("100")

    fcf_yield = _effective("fcf_yield")
    ni_growth = _effective("ni_growth")
    sbc_yield = _effective("sbc_yield")

    if fcf_yield is not None and ni_growth is not None and sbc_yield is not None:
        results["hohn_return"] = fcf_yield + ni_growth - sbc_yield
    elif fcf_yield is not None and ni_growth is not None:
        results["hohn_return"] = fcf_yield + ni_growth

    return results
