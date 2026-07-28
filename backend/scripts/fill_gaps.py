"""Gap-Fill: recherchiert gezielt (Firma, Key, Jahr)-Kombinationen nach,
bei denen Perioden-Zeilen fehlen — durch die normale Two-Stage-Pipeline
(inkl. Median-Sampling, Verifier, Sign-/Currency-Invarianten). Danach
net_debt-Ableitung + Konsistenz-Pass + Recalc pro betroffener Firma.

Kernlogik ist importierbar (fill_portfolio_gaps, write_not_found_placeholders,
build_completeness_report) und wird auch vom Portfolio-Batch
(app/values/batch.py) als Abschlussphase genutzt.

Usage (im App-Container):
    .venv/bin/python -m scripts.fill_gaps --portfolio DAX [--ticker ADS.DE] [--dry-run]
"""

import argparse
import sys
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.companies.models import Company
from app.db import SessionLocal
from app.portfolios.models import Portfolio
from app.values.models import CompanyValue, SourceType, ValueDefinition
from app.values.persistence import NOT_FOUND_SOURCE
from scripts.two_stage_research import apply_to_db, research_two_stage

PERIODS = ("Q1", "Q2", "Q3", "Q4", "FY")
ALWAYS_CURRENT = {"market_cap", "stock_price", "shares_outstanding"}
# Banken/Versicherer haben kein EBITDA-Konzept — Luecke ist dort korrekt.
FINANCIALS = {"DBK.DE", "CBK.DE", "ALV.DE", "MUV2.DE", "HNR1.DE"}


def _is_structurally_empty(ticker: str, key: str) -> bool:
    """Kombos, deren Fehlen fachlich korrekt ist (kein Platzhalter noetig)."""
    return key == "ebitda" and ticker in FINANCIALS


def _portfolio_companies(db: Session, portfolio_id: UUID, ticker: str | None = None) -> list[Company]:
    companies = (
        db.query(Company)
        .filter(Company.portfolio_id == portfolio_id)
        .order_by(Company.ticker)
        .all()
    )
    if ticker:
        companies = [c for c in companies if c.ticker == ticker]
    return companies


def _expected_api_keys(db: Session) -> list[str]:
    """Erwartungsraster: API-Keys ohne die Snapshot-Keys (immer aktuell)."""
    return [
        vd.key for vd in db.query(ValueDefinition)
        .filter(ValueDefinition.source_type == SourceType.API)
        .order_by(ValueDefinition.sort_order)
        if vd.key not in ALWAYS_CURRENT
    ]


def collect_missing_combos(
    db: Session, portfolio_id: UUID, years: list[int], ticker: str | None = None
) -> list[tuple[Company, str, int, list[str]]]:
    """(Firma, Key, Jahr, fehlende Perioden) fuer alle Kombos mit Luecken."""
    companies = _portfolio_companies(db, portfolio_id, ticker)
    api_keys = _expected_api_keys(db)

    todo = []
    for c in companies:
        for key in api_keys:
            if _is_structurally_empty(c.ticker, key):
                continue
            for year in years:
                # not_found-Platzhalter (numeric_value NULL) zaehlen NICHT
                # als vorhanden — sonst wuerde ein spaeterer Batch die Zelle
                # nie nachrecherchieren. Erfolgreiche Recherche ueberschreibt
                # den Platzhalter ohnehin (Insert-or-Update in apply_to_db).
                present = {
                    row.period_type
                    for row in db.query(
                        CompanyValue.period_type,
                        CompanyValue.numeric_value,
                        CompanyValue.primary_method,
                    )
                    .filter(
                        CompanyValue.company_id == c.id,
                        CompanyValue.value_key == key,
                        CompanyValue.period_year == year,
                    )
                    if not (row.numeric_value is None and row.primary_method == "not_found")
                }
                missing = [p for p in PERIODS if p not in present]
                if missing:
                    todo.append((c, key, year, missing))
    return todo


