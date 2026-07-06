"""Detail-page aggregation endpoint.

One request → everything the frontend needs to render the company detail
page (overview stammdaten, overview metrics, all quarterly sections, and
the balance-sheet card). Reads only what is already persisted in the DB;
does not trigger the refresh pipeline (that stays behind the explicit
refresh endpoints).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.models import User
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


def _build_quarterly_row(
    rows: dict[tuple[str, str, int | None], CompanyValue],
    key: str,
    year: int | None,
) -> QuarterlyRow:
    return QuarterlyRow(
        q1=_to_ref(rows.get((key, "Q1", year))),
        q2=_to_ref(rows.get((key, "Q2", year))),
        q3=_to_ref(rows.get((key, "Q3", year))),
        q4=_to_ref(rows.get((key, "Q4", year))),
        annual=_to_ref(rows.get((key, "FY", year))),
    )


def _get_owned_company(db: Session, user: User, company_id: UUID) -> Company:
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    portfolio = db.query(Portfolio).filter(Portfolio.id == company.portfolio_id).one_or_none()
    if not portfolio or portfolio.owner_user_id != user.id:
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

    # 3) Quarterly sections
    quarterly_rows = _load_rows(
        db, company_id,
        keys=QUARTERLY_DISPLAY_KEYS,
        period_types=["Q1", "Q2", "Q3", "Q4", "FY"],
        period_years=[current_year, prior_year] if current_year is not None else [],
    )
    quarterly: list[QuarterlySection] = []
    for key in QUARTERLY_DISPLAY_KEYS:
        d = defs.get(key)
        if d is None:
            continue
        quarterly.append(QuarterlySection(
            value_key=key,
            label_en=d.label_en,
            label_de=d.label_de,
            is_currency=(key in CURRENCY_KEYS),
            is_summable=(key in SUMMABLE_QUARTERLY_KEYS),
            unit=d.unit,
            current_year=current_year,
            prior_year=prior_year,
            current=_build_quarterly_row(quarterly_rows, key, current_year),
            prior=_build_quarterly_row(quarterly_rows, key, prior_year),
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
        ),
        current_fy_estimate_year=current_year,
        prior_fy_year=prior_year,
        stammdaten=stammdaten,
        overview_metrics=overview_metrics,
        quarterly=quarterly,
        balance_sheet=balance_sheet,
    )
