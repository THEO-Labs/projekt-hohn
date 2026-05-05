from decimal import Decimal


def _cagr_pct(growth_factor: Decimal, n: int) -> Decimal | None:
    """Annualize a multi-period growth factor (>0) over n years -> percent."""
    if n <= 0:
        return None
    try:
        gf = float(growth_factor)
        if gf <= 0:
            return None
        annual = gf ** (1.0 / n) - 1.0
        return Decimal(str(annual)) * Decimal("100")
    except (ValueError, OverflowError):
        return None


STAMMDATEN_CALC_KEYS = {"market_cap_calc"}

FY_CALC_KEYS = {
    "cash_sum",
    "debt_sum",
    "net_debt",
    "ev",
    "net_buyback",
    "sbc_yield",
    "net_buyback_yield",
    "buyback_yield",
    "fcf_yield",
    "ni_growth",
    "net_debt_change",
    "net_debt_change_pct",
    "dividend_yield",
    "hohn_return_simple",
    "hohn_return_detailed",
    # Realised total shareholder return: ((MCap end FY / MCap start FY) - 1)
    # + dividend yield. Only meaningful when next-year start-of-FY MCap exists
    # (= end-of-current-FY) AND the FY itself is fully reported (not estimate).
    "actual_return",
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
    next_year_market_cap: Decimal | None = None,
) -> dict[str, Decimal | None]:
    results: dict[str, Decimal | None] = {k: None for k in FY_CALC_KEYS}

    # `current.market_cap` is now stored as the START-of-FY snapshot (anchor
    # = FY-end of period_year-1) by _fetch_and_store_historical_mcap, so this
    # gives the investor-entry-point denominator directly. SNAPSHOT MCap is
    # only used as fallback when historical hasn't been fetched yet.
    market_cap = current.get("market_cap") or stammdaten.get("market_cap")

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
    results["buyback_yield"] = _safe_div_pct(buyback_vol, market_cap)

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

    # Realised Total Shareholder Return for COMPLETED FYs:
    #   actual_return = (MCap end-of-FY / MCap start-of-FY - 1) * 100 + dividend_yield
    # Where MCap end-of-FY-N is stored as period_year=N+1 market_cap (because
    # our convention anchors stammdaten to start-of-FY). Returns None for
    # the running FY (next_year_market_cap unavailable yet) — caller should
    # additionally suppress display when the FY itself is a forecast.
    if (
        next_year_market_cap is not None
        and market_cap is not None
        and market_cap != 0
    ):
        price_return_pct = (next_year_market_cap / market_cap - Decimal("1")) * Decimal("100")
        div_pct = results.get("dividend_yield") or Decimal("0")
        results["actual_return"] = price_return_pct + div_pct

    fcf_yield = results.get("fcf_yield")
    ni_growth = results.get("ni_growth")
    sbc_yield = results.get("sbc_yield")
    nd_change_pct = results.get("net_debt_change_pct")
    div_yield = results.get("dividend_yield")
    net_buyback_yield = results.get("net_buyback_yield")

    # Einfache Hohn-Rendite = FCF Yield + NI Growth − SBC/MCap + ΔND/MCap
    # Partial: summiere verfuegbare Komponenten, mind. eine muss da sein.
    simple_parts = [
        ("+", fcf_yield),
        ("+", ni_growth),
        ("-", sbc_yield),
        ("+", nd_change_pct),
    ]
    available_simple = [(s, v) for s, v in simple_parts if v is not None]
    if available_simple:
        total = Decimal("0")
        for sign, val in available_simple:
            total += -val if sign == "-" else val
        results["hohn_return_simple"] = total

    # Detailed Hohn-Rendite = Div Yield + NI Growth + Net Buyback/MCap + ΔND/MCap
    detailed_parts = [
        ("+", div_yield),
        ("+", ni_growth),
        ("+", net_buyback_yield),
        ("+", nd_change_pct),
    ]
    available_detailed = [(s, v) for s, v in detailed_parts if v is not None]
    if available_detailed:
        total_d = Decimal("0")
        for sign, val in available_detailed:
            total_d += -val if sign == "-" else val
        results["hohn_return_detailed"] = total_d

    return results


