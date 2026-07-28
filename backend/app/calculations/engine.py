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

HOHN_KEYS = {"hohn_return_simple", "hohn_return_detailed", "h_peg"}

FY_CALC_KEYS = {
    "net_buyback",
    "sbc_yield",
    "net_buyback_yield",
    "buyback_yield",
    "fcf_yield",
    "ni_growth",
    "net_debt_change",
    "net_debt_change_pct",
    "dividend_yield",
    "actual_return",
    "pe_ratio",
    "ev_ebitda",
    # Detail-page additions:
    "ni_margin",
    "ocf_margin",
    "fcf_margin",
    "ps_ratio",
} | HOHN_KEYS

CALCULATED_KEYS = STAMMDATEN_CALC_KEYS | FY_CALC_KEYS


def _safe_div_pct(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator * Decimal("100")


# Banken/Versicherer: Finanzverbindlichkeiten sind Refinanzierung, kein
# Schulden-"Abbau" — net_debt_change_pct fliegt bei ihnen aus der H-Return
# (analog EBITDA=null in den Prompts). Exakte Ticker wie in der DB.
FINANCIAL_TICKERS = frozenset({"DBK.DE", "CBK.DE", "ALV.DE", "MUV2.DE", "HNR1.DE"})

# Turnaround-Schutz: |ni_growth| wird gekappt, sonst dominiert ein kleines/
# negatives Vorjahres-NI (Nenner) jedes Ranking.
NI_GROWTH_CAP = Decimal("100")


def is_financial(ticker: str | None) -> bool:
    return ticker in FINANCIAL_TICKERS


def _cap_growth(value: Decimal) -> Decimal:
    if value > NI_GROWTH_CAP:
        return NI_GROWTH_CAP
    if value < -NI_GROWTH_CAP:
        return -NI_GROWTH_CAP
    return value


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
    current_adjusted: dict[str, Decimal | None] | None = None,
    previous_adjusted: dict[str, Decimal | None] | None = None,
    exclude_net_debt_change: bool = False,
    is_running_fy: bool = False,
) -> tuple[dict[str, Decimal | None], dict[str, Decimal | None]]:
    """Berechnet Calculated-Werte fuer das FY.

    Returns (results, results_adjusted):
      - results: mit Reported-Inputs (Standard)
      - results_adjusted: mit Adjusted-Inputs fuer NI/EBITDA/FCF wenn vorhanden,
        sonst Fallback auf Reported. Wird genutzt um numeric_value_adjusted-
        Felder fuer Calculated-Multiples zu persistieren, damit Frontend bei
        Adjusted-Toggle keine Re-Berechnung machen muss.
    """
    results: dict[str, Decimal | None] = {k: None for k in FY_CALC_KEYS}
    results_adjusted: dict[str, Decimal | None] = {k: None for k in FY_CALC_KEYS}

    # market_cap_calc bevorzugt: PDF-shares × Yahoo-stock_price ist sauberer
    # Anker als Yahoo's eigener marketCap-Field (der bei Klassen-Aktien wie
    # Airbnb falsche shares-Zahlen nutzt). market_cap als Fallback wenn
    # market_cap_calc nicht da ist (AR-(N-1) nicht hochgeladen).
    # Sanity-Check: Wenn market_cap_calc um Faktor 2+ von Yahoo market_cap
    # abweicht, deutet das auf einen Stock-Split-Mismatch hin (Yahoo Adj Close
    # ist retroaktiv split-adjustiert, PDF-Shares aus aelteren AR-Reports sind
    # NICHT). In dem Fall Yahoo market_cap nehmen (split-konsistent).
    #
    # Bei laufendem FY (Estimate-Mode): IMMER aktuelle Stammdaten (Live-
    # Snapshot, heute) — kein Fallback auf FY-Anker. Kunden-Anforderung:
    # Estimates-Berechnungen mit dem Wert, der in den Stammdaten-Zellen liegt.
    # Bei abgeschlossenem FY: FY-Anker (Anfang FY = Ende FY-1), Fallback Snapshot.
    if is_running_fy:
        mcap_calc = stammdaten.get("market_cap_calc")
        mcap_yahoo = stammdaten.get("market_cap")
    else:
        mcap_calc = current.get("market_cap_calc") or stammdaten.get("market_cap_calc")
        mcap_yahoo = current.get("market_cap") or stammdaten.get("market_cap")
    if mcap_calc is not None and mcap_yahoo is not None and mcap_yahoo != 0:
        ratio = mcap_calc / mcap_yahoo
        if ratio < Decimal("0.5") or ratio > Decimal("2.0"):
            market_cap = mcap_yahoo
        else:
            market_cap = mcap_calc
    else:
        market_cap = mcap_calc or mcap_yahoo

    # net_debt kommt jetzt direkt aus der Extraktion (Primary Key) — keine
    # Aggregation aus Cash/Lease/LT-Debt-Subkomponenten mehr.
    net_debt = current.get("net_debt")

    buyback_vol = current.get("buyback_volume")
    sbc = current.get("sbc")
    # Fehlende Komponente = 0, solange mindestens eine belegt ist: Firmen
    # ohne Buyback-Programm sollen 0 - sbc zeigen statt einer leeren Zelle.
    if buyback_vol is not None or sbc is not None:
        results["net_buyback"] = (buyback_vol or Decimal("0")) - (sbc or Decimal("0"))

    results["sbc_yield"] = _safe_div_pct(sbc, market_cap)
    results["net_buyback_yield"] = _safe_div_pct(results["net_buyback"], market_cap)
    results["buyback_yield"] = _safe_div_pct(buyback_vol, market_cap)

    fcf = current.get("fcf")
    results["fcf_yield"] = _safe_div_pct(fcf, market_cap)

    # VALUATION-Multiples (Reported-Inputs):
    # KGV = Market Cap / Net Income (nur sinnvoll bei positivem NI).
    # EV/EBITDA = (Market Cap + Net Debt) / EBITDA.
    ebitda = current.get("ebitda")
    ni_for_pe = current.get("net_income")
    if market_cap is not None and ni_for_pe is not None and ni_for_pe > 0:
        results["pe_ratio"] = market_cap / ni_for_pe
    if market_cap is not None and ebitda is not None and ebitda > 0:
        ev = market_cap + (net_debt if net_debt is not None else Decimal("0"))
        results["ev_ebitda"] = ev / ebitda

    # Margins + PS Ratio (aus revenue). Reported.
    revenue = current.get("revenue")
    ocf = current.get("operating_cash_flow")
    if revenue is not None and revenue > 0:
        if ni_for_pe is not None:
            results["ni_margin"] = ni_for_pe / revenue * Decimal("100")
        if ocf is not None:
            results["ocf_margin"] = ocf / revenue * Decimal("100")
        if fcf is not None:
            results["fcf_margin"] = fcf / revenue * Decimal("100")
        if market_cap is not None:
            results["ps_ratio"] = market_cap / revenue

    # VALUATION-Multiples Adjusted: nur persistieren wenn echter Adjusted-Input
    # da ist (sonst bleibt numeric_value_adjusted=NULL und Frontend zeigt korrekt
    # 'kein Adj'-Fallback-Marker).
    ca = current_adjusted or {}
    ni_adj_raw = ca.get("net_income")
    ebitda_adj_raw = ca.get("ebitda")
    fcf_adj_raw = ca.get("fcf")
    if market_cap is not None and fcf_adj_raw is not None and market_cap != 0:
        results_adjusted["fcf_yield"] = fcf_adj_raw / market_cap * Decimal("100")
    if market_cap is not None and ni_adj_raw is not None and ni_adj_raw > 0:
        results_adjusted["pe_ratio"] = market_cap / ni_adj_raw
    if market_cap is not None and ebitda_adj_raw is not None and ebitda_adj_raw > 0:
        ev = market_cap + (net_debt if net_debt is not None else Decimal("0"))
        results_adjusted["ev_ebitda"] = ev / ebitda_adj_raw

    # Margins + PS Ratio Adjusted. Nutzen Adjusted-Revenue wenn vorhanden
    # (Organic/Constant-Currency), sonst Fallback auf Reported.
    revenue_adj_raw = ca.get("revenue")
    ocf_adj_raw = ca.get("operating_cash_flow")
    revenue_adj_eff = revenue_adj_raw if revenue_adj_raw is not None else revenue
    if revenue_adj_eff is not None and revenue_adj_eff > 0:
        if ni_adj_raw is not None:
            results_adjusted["ni_margin"] = ni_adj_raw / revenue_adj_eff * Decimal("100")
        if ocf_adj_raw is not None:
            results_adjusted["ocf_margin"] = ocf_adj_raw / revenue_adj_eff * Decimal("100")
        if fcf_adj_raw is not None:
            results_adjusted["fcf_margin"] = fcf_adj_raw / revenue_adj_eff * Decimal("100")
        if market_cap is not None and revenue_adj_raw is not None:
            # Nur setzen wenn revenue_adj_raw echt vorhanden — sonst identisch mit ps_ratio Reported.
            results_adjusted["ps_ratio"] = market_cap / revenue_adj_raw
    # Fuer NI-Growth + Hohn-Rendite-Adj brauchen wir Fallback (sonst kaskadieren
    # Forecast-Years die nur Adj-Prev haben aber kein Adj-Current ins Leere).
    ni_adj = ni_adj_raw if ni_adj_raw is not None else ni_for_pe

    ni = current.get("net_income")
    if previous:
        ni_prev = previous.get("net_income")
        if ni is not None and ni_prev is not None and ni_prev != 0:
            # |ni_prev| im Nenner, damit das Vorzeichen des Wachstums auch
            # bei negativem Vorjahres-Net-Income korrekt bleibt (Turnaround
            # von Verlust → Gewinn = positives Wachstum, nicht negatives).
            results["ni_growth"] = _cap_growth((ni - ni_prev) / abs(ni_prev) * Decimal("100"))
        # NI-Growth Adjusted: mode-konsistent (Adj-NI / Adj-NI-Prev).
        pa = previous_adjusted or {}
        ni_adj_prev = pa.get("net_income") if pa.get("net_income") is not None else ni_prev
        if ni_adj is not None and ni_adj_prev is not None and ni_adj_prev != 0:
            results_adjusted["ni_growth"] = _cap_growth((ni_adj - ni_adj_prev) / abs(ni_adj_prev) * Decimal("100"))

        # ΔNet Debt = previous − current (positive = Schulden-Abbau / Cash-Wachstum).
        prev_net_debt = previous.get("net_debt")
        if prev_net_debt is not None and net_debt is not None:
            results["net_debt_change"] = prev_net_debt - net_debt
            results["net_debt_change_pct"] = _safe_div_pct(results["net_debt_change"], market_cap)

    dividends = current.get("dividends")
    results["dividend_yield"] = _safe_div_pct(dividends, market_cap)

    # Realised Total Shareholder Return for COMPLETED FYs:
    #   actual_return = (MCap end-of-FY / MCap start-of-FY - 1) * 100
    # Where MCap end-of-FY-N is stored as period_year=N+1 market_cap (because
    # our convention anchors stammdaten to start-of-FY).
    # IMPORTANT: we use Yahoo Adj Close × shares for MCap, and Yahoo Adj Close
    # is already dividend-adjusted (back-corrected as if dividends were
    # reinvested). So this ratio already IS the Total Shareholder Return; we
    # must NOT add dividend_yield on top — that would double-count dividends.
    if (
        next_year_market_cap is not None
        and market_cap is not None
        and market_cap != 0
    ):
        results["actual_return"] = (next_year_market_cap / market_cap - Decimal("1")) * Decimal("100")

    fcf_yield = results.get("fcf_yield")
    ni_growth = results.get("ni_growth")
    sbc_yield = results.get("sbc_yield")
    nd_change_pct = results.get("net_debt_change_pct")
    if exclude_net_debt_change:
        # Wert bleibt gespeichert (Info), zaehlt aber nicht in die H-Return.
        nd_change_pct = None
    div_yield = results.get("dividend_yield")
    net_buyback_yield = results.get("net_buyback_yield")

    # Einfache Hohn-Rendite = FCF Yield + NI Growth − SBC/MCap + ΔND/MCap
    # Partial: summiere verfügbare Komponenten, mind. eine muss da sein.
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

    # Hohn-Rendite Adjusted: gleiche Formeln aber mit Adjusted-Komponenten
    # (fcf_yield_adj, ni_growth_adj). sbc/buyback/dividends/net-debt-change
    # bleiben Reported (kein Adjusted-Pendant per Definition).
    fcf_yield_adj = results_adjusted.get("fcf_yield")
    ni_growth_adj = results_adjusted.get("ni_growth")
    simple_parts_adj = [
        ("+", fcf_yield_adj if fcf_yield_adj is not None else fcf_yield),
        ("+", ni_growth_adj if ni_growth_adj is not None else ni_growth),
        ("-", sbc_yield),
        ("+", nd_change_pct),
    ]
    available_simple_adj = [(s, v) for s, v in simple_parts_adj if v is not None]
    if available_simple_adj:
        total_adj = Decimal("0")
        for sign, val in available_simple_adj:
            total_adj += -val if sign == "-" else val
        results_adjusted["hohn_return_simple"] = total_adj
    detailed_parts_adj = [
        ("+", div_yield),
        ("+", ni_growth_adj if ni_growth_adj is not None else ni_growth),
        ("+", net_buyback_yield),
        ("+", nd_change_pct),
    ]
    available_detailed_adj = [(s, v) for s, v in detailed_parts_adj if v is not None]
    if available_detailed_adj:
        total_d_adj = Decimal("0")
        for sign, val in available_detailed_adj:
            total_d_adj += -val if sign == "-" else val
        results_adjusted["hohn_return_detailed"] = total_d_adj

    # H-PEG = PE Ratio / H-Return (detailed, in %). Analog zum klassischen
    # PEG = PE / Growth. Nur sinnvoll bei positivem H-Return. In der Kunden-
    # Excel steht "= PE / H-Return / 100" weil dort H-Return als Dezimal
    # (0.15) gespeichert ist; bei uns ist H-Return als %-Wert (15.0), also
    # entfaellt der /100-Teiler.
    h_return_d = results.get("hohn_return_detailed")
    pe_ratio = results.get("pe_ratio")
    if h_return_d is not None and pe_ratio is not None and h_return_d > 0:
        results["h_peg"] = pe_ratio / h_return_d
    h_return_d_adj = results_adjusted.get("hohn_return_detailed")
    pe_ratio_adj = results_adjusted.get("pe_ratio")
    pe_for_peg_adj = pe_ratio_adj if pe_ratio_adj is not None else pe_ratio
    if h_return_d_adj is not None and pe_for_peg_adj is not None and h_return_d_adj > 0:
        results_adjusted["h_peg"] = pe_for_peg_adj / h_return_d_adj

    return results, results_adjusted


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
    return data.get("net_debt")


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
        # Vorzeichen-stabile Wachstumsdefinition (|ni_start| als Nenner).
        # CAGR braucht eine echte positive Ratio — nur dann sinnvoll
        # berechenbar, sonst None.
        cum_growth = (ni_end - ni_start) / abs(ni_start) * Decimal("100")
        ratio = ni_end / ni_start
        cagr = _cagr_pct(ratio, n) if ratio > 0 else None
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
