"""Detail-page aggregation endpoint.

One request → everything the frontend needs to render the company detail
page (overview stammdaten, overview metrics, all quarterly sections, and
the balance-sheet card). Reads only what is already persisted in the DB;
does not trigger the refresh pipeline (that stays behind the explicit
refresh endpoints).
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.models import User
from app.companies.isin import accounting_standard
from app.companies.models import Company
from app.db import get_db
from app.portfolios.models import Portfolio
from app.values.currency_keys import CURRENCY_KEYS
from app.values.models import CompanyValue, ValueDefinition
from app.values.quarterly_estimates import SUMMABLE_QUARTERLY_KEYS


detail_router = APIRouter(prefix="/api/companies", tags=["detail-page"])


# --- Response models ------------------------------------------------------


class ValueRef(BaseModel):
    """Compact reference to a single stored value + provenance."""
    value: Decimal | None = None
    adjusted: Decimal | None = None
    source_name: str | None = None
    source_link: str | None = None
    fetched_at: datetime | None = None
    manually_overridden: bool = False
    primary_method: str | None = None
    # is_forecast is the authoritative marker for "Actual (already reported)"
    # vs "Estimate (not yet reported)". Used by the frontend for colour coding.
    # Independent of primary_method: Claude can also fetch actuals from an
    # already-published 10-Q (is_forecast=false), which should render as
    # Actual, not Estimate.
    is_forecast: bool = False
    consistency_flags: str | None = None
    # "not_yet_reported": Periode des laufenden Jahres, deren Zahlen die
    # Firma noch gar nicht veroeffentlicht haben kann (Quartalsende in der
    # Zukunft oder juenger als die typische Reporting-Frist). Das UI rendert
    # das dezent grau statt rot (not_found = Recherche fand nichts).
    status: str | None = None


class QuarterlyRow(BaseModel):
    q1: ValueRef = ValueRef()
    q2: ValueRef = ValueRef()
    q3: ValueRef = ValueRef()
    q4: ValueRef = ValueRef()
    annual: ValueRef = ValueRef()


class QuarterlySection(BaseModel):
    value_key: str
    label_en: str
    label_de: str
    is_currency: bool
    is_summable: bool
    unit: str | None
    current_year: int | None
    prior_year: int | None
    current: QuarterlyRow = QuarterlyRow()
    prior: QuarterlyRow = QuarterlyRow()


class OverviewMetric(BaseModel):
    value_key: str
    label_en: str
    label_de: str
    unit: str | None
    ref: ValueRef


class StammdatenPack(BaseModel):
    stock_price: ValueRef = ValueRef()
    shares_outstanding: ValueRef = ValueRef()
    market_cap: ValueRef = ValueRef()
    enterprise_value: ValueRef = ValueRef()


class BalanceSheetRow(BaseModel):
    label: str
    value_key: str | None
    y1: ValueRef = ValueRef()
    y2: ValueRef = ValueRef()
    format_hint: str = "currency"  # "currency" | "percent"
    emphasis: bool = False


class BalanceSheetGroup(BaseModel):
    rows: list[BalanceSheetRow]


class BalanceSheetSection(BaseModel):
    current_year: int | None
    prior_year: int | None
    groups: list[BalanceSheetGroup]


class CompanyMetaOut(BaseModel):
    id: UUID
    portfolio_id: UUID
    name: str
    ticker: str
    isin: str | None
    currency: str
    fiscal_year_end_month: int | None
    fiscal_year_end_day: int | None
    # Berechnetes Feld: US-Filer -> "US-GAAP", sonst "IFRS"
    accounting_standard: str


class CompanyDetailOut(BaseModel):
    company: CompanyMetaOut
    current_fy_estimate_year: int | None
    prior_fy_year: int | None
    stammdaten: StammdatenPack
    overview_metrics: list[OverviewMetric]
    quarterly: list[QuarterlySection]
    balance_sheet: BalanceSheetSection


# --- Helpers --------------------------------------------------------------

# Quarterly display order for the detail page (income statement first, then
# cashflow, then capital allocation).
QUARTERLY_DISPLAY_KEYS: list[str] = [
    "revenue",
    "net_income",
    "eps_diluted",
    "operating_cash_flow",
    "capex",
    "fcf",
    "dividends",
    "buyback_volume",
    "sbc",
    "net_buyback",
]

OVERVIEW_METRIC_KEYS: list[str] = [
    "hohn_return_detailed",
    "dividend_yield",
    "ni_growth",
    "net_buyback_yield",
    "net_debt_change_pct",
    "h_peg",
    "pe_ratio",
    "fcf_yield",
    "ev_ebitda",
    "ps_ratio",
]


# Typische Frist zwischen Quartalsende und Veroeffentlichung des Berichts.
# Innerhalb dieser Frist gilt eine fehlende Zahl als "noch nicht berichtet",
# nicht als Recherche-Fehlschlag.
REPORTING_GRACE_DAYS = 45


def quarter_end_date(
    period_year: int,
    quarter: str,
    fy_end_month: int | None,
    fy_end_day: int | None,
) -> date | None:
    """Quartalsende aus dem FY-Ende ableiten — gleiche Konvention wie
    edgar._q_end_date: Q4 = FY-Ende, Q3 = -3M, Q2 = -6M, Q1 = -9M, mit
    Day-Clamping auf den Monatsletzten. Ohne bekanntes FY-Ende fallen wir
    auf Kalenderquartale zurueck (FY-Ende 31.12.)."""
    if quarter not in ("Q1", "Q2", "Q3", "Q4"):
        return None
    if not fy_end_month or not fy_end_day:
        fy_end_month, fy_end_day = 12, 31
    try:
        fy_end = date(period_year, fy_end_month, fy_end_day)
    except ValueError:
        return None
    months_back = (4 - int(quarter[1])) * 3
    new_month = fy_end.month - months_back
    new_year = fy_end.year
    while new_month <= 0:
        new_month += 12
        new_year -= 1
    last_day = calendar.monthrange(new_year, new_month)[1]
    return date(new_year, new_month, min(fy_end.day, last_day))


def is_not_yet_reported(
    period_year: int | None,
    quarter: str,
    fy_end_month: int | None,
    fy_end_day: int | None,
    today: date | None = None,
) -> bool:
    """True, wenn die Firma die Zahlen fuer dieses Quartal noch gar nicht
    veroeffentlicht haben kann: Quartalsende liegt in der Zukunft oder
    weniger als REPORTING_GRACE_DAYS zurueck. Laengst faellige Perioden
    (z.B. Vorjahr) liefern False."""
    if period_year is None:
        return False
    if today is None:
        today = datetime.now(timezone.utc).date()
    q_end = quarter_end_date(period_year, quarter, fy_end_month, fy_end_day)
    if q_end is None:
        return False
    return (today - q_end).days < REPORTING_GRACE_DAYS


def _to_ref(row: CompanyValue | None) -> ValueRef:
    if row is None:
        return ValueRef()
    return ValueRef(
        value=row.numeric_value,
        adjusted=row.numeric_value_adjusted,
        source_name=row.source_name,
        source_link=row.source_link,
        fetched_at=row.fetched_at,
        manually_overridden=bool(row.manually_overridden),
        primary_method=row.primary_method,
        is_forecast=bool(row.is_forecast),
        consistency_flags=row.consistency_flags,
    )


def _fy_estimate_year(db: Session, company_id: UUID) -> int | None:
    """Latest FY period_year that has an h_return_detailed row —
    same logic as the frontend uses."""
    max_year = (
        db.query(func.max(CompanyValue.period_year))
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == "hohn_return_detailed",
            CompanyValue.period_type == "FY",
        )
        .scalar()
    )
    return int(max_year) if max_year is not None else None


def _load_rows(
    db: Session,
    company_id: UUID,
    keys: list[str],
    period_types: list[str],
    period_years: list[int | None],
) -> dict[tuple[str, str, int | None], CompanyValue]:
    """Bulk-load rows keyed by (value_key, period_type, period_year)."""
    if not keys:
        return {}
    q = db.query(CompanyValue).filter(
        CompanyValue.company_id == company_id,
        CompanyValue.value_key.in_(keys),
    )
    if period_types:
        q = q.filter(CompanyValue.period_type.in_(period_types))
    if period_years:
        # Nullable-in-list is safe: SQLAlchemy renders NULL correctly.
        q = q.filter(CompanyValue.period_year.in_(period_years))
    rows = q.all()
    out: dict[tuple[str, str, int | None], CompanyValue] = {}
    for r in rows:
        out[(r.value_key, r.period_type, r.period_year)] = r
    return out


def _derive_annual(
    key: str,
    q1: CompanyValue | None,
    q2: CompanyValue | None,
    q3: CompanyValue | None,
    q4: CompanyValue | None,
    fy_row: CompanyValue | None,
) -> ValueRef:
    """Annual value is derived from the Q rows at serve-time (single source of
    truth = the Q rows). For SUMMABLE keys we sum Q1..Q4; for POINT_IN_TIME we
    take Q4. FY-row is used only as a fallback when Q rows are missing (mostly
    historical years that were backfilled at FY-granularity).
    """
    is_summable = key in SUMMABLE_QUARTERLY_KEYS
    q_rows = [q1, q2, q3, q4]

    # Case 1 — SUMMABLE and all 4 Q present: sum them.
    if is_summable and all(r is not None and r.numeric_value is not None for r in q_rows):
        total = sum((r.numeric_value for r in q_rows if r is not None and r.numeric_value is not None), Decimal("0"))
        # Adjusted-Jahressumme: sobald mindestens ein Quartal einen echten
        # Adjusted-Wert hat, Mischsumme (adjusted wenn vorhanden, sonst GAAP
        # je Quartal) — sonst zeigte die Adjusted-Annual-Spalte nach einem
        # Q-Adjusted-Override weiter die reine GAAP-Summe. Ohne jedes
        # Q-Adjusted bleibt sie NULL (GAAP-Fallback-Marker im UI).
        total_adj: Decimal | None
        if any(r is not None and r.numeric_value_adjusted is not None for r in q_rows):
            total_adj = sum(
                (
                    r.numeric_value_adjusted
                    if r.numeric_value_adjusted is not None
                    else r.numeric_value
                    for r in q_rows
                    if r is not None
                ),
                Decimal("0"),
            )
        else:
            total_adj = None
        # Build a compact source description that names the per-quarter origins.
        parts: list[str] = []
        latest_fetched: datetime | None = None
        methods: set[str] = set()
        any_forecast = False
        for q_label, r in zip(("Q1", "Q2", "Q3", "Q4"), q_rows):
            if r is None:
                continue
            method = r.primary_method or "unknown"
            methods.add(method)
            parts.append(f"{q_label}: {method}")
            if r.fetched_at and (latest_fetched is None or r.fetched_at > latest_fetched):
                latest_fetched = r.fetched_at
            if r.is_forecast:
                any_forecast = True
        method_summary = "actual" if methods == {"provider"} or methods <= {"provider", "pdf", "manual"} else "mixed"
        return ValueRef(
            value=total,
            adjusted=total_adj,
            source_name=f"Derived Annual = Q1+Q2+Q3+Q4 | " + " | ".join(parts),
            source_link=None,
            fetched_at=latest_fetched,
            manually_overridden=False,
            primary_method="calculated" if method_summary == "mixed" else "provider",
            is_forecast=any_forecast,
        )

    # Case 2 — POINT_IN_TIME and Q4 present: use Q4 as the fiscal-year-end snapshot.
    if not is_summable and q4 is not None and q4.numeric_value is not None:
        return ValueRef(
            value=q4.numeric_value,
            adjusted=q4.numeric_value_adjusted,
            source_name=f"Derived Annual = Q4 snapshot | Q4: {q4.primary_method or 'unknown'}",
            source_link=q4.source_link,
            fetched_at=q4.fetched_at,
            manually_overridden=bool(q4.manually_overridden),
            primary_method="calculated",
            is_forecast=bool(q4.is_forecast),
        )

    # Case 3 — fallback to whatever FY row we have.
    return _to_ref(fy_row)


def _build_quarterly_row(
    rows: dict[tuple[str, str, int | None], CompanyValue],
    key: str,
    year: int | None,
    fy_end_month: int | None = None,
    fy_end_day: int | None = None,
) -> QuarterlyRow:
    q1 = rows.get((key, "Q1", year))
    q2 = rows.get((key, "Q2", year))
    q3 = rows.get((key, "Q3", year))
    q4 = rows.get((key, "Q4", year))
    fy = rows.get((key, "FY", year))

    def _q_ref(quarter: str, row: CompanyValue | None) -> ValueRef:
        # Zelle ohne Zeile oder mit not_found-Zeile: wenn das Quartal noch
        # gar nicht berichtet sein kann, statt "Recherche fand nichts" den
        # Status "noch nicht berichtet" liefern. Vergangene, laengst
        # faellige Perioden bleiben not_found/leer.
        # Ein recherchierter/manueller Wert darf NIE maskiert werden — der
        # Status ersetzt nur wirklich leere Zellen (Review-Befund: Research
        # aktualisiert numeric_value, aber nicht primary_method).
        if (
            (row is None or (row.primary_method == "not_found" and row.numeric_value is None))
            and is_not_yet_reported(year, quarter, fy_end_month, fy_end_day)
        ):
            return ValueRef(status="not_yet_reported")
        return _to_ref(row)

    return QuarterlyRow(
        q1=_q_ref("Q1", q1),
        q2=_q_ref("Q2", q2),
        q3=_q_ref("Q3", q3),
        q4=_q_ref("Q4", q4),
        annual=_derive_annual(key, q1, q2, q3, q4, fy),
    )


def _get_owned_company(db: Session, user: User, company_id: UUID) -> Company:
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    portfolio = db.query(Portfolio).filter(Portfolio.id == company.portfolio_id).one_or_none()
    from app.portfolios.models import has_portfolio_access
    if not portfolio or not has_portfolio_access(db, user.id, portfolio):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company


# --- Endpoint -------------------------------------------------------------


@detail_router.get("/{company_id}/detail", response_model=CompanyDetailOut)
def get_company_detail(
    company_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CompanyDetailOut:
    company = _get_owned_company(db, user, company_id)

    current_year = _fy_estimate_year(db, company_id)
    prior_year = current_year - 1 if current_year is not None else None

    # 1) Stammdaten (SNAPSHOT)
    snapshot_rows = _load_rows(
        db, company_id,
        keys=[
            "stock_price", "shares_outstanding", "market_cap", "net_debt",
        ],
        period_types=["SNAPSHOT"],
        period_years=[],
    )
    stock_price = snapshot_rows.get(("stock_price", "SNAPSHOT", None))
    shares = snapshot_rows.get(("shares_outstanding", "SNAPSHOT", None))
    market_cap = snapshot_rows.get(("market_cap", "SNAPSHOT", None))
    net_debt_snap = snapshot_rows.get(("net_debt", "SNAPSHOT", None))

    # Enterprise Value = MC + Net Debt (falls beide vorhanden)
    ev_ref = ValueRef()
    if market_cap and net_debt_snap and market_cap.numeric_value is not None and net_debt_snap.numeric_value is not None:
        _epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        mc_ts = market_cap.fetched_at or _epoch
        nd_ts = net_debt_snap.fetched_at or _epoch
        ev_ref = ValueRef(
            value=market_cap.numeric_value + net_debt_snap.numeric_value,
            fetched_at=market_cap.fetched_at if mc_ts >= nd_ts else net_debt_snap.fetched_at,
            source_name="Calculated: Market Cap + Net Debt",
            primary_method="calculated",
        )

    stammdaten = StammdatenPack(
        stock_price=_to_ref(stock_price),
        shares_outstanding=_to_ref(shares),
        market_cap=_to_ref(market_cap),
        enterprise_value=ev_ref,
    )

    # 2) Overview metrics (FY, current year)
    defs = {d.key: d for d in db.query(ValueDefinition).all()}
    overview_rows = _load_rows(
        db, company_id,
        keys=OVERVIEW_METRIC_KEYS,
        period_types=["FY"],
        period_years=[current_year] if current_year is not None else [],
    )
    overview_metrics: list[OverviewMetric] = []
    for key in OVERVIEW_METRIC_KEYS:
        d = defs.get(key)
        if d is None:
            continue
        row = overview_rows.get((key, "FY", current_year))
        overview_metrics.append(OverviewMetric(
            value_key=key,
            label_en=d.label_en,
            label_de=d.label_de,
            unit=d.unit,
            ref=_to_ref(row),
        ))

    # 3) Quarterly sections. net_buyback braucht buyback_volume + sbc als Inputs.
    quarterly_rows = _load_rows(
        db, company_id,
        keys=QUARTERLY_DISPLAY_KEYS + ["buyback_volume", "sbc"],
        period_types=["Q1", "Q2", "Q3", "Q4", "FY"],
        period_years=[current_year, prior_year] if current_year is not None else [],
    )

    def _derived_net_buyback_row(year: int | None) -> QuarterlyRow:
        """net_buyback = buyback_volume - sbc, pro Quartal."""
        def cell_for(q: str) -> ValueRef:
            from decimal import Decimal as _D
            bv = quarterly_rows.get(("buyback_volume", q, year))
            s = quarterly_rows.get(("sbc", q, year))
            bv_val = bv.numeric_value if bv is not None else None
            s_val = s.numeric_value if s is not None else None
            # Eine belegte Komponente reicht — fehlende zaehlt als 0 (Kunden-
            # Feedback: Tabelle war leer obwohl Buybacks vorhanden).
            if bv_val is None and s_val is None:
                return ValueRef()
            _zero = _D("0")
            bv = bv or ValueRef()
            s = s or ValueRef()
            bv_num = bv_val if bv_val is not None else _zero
            s_num = s_val if s_val is not None else _zero
            bv_adj = bv.numeric_value_adjusted if getattr(bv, "numeric_value_adjusted", None) is not None else bv_num
            s_adj = s.numeric_value_adjusted if getattr(s, "numeric_value_adjusted", None) is not None else s_num
            return ValueRef(
                value=bv_num - s_num,
                adjusted=bv_adj - s_adj if bv_adj is not None and s_adj is not None else None,
                source_name=f"Derived: Buyback Volume ({bv.primary_method or '?'}) − SBC ({s.primary_method or '?'})",
                fetched_at=max(x for x in (bv.fetched_at, s.fetched_at) if x is not None) if any((bv.fetched_at, s.fetched_at)) else None,
                primary_method="calculated",
                is_forecast=bool(bv.is_forecast or s.is_forecast),
            )
        return QuarterlyRow(
            q1=cell_for("Q1"), q2=cell_for("Q2"), q3=cell_for("Q3"), q4=cell_for("Q4"),
            annual=cell_for("FY"),
        )

    quarterly: list[QuarterlySection] = []
    for key in QUARTERLY_DISPLAY_KEYS:
        d = defs.get(key)
        if d is None:
            continue
        if key == "net_buyback":
            current_row = _derived_net_buyback_row(current_year)
            prior_row = _derived_net_buyback_row(prior_year)
        else:
            current_row = _build_quarterly_row(
                quarterly_rows, key, current_year,
                company.fiscal_year_end_month, company.fiscal_year_end_day,
            )
            prior_row = _build_quarterly_row(
                quarterly_rows, key, prior_year,
                company.fiscal_year_end_month, company.fiscal_year_end_day,
            )
        quarterly.append(QuarterlySection(
            value_key=key,
            label_en=d.label_en,
            label_de=d.label_de,
            is_currency=(key in CURRENCY_KEYS),
            is_summable=(key in SUMMABLE_QUARTERLY_KEYS),
            unit=d.unit,
            current_year=current_year,
            prior_year=prior_year,
            current=current_row,
            prior=prior_row,
        ))

    # 4) Balance sheet — POINT_IN_TIME keys (net_debt, cash_and_equivalents, …).
    # estimate_fy_via_quarterly_sum persists an FY row with Q4-Endstand; falls
    # der Refresh nur die Q-Rows hat (kein FY roll-up gelaufen), fallen wir auf
    # die Q4 Row zurueck. Beides gleichwertig — es ist der Bilanzstichtag.
    bs_keys = ["cash_and_equivalents", "st_investments", "st_debt", "lt_debt", "net_debt", "market_cap"]
    bs_rows = _load_rows(
        db, company_id,
        keys=bs_keys,
        period_types=["FY", "Q4"],
        period_years=[current_year, prior_year] if current_year is not None else [],
    )

    def _bs_pair(key: str) -> tuple[ValueRef, ValueRef]:
        cy_row = bs_rows.get((key, "FY", current_year)) or bs_rows.get((key, "Q4", current_year))
        py_row = bs_rows.get((key, "FY", prior_year)) or bs_rows.get((key, "Q4", prior_year))
        return (_to_ref(cy_row), _to_ref(py_row))

    cash_c, cash_p = _bs_pair("cash_and_equivalents")
    sti_c, sti_p = _bs_pair("st_investments")
    stdebt_c, stdebt_p = _bs_pair("st_debt")
    ltdebt_c, ltdebt_p = _bs_pair("lt_debt")
    nd_c, nd_p = _bs_pair("net_debt")

    # Recompute net_debt from components (st_debt + lt_debt - cash - st_investments)
    # whenever all four components are provider-actuals — that always beats
    # a separately-fetched web_guidance estimate of net_debt.
    def _derived_net_debt(cash: ValueRef, sti: ValueRef, std: ValueRef, ltd: ValueRef) -> ValueRef | None:
        if not all(
            r is not None and r.value is not None and r.primary_method in ("provider", "pdf", "manual")
            for r in (cash, sti, std, ltd)
        ):
            return None
        derived = (std.value + ltd.value) - (cash.value + sti.value)
        return ValueRef(
            value=derived,
            source_name="Derived Net Debt = ST Debt + LT Debt − Cash − ST Investments",
            fetched_at=max(
                (r.fetched_at for r in (cash, sti, std, ltd) if r.fetched_at is not None),
                default=None,
            ),
            primary_method="calculated",
            is_forecast=False,
        )

    nd_c_derived = _derived_net_debt(cash_c, sti_c, stdebt_c, ltdebt_c)
    if nd_c_derived is not None:
        nd_c = nd_c_derived
    nd_p_derived = _derived_net_debt(cash_p, sti_p, stdebt_p, ltdebt_p)
    if nd_p_derived is not None:
        nd_p = nd_p_derived

    def _sum(vals: list[Decimal | None]) -> Decimal | None:
        nums = [v for v in vals if v is not None]
        if not nums:
            return None
        return sum(nums, Decimal("0"))

    total_cash_c = _sum([cash_c.value, sti_c.value])
    total_cash_p = _sum([cash_p.value, sti_p.value])
    sum_debt_c = _sum([stdebt_c.value, ltdebt_c.value])
    sum_debt_p = _sum([stdebt_p.value, ltdebt_p.value])

    nd_change = None
    if nd_c.value is not None and nd_p.value is not None:
        nd_change = nd_c.value - nd_p.value

    balance_sheet = BalanceSheetSection(
        current_year=current_year,
        prior_year=prior_year,
        groups=[
            BalanceSheetGroup(rows=[
                BalanceSheetRow(label="Cash", value_key="cash_and_equivalents", y1=cash_c, y2=cash_p),
                BalanceSheetRow(label="Short-Term Investments", value_key="st_investments", y1=sti_c, y2=sti_p),
                BalanceSheetRow(label="Total Cash", value_key=None, y1=ValueRef(value=total_cash_c), y2=ValueRef(value=total_cash_p), emphasis=True),
            ]),
            BalanceSheetGroup(rows=[
                BalanceSheetRow(label="ST Debt", value_key="st_debt", y1=stdebt_c, y2=stdebt_p),
                BalanceSheetRow(label="LT Debt", value_key="lt_debt", y1=ltdebt_c, y2=ltdebt_p),
                BalanceSheetRow(label="Sum Debt", value_key=None, y1=ValueRef(value=sum_debt_c), y2=ValueRef(value=sum_debt_p), emphasis=True),
            ]),
            BalanceSheetGroup(rows=[
                BalanceSheetRow(label="Net Debt", value_key="net_debt", y1=nd_c, y2=nd_p, emphasis=True),
                BalanceSheetRow(label="Change", value_key=None, y1=ValueRef(value=nd_change), y2=ValueRef()),
            ]),
        ],
    )

    return CompanyDetailOut(
        company=CompanyMetaOut(
            id=company.id,
            portfolio_id=company.portfolio_id,
            name=company.name,
            ticker=company.ticker,
            isin=company.isin,
            currency=company.currency,
            fiscal_year_end_month=company.fiscal_year_end_month,
            fiscal_year_end_day=company.fiscal_year_end_day,
            accounting_standard=accounting_standard(company.isin),
        ),
        current_fy_estimate_year=current_year,
        prior_fy_year=prior_year,
        stammdaten=stammdaten,
        overview_metrics=overview_metrics,
        quarterly=quarterly,
        balance_sheet=balance_sheet,
    )