def fill_portfolio_gaps(
    db: Session,
    portfolio_id: UUID,
    years: list[int],
    ticker: str | None = None,
    progress_cb: Callable[[str], None] | None = None,
    cost_tracker=None,
) -> dict:
    """Recherchiert alle luecken-behafteten Kombos nach und laesst danach
    Ableitungen + Recalc pro beruehrter Firma laufen.

    Rueckgabe-Statistik:
      combos  — Kombos mit fehlenden Perioden (Arbeitsvorrat)
      filled  — Recherche erfolgreich, mindestens eine Zeile geschrieben
      empty   — Recherche erfolgreich, aber keine Zeile geschrieben
      failed  — Recherche/Persistenz mit Fehler
    """
    def _log(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    todo = collect_missing_combos(db, portfolio_id, years, ticker)
    _log(f"gap-fill: {len(todo)} (company,key,year) combos with missing periods")

    touched_companies: dict[UUID, Company] = {}
    filled = empty = failed = 0
    for i, (c, key, year, missing) in enumerate(todo, 1):
        try:
            # Im per-Combo-try UND first() statt one_or_none(): ein Actual+
            # Forecast-Paar fuer FY N-1 darf nicht die ganze Gap-Fill-Phase
            # abbrechen. Actual-Zeile bevorzugen.
            prev = (
                db.query(CompanyValue)
                .filter(
                    CompanyValue.company_id == c.id,
                    CompanyValue.value_key == key,
                    CompanyValue.period_year == year - 1,
                    CompanyValue.period_type == "FY",
                )
                .order_by(CompanyValue.is_forecast.asc())
                .first()
            )
            result = research_two_stage(
                ticker=c.ticker, company_name=c.name, value_key=key, year=year,
                currency=c.currency, mode="historic", quarter=None,
                prev_year_fy_hint=prev.numeric_value if prev else None,
            )
            written = apply_to_db(db, c.id, key, year, result, currency=c.currency)
            db.commit()
            touched_companies[c.id] = c
            if written:
                filled += 1
            else:
                empty += 1
            _log(f"[{i}/{len(todo)}] {c.ticker} {key} {year}: wrote {len(written)} rows "
                 f"(was missing {','.join(missing)})")
        except Exception as e:
            db.rollback()
            failed += 1
            _log(f"[{i}/{len(todo)}] {c.ticker} {key} {year} FAILED: {str(e)[:150]}")

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
            _log(f"postprocess {c.ticker}: ok")
        except Exception as e:
            db.rollback()
            _log(f"postprocess {c.ticker} FAILED: {str(e)[:150]}")

    _log(f"gap-fill done: {filled + empty} ok, {failed} failed")
    return {"combos": len(todo), "filled": filled, "empty": empty, "failed": failed}


def write_not_found_placeholders(db: Session, portfolio_id: UUID, years: list[int]) -> int:
    """Legt fuer alle nach Gap-Fill + Ableitungen weiterhin komplett fehlenden
    erwarteten Zellen (keine Zeile vorhanden) not_found-Platzhalter an —
    rote "manuell recherchieren"-Marker im UI. Strukturell legitim leere
    Kombos (Banken-EBITDA) werden uebersprungen. Committet selbst.
    """
    companies = _portfolio_companies(db, portfolio_id)
    api_keys = _expected_api_keys(db)
    now = datetime.now(timezone.utc)

    created = 0
    for c in companies:
        existing = {
            (row.value_key, row.period_year, row.period_type)
            for row in db.query(
                CompanyValue.value_key, CompanyValue.period_year, CompanyValue.period_type
            ).filter(
                CompanyValue.company_id == c.id,
                CompanyValue.value_key.in_(api_keys),
                CompanyValue.period_year.in_(years),
            )
        }
        for key in api_keys:
            if _is_structurally_empty(c.ticker, key):
                continue
            for year in years:
                for period in PERIODS:
                    if (key, year, period) in existing:
                        continue
                    db.add(CompanyValue(
                        company_id=c.id,
                        value_key=key,
                        period_year=year,
                        period_type=period,
                        numeric_value=None,
                        primary_method="not_found",
                        source_name=NOT_FOUND_SOURCE,
                        currency=c.currency,
                        fetched_at=now,
                        last_refresh_attempt=now,
                    ))
                    created += 1
    db.commit()
    return created


def build_completeness_report(db: Session, portfolio_id: UUID, years: list[int]) -> dict:
    """Vollstaendigkeits-Report pro value_key ueber das Erwartungsraster:
      expected   — erwartete Zellen (ohne strukturell ausgenommene)
      with_value — Zeile mit numeric_value vorhanden
      not_found  — not_found-Platzhalter (manuell nachrecherchieren)
      excluded   — strukturell ausgenommen (Banken-EBITDA)
    """
    companies = _portfolio_companies(db, portfolio_id)
    api_keys = _expected_api_keys(db)
    company_ids = [c.id for c in companies]

    # Beste Zeile pro Zelle: wert-tragende Zeile gewinnt vor Platzhalter.
    by_cell: dict[tuple, CompanyValue] = {}
    if company_ids:
        rows = (
            db.query(CompanyValue)
            .filter(
                CompanyValue.company_id.in_(company_ids),
                CompanyValue.value_key.in_(api_keys),
                CompanyValue.period_year.in_(years),
                CompanyValue.period_type.in_(PERIODS),
            )
            .all()
        )
        for row in rows:
            cell = (row.company_id, row.value_key, row.period_year, row.period_type)
            if cell not in by_cell or by_cell[cell].numeric_value is None:
                by_cell[cell] = row

    per_key = {
        key: {"expected": 0, "with_value": 0, "not_found": 0, "excluded": 0}
        for key in api_keys
    }
    for c in companies:
        for key in api_keys:
            stats = per_key[key]
            for year in years:
                for period in PERIODS:
                    if _is_structurally_empty(c.ticker, key):
                        stats["excluded"] += 1
                        continue
                    stats["expected"] += 1
                    row = by_cell.get((c.id, key, year, period))
                    if row is None:
                        continue
                    if row.numeric_value is not None:
                        stats["with_value"] += 1
                    elif row.primary_method == "not_found":
                        stats["not_found"] += 1
    return {"years": list(years), "per_key": per_key}


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
        if args.dry_run:
            todo = collect_missing_combos(db, pf.id, years, args.ticker)
            print(f"gap-fill: {len(todo)} (company,key,year) combos with missing periods",
                  flush=True)
            for c, key, year, missing in todo:
                print(f"  {c.ticker} {key} {year}: missing {','.join(missing)}", flush=True)
            return 0

        fill_portfolio_gaps(
            db, pf.id, years, ticker=args.ticker,
            progress_cb=lambda msg: print(msg, flush=True),
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
