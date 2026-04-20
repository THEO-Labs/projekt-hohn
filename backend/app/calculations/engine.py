from decimal import Decimal


CALCULATED_KEYS = {
    "fcf",
    "fcf_yield",
    "ni_growth",
    "sbc_yield",
    "hohn_return",
}


def calculate_all(
    values: dict[str, Decimal | None],
    previous_values: dict[str, Decimal | None] | None = None,
) -> dict[str, Decimal | None]:
    """Compute derived metrics for a single FY.

    `values` holds inputs for the target year (plus market_cap from Stammdaten).
    `previous_values` holds inputs for the year before, used only for NI growth
    (approximated via sales growth under the constant-margin assumption).
    """
    results: dict[str, Decimal | None] = {}

    sales = values.get("sales")
    net_income = values.get("net_income")
    fcf_margin_pct = values.get("fcf_margin_non_gaap")
    sbc = values.get("sbc")
    market_cap = values.get("market_cap")

    if sales is not None and fcf_margin_pct is not None:
        results["fcf"] = sales * fcf_margin_pct / Decimal("100")
    else:
        results["fcf"] = None

    fcf = results["fcf"]
    if fcf is not None and market_cap is not None and market_cap != 0:
        results["fcf_yield"] = fcf / market_cap * Decimal("100")
    else:
        results["fcf_yield"] = None

    sales_prev = previous_values.get("sales") if previous_values else None
    if sales is not None and sales_prev is not None and sales_prev != 0:
        results["ni_growth"] = (sales / sales_prev - Decimal("1")) * Decimal("100")
    else:
        results["ni_growth"] = None

    if sbc is not None and market_cap is not None and market_cap != 0:
        results["sbc_yield"] = sbc / market_cap * Decimal("100")
    else:
        results["sbc_yield"] = None

    fcf_yield = results["fcf_yield"]
    ni_growth = results["ni_growth"]
    sbc_yield = results["sbc_yield"]
    if fcf_yield is not None and ni_growth is not None and sbc_yield is not None:
        results["hohn_return"] = fcf_yield + ni_growth - sbc_yield
    else:
        results["hohn_return"] = None

    return results
