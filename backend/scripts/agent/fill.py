"""Generalisiertes Fill-Framework fuer Agent-Research.

Aufruf:
    PYTHONPATH=/Users/till-olelohse/projekt-hohn/backend uv run python \\
        scripts/agent/fill.py --ticker AVGO

Erwartet ein Data-Modul unter `scripts/agent/companies/<ticker_lower>.py`
mit den Konstanten TICKER, Q_DATA, EPS_DATA, BS_DATA (siehe _TEMPLATE.py).

Skippt existing rows mit primary_method IN (pdf, manual). Ueberschreibt
web_guidance und calculated Rows.
"""
from __future__ import annotations

import argparse
import importlib
import logging
import sys
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from app.db import SessionLocal
from app.companies.models import Company
from app.values.models import CompanyValue

# Value-keys die auch bei GAAP == Non-GAAP ein Adj bekommen (bei diesen kann
# das Fehlen einer separaten Non-GAAP-Zahl wirklich bedeuten "gleich").
_NON_CURRENCY_KEYS = {"eps_diluted", "eps_basic"}


def _upsert_row(
    db, company_id, key, period_type, period_year,
    gaap_mio_or_raw, adj_mio_or_raw,
    source_ref, is_currency=True, force_update=False,
):
    existing = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == period_type,
            CompanyValue.period_year == period_year,
        )
        .first()
    )
    if existing and existing.primary_method in ("pdf", "manual") and not force_update:
        print(f"SKIP  {key}/{period_type}/{period_year}: existing {existing.primary_method}")
        return
    if existing and getattr(existing, "manually_overridden", False) and not force_update:
        print(f"SKIP  {key}/{period_type}/{period_year}: manually_overridden")
        return
    if gaap_mio_or_raw is None:
        print(f"SKIP  {key}/{period_type}/{period_year}: no value")
        return

    if is_currency:
        gaap_base = Decimal(str(gaap_mio_or_raw)) * Decimal("1000000")
        adj_base = (
            Decimal(str(adj_mio_or_raw)) * Decimal("1000000")
            if adj_mio_or_raw is not None else None
        )
    else:
        gaap_base = Decimal(str(gaap_mio_or_raw))
        adj_base = Decimal(str(adj_mio_or_raw)) if adj_mio_or_raw is not None else None

    # Wenn Adj == GAAP: leer lassen (Frontend-Fallback zeigt GAAP-Wert in adj-Spalte)
    adj_final = adj_base if (adj_base is not None and adj_base != gaap_base) else None

    now = datetime.now(timezone.utc)
    source_name = f"Agent-Research: {source_ref}"[:4000]
    currency = "USD" if is_currency else None

    if existing:
        existing.numeric_value = gaap_base
        existing.numeric_value_adjusted = adj_final
        existing.source_name = source_name
        existing.fetched_at = now
        existing.is_forecast = False
        existing.primary_method = "manual"
        existing.currency = currency
        print(f"UPD   {key}/{period_type}/{period_year} = {gaap_mio_or_raw} / adj={adj_mio_or_raw}")
    else:
        cv = CompanyValue(
            id=uuid4(),
            company_id=company_id,
            value_key=key,
            period_type=period_type,
            period_year=period_year,
            numeric_value=gaap_base,
            numeric_value_adjusted=adj_final,
            source_name=source_name,
            currency=currency,
            fetched_at=now,
            is_forecast=False,
            primary_method="manual",
        )
        db.add(cv)
        print(f"INS   {key}/{period_type}/{period_year} = {gaap_mio_or_raw} / adj={adj_mio_or_raw}")


