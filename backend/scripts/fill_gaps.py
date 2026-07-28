"""Gap-Fill: recherchiert gezielt (Firma, Key, Jahr)-Kombinationen nach,
bei denen Perioden-Zeilen fehlen — durch die normale Two-Stage-Pipeline
(inkl. Median-Sampling, Verifier, Sign-/Currency-Invarianten). Danach
net_debt-Ableitung + Konsistenz-Pass + Recalc pro betroffener Firma.

Usage (im App-Container):
    .venv/bin/python -m scripts.fill_gaps --portfolio DAX [--ticker ADS.DE] [--dry-run]
"""

import argparse
import sys

from app.companies.models import Company
from app.db import SessionLocal
from app.portfolios.models import Portfolio
from app.values.models import CompanyValue, SourceType, ValueDefinition
from scripts.two_stage_research import apply_to_db, research_two_stage

PERIODS = ("Q1", "Q2", "Q3", "Q4", "FY")
ALWAYS_CURRENT = {"market_cap", "stock_price", "shares_outstanding"}
# Banken/Versicherer haben kein EBITDA-Konzept — Luecke ist dort korrekt.
FINANCIALS = {"DBK.DE", "CBK.DE", "ALV.DE", "MUV2.DE", "HNR1.DE"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--portfolio", default="DAX")
    ap.add_argument("--ticker", default=None, help="nur diese Firma")
    ap.add_argument("--years", default="2025,2026")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    years = [int(y) for y in args.years.split(",")]

    db = SessionLocal()
    try:
        pf = db.query(Portfolio).filter(Portfolio.name == args.portfolio).one()
        companies = (
            db.query(Company).filter(Company.portfolio_id == pf.id).order_by(Company.ticker).all()
        )
        if args.ticker:
            companies = [c for c in companies if c.ticker == args.ticker]
        api_keys = [
            vd.key for vd in db.query(ValueDefinition)
            .filter(ValueDefinition.source_type == SourceType.API)
            .order_by(ValueDefinition.sort_order)
            if vd.key not in ALWAYS_CURRENT
        ]

        todo = []
        for c in companies:
            for key in api_keys:
                if key == "ebitda" and c.ticker in FINANCIALS:
                    continue
                for year in years:
                    present = {
                        row.period_type
                        for row in db.query(CompanyValue.period_type)
                        .filter(
                            CompanyValue.company_id == c.id,
                            CompanyValue.value_key == key,
                            CompanyValue.period_year == year,
                        )
                    }
                    missing = [p for p in PERIODS if p not in present]
                    if missing:
                        todo.append((c, key, year, missing))

        print(f"gap-fill: {len(todo)} (company,key,year) combos with missing periods", flush=True)
        if args.dry_run:
            for c, key, year, missing in todo:
                print(f"  {c.ticker} {key} {year}: missing {','.join(missing)}", flush=True)
            return 0

        touched_companies = {}
        ok = fail = 0
        for i, (c, key, year, missing) in enumerate(todo, 1):
            prev = (
                db.query(CompanyValue)
                .filter(
                    CompanyValue.company_id == c.id,
                    CompanyValue.value_key == key,
                    CompanyValue.period_year == year - 1,
                    CompanyValue.period_type == "FY",
                )
                .one_or_none()
            )
            try:
                result = research_two_stage(
                    ticker=c.ticker, company_name=c.name, value_key=key, year=year,
                    currency=c.currency, mode="historic", quarter=None,
                    prev_year_fy_hint=prev.numeric_value if prev else None,
                )
                written = apply_to_db(db, c.id, key, year, result, currency=c.currency)
                db.commit()
                touched_companies[c.id] = c
                ok += 1
                print(f"[{i}/{len(todo)}] {c.ticker} {key} {year}: wrote {len(written)} rows "
                      f"(was missing {','.join(missing)})", flush=True)
            except Exception as e:
                db.rollback()
                fail += 1
                print(f"[{i}/{len(todo)}] {c.ticker} {key} {year} FAILED: {str(e)[:150]}", flush=True)

        # Nachlauf pro beruehrter Firma: net_debt-Ableitung, Konsistenz, Recalc.
        from app.values.consistency import (
            derive_missing_ocf,
            derive_net_debt_from_components,
            derive_sbc_quarters,
            validate_cross_metrics,
        )
        from app.values.routes import _run_and_persist_calculations

        for c in touched_companies.values():
            try:
                for year in years:
                    derive_net_debt_from_components(db, c.id, year)
                    derive_missing_ocf(db, c.id, year)
                    derive_sbc_quarters(db, c.id, year)
                    validate_cross_metrics(db, c.id, year)
                    _run_and_persist_calculations(db, c.id, "FY", year)
                db.commit()
                print(f"postprocess {c.ticker}: ok", flush=True)
            except Exception as e:
                db.rollback()
                print(f"postprocess {c.ticker} FAILED: {str(e)[:150]}", flush=True)

        print(f"gap-fill done: {ok} ok, {fail} failed", flush=True)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