CUMULATIVE_KEYS = (
    "fcf_yield",
    "sbc_yield",
    "dividend_yield",
    "buyback_yield",
    "net_buyback_yield",
    "net_debt_change_pct",
    "ni_growth",
    "hohn_return_simple",
    "hohn_return_detailed",
)


def _empty_cell(missing: list[str] | None = None) -> dict:
    return {"cum": None, "pa_avg": None, "pa_cagr": None, "missing": missing or []}


def _yield_cell(sum_value: Decimal | None, market_cap: Decimal | None, n: int, missing: list[str] | None = None) -> dict:
    if sum_value is None or market_cap is None or market_cap == 0 or n <= 0:
        return _empty_cell(missing)
    cum = sum_value / market_cap * Decimal("100")
    pa_avg = cum / Decimal(n)
    pa_cagr = _cagr_pct(Decimal("1") + cum / Decimal("100"), n)
    return {"cum": cum, "pa_avg": pa_avg, "pa_cagr": pa_cagr, "missing": missing or []}


def _net_debt_of(data: dict[str, Decimal | None]) -> Decimal | None:
    nd = data.get("net_debt")
    if nd is not None:
        return nd
    debt_components = [data.get("lease_liabilities"), data.get("long_term_debt")]
    cash_components = [
        data.get("cash_and_equivalents"),
        data.get("marketable_securities_st"),
        data.get("marketable_securities_lt"),
    ]
    if not any(v is not None for v in debt_components + cash_components):
        return None
    debt = sum((v for v in debt_components if v is not None), Decimal("0"))
    cash = sum((v for v in cash_components if v is not None), Decimal("0"))
    return debt - cash


def _sum_over_years(year_data: dict[int, dict[str, Decimal | None]], key: str) -> tuple[Decimal | None, list[str]]:
    """Returns (sum, missing_year_labels). Sum is None if any year is missing."""
    vals = []
    missing: list[str] = []
    for y in sorted(year_data.keys()):
        v = year_data[y].get(key)
        if v is None:
            missing.append(f"{key} FY{y}")
            continue
        vals.append(v)
    if missing:
        return None, missing
    if not vals:
        return None, [key]
    return sum(vals, Decimal("0")), []