def _sanity_check(module) -> list[str]:
    """Basic Konsistenz-Checks vor dem Commit."""
    errors: list[str] = []
    summable_keys = {"revenue", "net_income", "operating_cash_flow", "capex",
                     "fcf", "sbc", "dividends", "buyback_volume"}

    # Q1-Q4 sum == FY (per year)
    per_year: dict[int, dict[str, dict]] = {}
    for pt, py, src, data in module.Q_DATA:
        per_year.setdefault(py, {})[pt] = data

    for py, quarters in per_year.items():
        if "FY" not in quarters:
            continue
        for key in summable_keys:
            q_vals = [quarters[q][key][0] for q in ("Q1", "Q2", "Q3", "Q4")
                      if q in quarters and key in quarters[q]]
            if len(q_vals) != 4:
                continue
            q_sum = sum(q_vals)
            fy_val = quarters["FY"].get(key, (None,))[0]
            if fy_val is None:
                continue
            rel_diff = abs(q_sum - fy_val) / abs(fy_val) if fy_val != 0 else 0
            if rel_diff > 0.005:
                errors.append(
                    f"{module.TICKER} FY{py} {key}: Sum(Q1-Q4)={q_sum} != FY={fy_val} "
                    f"(rel_diff={rel_diff:.2%})"
                )

    # FCF == OCF - CapEx per quarter
    for pt, py, src, data in module.Q_DATA:
        if not all(k in data for k in ("operating_cash_flow", "capex", "fcf")):
            continue
        ocf = data["operating_cash_flow"][0]
        capex = data["capex"][0]
        fcf = data["fcf"][0]
        expected = ocf - capex
        if abs(expected - fcf) > max(1, abs(fcf) * 0.005):
            errors.append(
                f"{module.TICKER} FY{py} {pt} FCF: OCF({ocf}) - CapEx({capex}) = {expected} "
                f"vs reported FCF={fcf}"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True, help="Ticker (z.B. AVGO)")
    parser.add_argument("--skip-sanity", action="store_true", help="Sanity-Check ueberspringen")
    parser.add_argument("--dry-run", action="store_true", help="Nur zeigen, nicht commit")
    parser.add_argument("--force-update", action="store_true",
                        help="Ueberschreibe existing manual/pdf Rows (Corrections)")
    args = parser.parse_args()

    ticker = args.ticker.upper()
    # Punkt in Xetra-Ticker (SAP.DE) durch Underscore fuer valid Python-Modul-Name
    module_slug = ticker.lower().replace(".", "_")
    module_name = f"scripts.agent.companies.{module_slug}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        print(f"FEHLER: Data-Modul {module_name} nicht gefunden.")
        print(f"Erstelle backend/scripts/agent/companies/{ticker.lower()}.py")
        print(f"Template: siehe _TEMPLATE.py")
        return 1

    # Sanity-Check
    if not args.skip_sanity:
        errors = _sanity_check(module)
        if errors:
            print("SANITY-CHECK FEHLGESCHLAGEN:")
            for e in errors:
                print(f"  - {e}")
            print("Nutze --skip-sanity um trotzdem zu committen, oder korrigiere die Daten.")
            return 2

    db = SessionLocal()
    try:
        # Falls Ticker in mehreren Portfolios existiert, bevorzuge das DAX-Portfolio
        DAX_PORTFOLIO_ID = "b3a10032-c646-4036-97eb-ee72331ae423"
        companies = db.query(Company).filter(Company.ticker == ticker).all()
        if not companies:
            print(f"Company {ticker} nicht in DB.")
            return 1
        if len(companies) > 1:
            dax_match = [c for c in companies if str(c.portfolio_id) == DAX_PORTFOLIO_ID]
            company = dax_match[0] if dax_match else companies[0]
            print(f"Note: {ticker} existiert {len(companies)}x — nehme portfolio_id={company.portfolio_id}")
        else:
            company = companies[0]
        cid = company.id
        print(f"Fill {ticker} ({company.name}), fiscal_year_end_month={company.fiscal_year_end_month}")

        # Currency-Keys (alle ausser EPS)
        for pt, py, src, data in module.Q_DATA:
            for key, (gaap, adj) in data.items():
                _upsert_row(db, cid, key, pt, py, gaap, adj, src, is_currency=True, force_update=args.force_update)

        # EPS
        for pt, py, (gaap, adj), src in module.EPS_DATA:
            _upsert_row(db, cid, "eps_diluted", pt, py, gaap, adj, src, is_currency=False, force_update=args.force_update)

        # Balance Sheet FY-Snapshots
        for year, bs in module.BS_DATA.items():
            for key, (val, src) in bs.items():
                _upsert_row(db, cid, key, "FY", year, val, val, src, is_currency=True, force_update=args.force_update)

        if args.dry_run:
            print("DRY-RUN — rollback")
            db.rollback()
        else:
            db.commit()
            print("commit done")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
