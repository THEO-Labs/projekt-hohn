"""Gap-Inventar + not_found-Platzhalter + Vollstaendigkeits-Report.

Die fruehere LLM-Nachrecherche (fill_portfolio_gaps via Two-Stage-
Pipeline) ist entfernt — Luecken fuellen die neuen Refresh-Fluesse
(EDGAR/8-K-Bruecke bzw. statement_research). Dieses Modul inventarisiert
nur noch: collect_missing_combos (Luecken-Liste), write_not_found_placeholders
(rote Zellen im UI) und build_completeness_report — beide werden vom
Portfolio-Batch (app/values/batch.py) als Abschlussphase genutzt.

Usage (im App-Container):
    .venv/bin/python -m scripts.fill_gaps --portfolio DAX [--ticker ADS.DE] [--dry-run]
"""

import argparse
import sys
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.companies.models import Company
from app.db import SessionLocal
from app.portfolios.models import Portfolio
from app.values.models import CompanyValue, SourceType, ValueDefinition
from app.values.persistence import NOT_FOUND_SOURCE

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
                # als vorhanden — sonst wuerde ein spaeterer Refresh die
                # Zelle nie nachrecherchieren. Erfolgreiche Recherche
                # ueberschreibt den Platzhalter ohnehin.
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
                    placeholder = CompanyValue(
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
                    )
                    # SAVEPOINT pro Insert: Unique-Index-Kollision (Race mit
                    # parallelem Writer) -> Zeile ueberspringen.
                    try:
                        with db.begin_nested():
                            db.add(placeholder)
                            db.flush()
                    except IntegrityError:
                        continue
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
        todo = collect_missing_combos(db, pf.id, years, args.ticker)
        print(f"gap-inventar: {len(todo)} (company,key,year) combos with missing periods",
              flush=True)
        for c, key, year, missing in todo:
            print(f"  {c.ticker} {key} {year}: missing {','.join(missing)}", flush=True)
        if args.dry_run:
            return 0

        created = write_not_found_placeholders(db, pf.id, years)
        print(f"{created} not_found placeholders written", flush=True)
        report = build_completeness_report(db, pf.id, years)
        for key, stats in report["per_key"].items():
            print(f"  {key}: expected={stats['expected']} with_value={stats['with_value']} "
                  f"not_found={stats['not_found']} excluded={stats['excluded']}", flush=True)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
