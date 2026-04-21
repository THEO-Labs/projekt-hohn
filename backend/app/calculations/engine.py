from decimal import Decimal


STAMMDATEN_CALC_KEYS = {"market_cap_calc"}

FY_CALC_KEYS = {
    "cash_sum",
    "debt_sum",
    "net_debt",
    "ev",
    "net_buyback",
    "sbc_yield",
    "net_buyback_yield",
    "fcf_yield",
    "ni_growth",
    "net_debt_change",
    "net_debt_change_pct",
    "dividend_yield",
    "hohn_return_simple",
    "hohn_return_detailed",
}

CALCULATED_KEYS = STAMMDATEN_CALC_KEYS | FY_CALC_KEYS


def _safe_div_pct(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator * Decimal("100")


def calculate_stammdaten(values: dict[str, Decimal | None]) -> dict[str, Decimal | None]:
    results: dict[str, Decimal | None] = {"market_cap_calc": None}
    stock_price = values.get("stock_price")
    shares = values.get("shares_outstanding")
    if stock_price is not None and shares is not None:
        results["market_cap_calc"] = stock_price * shares
    return results


def calculate_fy(
    current: dict[str, Decimal | None],
    previous: dict[str, Decimal | None] | None,
    stammdaten: dict[str, Decimal | None],
) -> dict[str, Decimal | None]:
    results: dict[str, Decimal | None] = {k: None for k in FY_CALC_KEYS}

    market_cap = stammdaten.get("market_cap")

    cash_eq = current.get("cash_and_equivalents")
    mkt_st = current.get("marketable_securities_st")
    mkt_lt = current.get("marketable_securities_lt")
    if any(v is not None for v in (cash_eq, mkt_st, mkt_lt)):
        results["cash_sum"] = sum((v for v in (cash_eq, mkt_st, mkt_lt) if v is not None), Decimal("0"))

    leases = current.get("lease_liabilities")
    lt_debt = current.get("long_term_debt")
    if any(v is not None for v in (leases, lt_debt)):
        results["debt_sum"] = sum((v for v in (leases, lt_debt) if v is not None), Decimal("0"))

    debt_sum = results["debt_sum"]
    cash_sum = results["cash_sum"]
    if debt_sum is not None and cash_sum is not None:
        results["net_debt"] = debt_sum - cash_sum
    net_debt = results["net_debt"]

    if market_cap is not None and net_debt is not None:
        results["ev"] = market_cap + net_debt

    buyback_vol = current.get("buyback_volume")
    sbc = current.get("sbc")
    if buyback_vol is not None and sbc is not None:
        results["net_buyback"] = buyback_vol - sbc

    results["sbc_yield"] = _safe_div_pct(sbc, market_cap)
    results["net_buyback_yield"] = _safe_div_pct(results["net_buyback"], market_cap)

    fcf = current.get("fcf")
    results["fcf_yield"] = _safe_div_pct(fcf, market_cap)

    ni = current.get("net_income")
    if previous:
        ni_prev = previous.get("net_income")
        if ni is not None and ni_prev is not None and ni_prev != 0:
            results["ni_growth"] = (ni / ni_prev - Decimal("1")) * Decimal("100")

        # ΔNet Debt = previous - current (positive = Schulden-Abbau / Cash-Wachstum).
        prev_net_debt = previous.get("net_debt")
        if prev_net_debt is None:
            prev_debt = previous.get("debt_sum")
            prev_cash = previous.get("cash_sum")
            if prev_debt is None and (previous.get("lease_liabilities") is not None or previous.get("long_term_debt") is not None):
                prev_debt = sum(
                    (v for v in (previous.get("lease_liabilities"), previous.get("long_term_debt")) if v is not None),
                    Decimal("0"),
                )
            if prev_cash is None and any(previous.get(k) is not None for k in ("cash_and_equivalents", "marketable_securities_st", "marketable_securities_lt")):
                prev_cash = sum(
                    (v for v in (previous.get("cash_and_equivalents"), previous.get("marketable_securities_st"), previous.get("marketable_securities_lt")) if v is not None),
                    Decimal("0"),
                )
            if prev_debt is not None and prev_cash is not None:
                prev_net_debt = prev_debt - prev_cash

        eff_net_debt = net_debt if net_debt is not None else current.get("net_debt")
        if prev_net_debt is not None and eff_net_debt is not None:
            results["net_debt_change"] = prev_net_debt - eff_net_debt
            results["net_debt_change_pct"] = _safe_div_pct(results["net_debt_change"], market_cap)

    dividends = current.get("dividends")
    results["dividend_yield"] = _safe_div_pct(dividends, market_cap)

    def _effective(key: str) -> Decimal | None:
        if results.get(key) is not None:
            return results[key]
        return current.get(key)

    fcf_yield = _effective("fcf_yield")
    ni_growth = _effective("ni_growth")
    sbc_yield = _effective("sbc_yield")
    nd_change_pct = _effective("net_debt_change_pct")
    div_yield = _effective("dividend_yield")
    net_buyback_yield = _effective("net_buyback_yield")

    # Einfache Hohn-Rendite = FCF Yield + NI Growth − SBC/MCap + ΔND/MCap
    if fcf_yield is not None and ni_growth is not None:
        total = fcf_yield + ni_growth
        if sbc_yield is not None:
            total -= sbc_yield
        if nd_change_pct is not None:
            total += nd_change_pct
        results["hohn_return_simple"] = total

    # Detailed Hohn-Rendite = Div Yield + NI Growth + Net Buyback/MCap + ΔND/MCap
    if div_yield is not None and ni_growth is not None:
        total_d = div_yield + ni_growth
        if net_buyback_yield is not None:
            total_d += net_buyback_yield
        if nd_change_pct is not None:
            total_d += nd_change_pct
        results["hohn_return_detailed"] = total_d

    return results