def calculate_cumulative(
    year_data: dict[int, dict[str, Decimal | None]],
    pre_period_data: dict[str, Decimal | None],
    stammdaten: dict[str, Decimal | None],
) -> dict[str, dict]:
    """Compute cumulative Hohn-Rendite components over a multi-year period.
    Returns {key: {cum, pa_avg, pa_cagr, missing}} for each metric in CUMULATIVE_KEYS.
    Yields use the START-OF-PERIOD market_cap (= end of from_year-1) as denominator,
    matching the "investor entry-point" backtest framing. Falls back to SNAPSHOT
    if pre-period MCap is missing. NI Growth is end-to-end (NI[last] / NI[pre] - 1).
    ΔND is start-period − end-period.
    `missing` lists which inputs/components are not available; if non-empty,
    cum/pa_avg/pa_cagr may be None or partial."""
    results: dict[str, dict] = {k: _empty_cell() for k in CUMULATIVE_KEYS}

    years = sorted(year_data.keys())
    n = len(years)
    if n == 0:
        return results
    last_data = year_data[years[-1]]
    first_data = year_data[years[0]]
    # FY rows now store stammdaten anchored to the START of that FY, so the
    # MCap of year_data[from_year] is exactly the entry-point denominator we
    # want for the whole cumulative window. Fall back to SNAPSHOT only if the
    # historical fetch hasn't run yet.
    market_cap = first_data.get("market_cap") or stammdaten.get("market_cap")

    if market_cap is None or market_cap == 0:
        for k in CUMULATIVE_KEYS:
            results[k] = _empty_cell(["market_cap (Anfang Periode)"])
        return results

    sum_fcf, miss_fcf = _sum_over_years(year_data, "fcf")
    sum_sbc, miss_sbc = _sum_over_years(year_data, "sbc")
    sum_buybacks, miss_bb = _sum_over_years(year_data, "buyback_volume")
    sum_dividends, miss_div = _sum_over_years(year_data, "dividends")
    sum_net_buyback = (
        sum_buybacks - sum_sbc
        if sum_buybacks is not None and sum_sbc is not None
        else None
    )
    miss_net_buyback = sorted(set(miss_bb) | set(miss_sbc))

    results["fcf_yield"] = _yield_cell(sum_fcf, market_cap, n, miss_fcf)
    results["sbc_yield"] = _yield_cell(sum_sbc, market_cap, n, miss_sbc)
    results["dividend_yield"] = _yield_cell(sum_dividends, market_cap, n, miss_div)
    results["buyback_yield"] = _yield_cell(sum_buybacks, market_cap, n, miss_bb)
    results["net_buyback_yield"] = _yield_cell(sum_net_buyback, market_cap, n, miss_net_buyback)

    ni_end = last_data.get("net_income")
    ni_start = pre_period_data.get("net_income")
    ni_missing: list[str] = []
    if ni_end is None:
        ni_missing.append(f"net_income FY{years[-1]}")
    if ni_start is None:
        ni_missing.append(f"net_income FY{years[0] - 1} (pre-period)")
    if ni_end is not None and ni_start is not None and ni_start != 0:
        ratio = ni_end / ni_start
        cum_growth = (ratio - Decimal("1")) * Decimal("100")
        cagr = _cagr_pct(ratio, n)
        results["ni_growth"] = {
            "cum": cum_growth,
            "pa_avg": cum_growth / Decimal(n),
            "pa_cagr": cagr,
            "missing": [],
        }
    else:
        results["ni_growth"] = _empty_cell(ni_missing)

    nd_end = _net_debt_of(last_data)
    nd_start = _net_debt_of(pre_period_data)
    nd_missing: list[str] = []
    if nd_end is None:
        nd_missing.append(f"net_debt FY{years[-1]}")
    if nd_start is None:
        nd_missing.append(f"net_debt FY{years[0] - 1} (pre-period)")
    nd_change = (nd_start - nd_end) if nd_start is not None and nd_end is not None else None
    results["net_debt_change_pct"] = _yield_cell(nd_change, market_cap, n, nd_missing)

    def _aggregate(*parts: tuple[str, str]) -> dict:
        out: dict = {"missing": []}
        component_missing: list[str] = []
        for sign, key in parts:
            cell_missing = results[key].get("missing", [])
            if cell_missing:
                component_missing.append(f"{key}: {', '.join(cell_missing)}")
        for slot in ("cum", "pa_avg"):
            total = Decimal("0")
            available_count = 0
            for sign, key in parts:
                cell = results[key].get(slot)
                if cell is None:
                    continue
                total = total + (-cell if sign == "-" else cell)
                available_count += 1
            out[slot] = total if available_count > 0 else None
        out["pa_cagr"] = (
            _cagr_pct(Decimal("1") + out["cum"] / Decimal("100"), n)
            if out["cum"] is not None
            else None
        )
        out["missing"] = component_missing
        return out

    results["hohn_return_simple"] = _aggregate(
        ("+", "fcf_yield"), ("+", "ni_growth"), ("-", "sbc_yield"), ("+", "net_debt_change_pct"),
    )
    results["hohn_return_detailed"] = _aggregate(
        ("+", "dividend_yield"), ("+", "ni_growth"), ("+", "net_buyback_yield"), ("+", "net_debt_change_pct"),
    )

    return results
