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

from app.ir_documents.models import ExtractionStatus, IRDocument, PeriodCoverage
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

ESTIMABLE_KEYS = frozenset(FLOW_KEYS | BALANCE_KEYS)

QUARTERS = ("Q1", "Q2", "Q3", "Q4")

# Threshold below which we refuse to compute a flow factor — denominator too
# small means a tiny absolute change blows the factor up to absurd magnitudes.
# Picked relative to the FY base value: if previous-year cumulative quarters
# are less than 1 % of the FY value, the comparison signal is too weak.
_NEAR_ZERO_FRACTION = Decimal("0.01")


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


def _period_basis(
    db: Session,
    company_id: UUID,
    key: str,
    quarter: str,
    period_year: int,
) -> str | None:
    """Look up period_basis (YTD vs standalone) from the IRDocument that
    produced this quarter's value. Returns None if no PDF backed it."""
    try:
        doc = (
            db.query(IRDocument)
            .filter(
                IRDocument.company_id == company_id,
                IRDocument.period_year == period_year,
                IRDocument.period_coverage == PeriodCoverage(quarter),
                IRDocument.extraction_status == ExtractionStatus.DONE,
            )
            .order_by(IRDocument.uploaded_at.desc())
            .first()
        )
    except (ValueError, KeyError):
        return None
    if doc is None or not doc.extraction_results:
        return None
    entry = doc.extraction_results.get(key)
    if not isinstance(entry, dict):
        return None
    return entry.get("period_basis")


def _gather_flow_cumulative(
    db: Session,
    company_id: UUID,
    key: str,
    period_year: int,
) -> tuple[Decimal | None, list[str], bool]:
    """Combine quarterly flow values into a cumulative figure for `period_year`.

    Returns (value, quarters_used_label, used_ytd). YTD vs standalone is
    disambiguated via the IRDocument's `period_basis` metadata so we don't
    double-count Q1+Q2+Q3 when Q3 itself is reported YTD.

    Strategy: if ANY quarter has a YTD-style period_basis, we use the
    highest-Q YTD value as the entire cumulative (it already includes
    earlier quarters). Otherwise we sum standalone quarter values.
    """
    ytd_entries: list[tuple[str, Decimal]] = []
    standalone_entries: list[tuple[str, Decimal]] = []
    for q in QUARTERS:
        v = _q_value(db, company_id, key, q, period_year)
        if v is None:
            continue
        basis = _period_basis(db, company_id, key, q, period_year)
        is_ytd = bool(basis and "YTD" in basis.upper())
        if is_ytd:
            ytd_entries.append((q, v))
        else:
            standalone_entries.append((q, v))

    if ytd_entries:
        last_q, last_v = ytd_entries[-1]
        return last_v, [f"{last_q} (YTD)"], True
    if standalone_entries:
        total = sum((v for _, v in standalone_entries), Decimal("0"))
        return total, [q for q, _ in standalone_entries], False
    return None, [], False


def compute_estimate(
    db: Session,
    company_id: UUID,
    key: str,
    target_fy_year: int,
) -> EstimateResult | None:
    """Try to estimate FY[target_fy_year] for `key` from quarterly data.
    Returns None when inputs are missing or the signal is too noisy
    (sign flip, near-zero denominator) — caller should fall back to other sources."""
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

    cum_target, qs_target, ytd_t = _gather_flow_cumulative(db, company_id, key, target_fy_year)
    cum_prev, qs_prev, ytd_p = _gather_flow_cumulative(db, company_id, key, prev_fy)

    if cum_target is None or cum_prev is None:
        return None
    if cum_prev == 0:
        return None

    # Sanity gates: refuse to extrapolate when the signal is unreliable.
    # 1) Sign flip — a Q1 swinging from negative to positive (or vice-versa)
    #    multiplied by a same-sign FY base produces a result with the wrong
    #    sign. Better to fall back to Claude than emit garbage.
    if (cum_target * cum_prev) < 0:
        return None
    # 2) Near-zero denominator — tiny prev-period denominator inflates the
    #    factor disproportionately. Threshold relative to FY base value.
    if abs(cum_prev) < abs(prev_fy_val) * _NEAR_ZERO_FRACTION:
        return None

    factor = cum_target / cum_prev
    estimate = prev_fy_val * factor
    qs_label = "+".join(qs_target) if qs_target == qs_prev else f"{'+'.join(qs_target)} vs {'+'.join(qs_prev)}"
    delta_pct = (factor - Decimal("1")) * Decimal("100")
    ytd_note = " (YTD)" if (ytd_t or ytd_p) else ""

    pairs = [{"quarter": q, "target": "cumulative", "prev": "cumulative"} for q in qs_target]

    explanation = (
        f"Schätzung FY{target_fy_year} = FY{prev_fy} × Faktor.{ytd_note}  "
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
        quarters_used=qs_target,
        factor=factor,
        components={
            "fy_prev_year": prev_fy,
            "fy_prev_value": str(prev_fy_val),
            "cum_target": str(cum_target),
            "cum_prev": str(cum_prev),
            "factor": str(factor),
            "pairs": pairs,
            "used_ytd": ytd_t or ytd_p,
        },
    )
