"""Portfolio-wide batch runner for the two-stage research pipeline.

Iterates every company in a portfolio × every value_key × every requested
year, runs Extractor + Verifier, and (optionally) writes verifier-final
values into the DB.

Skips (company, key, year) combinations that already have a
primary_method starting with "two_stage_" AND fetched_at younger than
--skip-if-younger-than-days.

Usage:
    uv run python -m scripts.run_two_stage_portfolio \\
        --portfolio-id b3a10032-c646-4036-97eb-ee72331ae423 \\
        --years 2025 \\
        --keys revenue,net_income,ebitda,dividends \\
        --apply-to-db \\
        --max-cost-usd 25
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from app.db import SessionLocal
from app.companies.models import Company
from app.values.models import CompanyValue
from scripts.two_stage_research import (
    CostTracker,
    apply_to_db,
    choose_mode_for_year,
    research_two_stage,
)


DEFAULT_KEYS = [
    "revenue", "net_income", "ebitda", "fcf", "operating_cash_flow", "capex",
    "sbc", "buyback_volume", "dividends", "cash_and_equivalents", "st_investments",
    "st_debt", "lt_debt", "net_debt",
]


def _skip_check(
    db, company_id: UUID, key: str, year: int, threshold: datetime | None
) -> bool:
    if threshold is None:
        return False
    stmt = select(CompanyValue).where(
        CompanyValue.company_id == company_id,
        CompanyValue.value_key == key,
        CompanyValue.period_year == year,
        CompanyValue.period_type == "FY",
    )
    row = db.execute(stmt).scalar_one_or_none()
    if not row or not row.primary_method:
        return False
    return (
        row.primary_method.startswith("two_stage_")
        and row.fetched_at is not None
        and row.fetched_at > threshold
    )


def _prev_year_fy(db, company_id: UUID, key: str, year: int) -> Decimal | None:
    stmt = select(CompanyValue).where(
        CompanyValue.company_id == company_id,
        CompanyValue.value_key == key,
        CompanyValue.period_year == year - 1,
        CompanyValue.period_type == "FY",
    )
    row = db.execute(stmt).scalar_one_or_none()
    return row.numeric_value if row else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Two-stage portfolio batch runner.")
    ap.add_argument("--portfolio-id", required=True)
    ap.add_argument("--years", required=True, help="Comma-separated: 2024,2025")
    ap.add_argument("--keys", default=",".join(DEFAULT_KEYS))
    ap.add_argument("--ticker", default=None, help="Filter to a single ticker for testing")
    ap.add_argument("--apply-to-db", action="store_true")
    ap.add_argument("--max-cost-usd", type=float, default=None)
    ap.add_argument("--skip-if-younger-than-days", type=int, default=7)
    ap.add_argument("--extractor-model", default="claude-sonnet-4-6")
    ap.add_argument("--verifier-model", default=os.environ.get("VERIFIER_MODEL", "claude-haiku-4-5-20251001"))
    ap.add_argument("--jsonl-out", default=None, help="Path to write per-value JSONL log")
    args = ap.parse_args()

    years = [int(y.strip()) for y in args.years.split(",") if y.strip()]
    keys = [k.strip() for k in args.keys.split(",") if k.strip()]
    portfolio_id = UUID(args.portfolio_id)

    skip_threshold = None
    if args.skip_if_younger_than_days > 0:
        skip_threshold = datetime.now(timezone.utc) - timedelta(days=args.skip_if_younger_than_days)

    tracker = CostTracker(max_usd=args.max_cost_usd)

    db = SessionLocal()
    try:
        q = select(Company).where(Company.portfolio_id == portfolio_id).order_by(Company.name)
        companies = list(db.execute(q).scalars())
        if args.ticker:
            companies = [c for c in companies if c.ticker == args.ticker]

        total = len(companies) * len(keys) * len(years)
        print(f"Total combinations: {total} ({len(companies)} companies × {len(keys)} keys × {len(years)} years)", file=sys.stderr)

        jsonl_fp = open(args.jsonl_out, "a") if args.jsonl_out else None
        done = 0
        skipped = 0
        failed = 0
        for company in companies:
            for year in years:
                for key in keys:
                    done += 1
                    prefix = f"[{done}/{total}] {company.ticker} {key} {year}"

                    if _skip_check(db, company.id, key, year, skip_threshold):
                        skipped += 1
                        print(f"{prefix} SKIP (already verified within {args.skip_if_younger_than_days}d)", file=sys.stderr)
                        continue

                    try:
                        tracker.check_budget()
                    except RuntimeError as e:
                        print(f"{prefix} BUDGET STOP: {e}", file=sys.stderr)
                        return 3

                    try:
                        prev_fy = _prev_year_fy(db, company.id, key, year)
                        mode = choose_mode_for_year(year)
                        result = research_two_stage(
                            ticker=company.ticker,
                            company_name=company.name,
                            value_key=key,
                            year=year,
                            currency=company.currency,
                            mode=mode,
                            quarter=None,
                            prev_year_fy_hint=prev_fy,
                            extractor_model=args.extractor_model,
                            verifier_model=args.verifier_model,
                            cost_tracker=tracker,
                        )
                        verdict = result.verdict.verdict
                        finals = {k: str(v) for k, v in result.final_values.items() if v is not None}
                        print(f"{prefix} {verdict:<25} {json.dumps(finals)}", file=sys.stderr)

                        if jsonl_fp:
                            jsonl_fp.write(json.dumps({
                                "ticker": company.ticker,
                                "value_key": key,
                                "year": year,
                                "mode": mode,
                                "verdict": verdict,
                                "flags": result.verdict.flags,
                                "confidence": result.verdict.confidence,
                                "final_values": finals,
                                "cost_running_usd": round(tracker.spent_usd, 3),
                            }, default=str) + "\n")
                            jsonl_fp.flush()

                        if args.apply_to_db:
                            apply_to_db(db, company.id, key, year, result, currency=company.currency)
                            db.commit()
                    except Exception as e:
                        failed += 1
                        db.rollback()
                        print(f"{prefix} FAIL: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)

        print(
            f"\nDone: {done} total, skipped={skipped}, failed={failed}, "
            f"spent=${tracker.spent_usd:.2f}, calls={tracker.calls}",
            file=sys.stderr,
        )
        if jsonl_fp:
            jsonl_fp.close()
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
