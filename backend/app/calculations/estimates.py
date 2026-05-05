"""Factor-based FY estimates for the running fiscal year.

When the user uploads quarterly reports (Q1..Q4) for the running FY and we
have the corresponding quarter of the previous year, we extrapolate the FY:

- FLOW keys (NI, FCF, dividends, buyback, SBC):
    factor = Σ Q_uploaded_in_target_year / Σ same_quarters_in_prev_year
    estimate = FY_prev_year * factor
  This handles seasonality automatically: comparing Q1 to Q1 (not Q1 × 4)
  preserves the YoY growth rate while respecting seasonal pattern.

- BALANCE keys (cash, debt, leases, marketable_*, shares):
    estimate = value at latest quarter end (point-in-time snapshot)
  Balance items don't extrapolate by factor — the latest snapshot is the
  best signal we have for what the year-end balance will look like.

Falls back to None when:
- No quarterly PDF in target year, OR
- Same quarter PDF missing in prev year (factor needs both sides), OR
- FY prev value missing (needed as base for flow factor).

The caller is expected to fall back to Claude-research when None is returned.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.values.models import CompanyValue


FLOW_KEYS = frozenset({
    "net_income",
    "fcf",
    "sbc",
    "buyback_volume",
    "dividends",
})

BALANCE_KEYS = frozenset({
    "cash_and_equivalents",
    "marketable_securities_st",
    "marketable_securities_lt",
    "lease_liabilities",
    "long_term_debt",
    "shares_outstanding",
})

ESTIMABLE_KEYS = FLOW_KEYS | BALANCE_KEYS

QUARTERS = ("Q1", "Q2", "Q3", "Q4")


@dataclass
class EstimateResult:
    value: Decimal
    method: str  # "flow_factor" | "balance_snapshot"
    explanation: str
    quarters_used: list[str] = field(default_factory=list)
    factor: Decimal | None = None
    components: dict = field(default_factory=dict)


def _q_value(
    db: Session,
    company_id: UUID,
    key: str,
    period_type: str,
    period_year: int,
) -> Decimal | None:
    row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == period_type,
            CompanyValue.period_year == period_year,
        )
        .one_or_none()
    )
    return row.numeric_value if row and row.numeric_value is not None else None


def compute_estimate(
    db: Session,
    company_id: UUID,
    key: str,
    target_fy_year: int,
) -> EstimateResult | None:
    """Try to estimate FY[target_fy_year] for `key` from quarterly data.
    Returns None when inputs are missing — caller should fall back to other sources."""
    if key not in ESTIMABLE_KEYS:
        return None

    prev_fy = target_fy_year - 1

    if key in BALANCE_KEYS:
        # Use latest available quarter snapshot (Q4 → Q3 → Q2 → Q1)
        for q in reversed(QUARTERS):
            v = _q_value(db, company_id, key, q, target_fy_year)
            if v is not None:
                return EstimateResult(
                    value=v,
                    method="balance_snapshot",
                    explanation=(
                        f"Bilanz-Snapshot {q} {target_fy_year} = {v:,.0f} "
                        f"(Punkt-in-Zeit-Wert; bei Bilanzposten keine Faktor-Hochrechnung)."
                    ),
                    quarters_used=[q],
                    components={"snapshot_quarter": q, "snapshot_value": str(v)},
                )
        return None

    # FLOW key
    prev_fy_val = _q_value(db, company_id, key, "FY", prev_fy)
    if prev_fy_val is None or prev_fy_val == 0:
        return None

    cum_target = Decimal("0")
    cum_prev = Decimal("0")
    qs_used: list[str] = []
    pairs: list[dict] = []
    for q in QUARTERS:
        v_target = _q_value(db, company_id, key, q, target_fy_year)
        v_prev = _q_value(db, company_id, key, q, prev_fy)
        if v_target is None or v_prev is None:
            continue
        cum_target += v_target
        cum_prev += v_prev
        qs_used.append(q)
        pairs.append({
            "quarter": q,
            "target": str(v_target),
            "prev": str(v_prev),
        })

    if not qs_used or cum_prev == 0:
        return None

    factor = cum_target / cum_prev
    estimate = prev_fy_val * factor
    qs_label = "+".join(qs_used)
    delta_pct = (factor - Decimal("1")) * Decimal("100")

    explanation = (
        f"Schätzung FY{target_fy_year} = FY{prev_fy} × Faktor.  "
        f"FY{prev_fy} ({key}) = {prev_fy_val:,.0f}.  "
        f"{qs_label} {target_fy_year} = {cum_target:,.0f}, "
        f"{qs_label} {prev_fy} = {cum_prev:,.0f}, "
        f"Faktor = {factor:.4f} ({delta_pct:+.2f} % YoY).  "
        f"Resultat = {estimate:,.0f}."
    )

    return EstimateResult(
        value=estimate,
        method="flow_factor",
        explanation=explanation,
        quarters_used=qs_used,
        factor=factor,
        components={
            "fy_prev_year": prev_fy,
            "fy_prev_value": str(prev_fy_val),
            "cum_target": str(cum_target),
            "cum_prev": str(cum_prev),
            "factor": str(factor),
            "pairs": pairs,
        },
    )
