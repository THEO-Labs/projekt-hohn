"""Q-Faktor-Werte fuer eine Firma + Forecast-Year NUR neu berechnen.

Trigger NUR den lokalen Q-Faktor-Proxy aus den hochgeladenen Quartalsberichten.
KEINE Web-Recherche, KEIN Claude/Gemini-Call.

Update-Strategie pro ESTIMABLE_KEY:
  - forecast_alternates: q_factor_proxy-Entry wird neu geschrieben
  - numeric_value (primary): NUR aktualisiert wenn primary_method aktuell
    "q_factor_proxy" ist. Web/PDF-Werte bleiben unangetastet.

Am Ende: _run_and_persist_calculations damit kaskadierende Multiples
(P/E, EV/EBITDA, Hohn-Rendite, ...) den neuen Q-Faktor mitkriegen.

Usage:
  python -m scripts.recompute_qfactor_only --ticker NOW --period-year 2026
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.calculations.estimates import ESTIMABLE_KEYS, compute_estimate
from app.companies.models import Company
from app.db import SessionLocal
from app.ir_documents.models import IRDocument, PeriodCoverage
from app.values.currency_keys import CURRENCY_KEYS
from app.values.models import CompanyValue


def _persist_q_from_extraction(db, company_id: UUID, target_fy: int) -> int:
    """Persistiert Q1 [target_fy] CompanyValues aus extraction_results
    falls noch nicht in DB. Verhindert UniqueViolation-Rollback der
    Original-Extraction durch row-by-row SAVEPOINTs.
    Returns: Anzahl neu/aktualisiert geschriebene Rows.
    """
    written = 0
    for q in (PeriodCoverage.Q1, PeriodCoverage.Q2, PeriodCoverage.Q3):
        doc = (
            db.query(IRDocument)
            .filter(
                IRDocument.company_id == company_id,
                IRDocument.period_year == target_fy,
                IRDocument.period_coverage == q,
            )
            .order_by(IRDocument.uploaded_at.desc())
            .first()
        )
        if doc is None or not doc.extraction_results:
            continue
        period_type = q.value
        for key, info in doc.extraction_results.items():
            if key.startswith("_") or not isinstance(info, dict):
                continue
            raw = info.get("value")
            if raw is None:
                continue
            try:
                val = Decimal(str(raw))
            except (InvalidOperation, ValueError):
                continue

            existing = (
                db.query(CompanyValue)
                .filter(
                    CompanyValue.company_id == company_id,
                    CompanyValue.value_key == key,
                    CompanyValue.period_type == period_type,
                    CompanyValue.period_year == target_fy,
                    CompanyValue.is_forecast.is_(False),
                )
                .one_or_none()
            )
            if existing is not None:
                if existing.manually_overridden or existing.numeric_value == val:
                    continue
                existing.numeric_value = val
                existing.currency = info.get("currency") or existing.currency
                existing.source_name = (f"PDF: {doc.display_name} (Q-Faktor-Persist)")[:1900]
                existing.from_ir_pdf = True
                existing.fetched_at = datetime.now(timezone.utc)
                written += 1
                continue

            try:
                with db.begin_nested():
                    db.add(CompanyValue(
                        id=uuid4(),
                        company_id=company_id,
                        value_key=key,
                        period_type=period_type,
                        period_year=target_fy,
                        numeric_value=val,
                        currency=info.get("currency"),
                        source_name=(f"PDF: {doc.display_name} (S.{info.get('page')})" if info.get("page") else f"PDF: {doc.display_name}")[:1900],
                        source_link=f"/api/companies/{company_id}/ir-documents/{doc.id}/download",
                        fetched_at=datetime.now(timezone.utc),
                        from_ir_pdf=True,
                        is_forecast=False,
                    ))
                    db.flush()
                written += 1
            except IntegrityError:
                db.expire_all()
                continue
    return written


def _proxy_alt_dict(est, key: str, currency: str | None, target_fy: int) -> dict:
    if est.method == "flow_factor" and est.factor is not None:
        source_label = f"Proxy (Q-Faktor): FY{target_fy - 1} × Faktor {est.factor:.4f}"
    elif est.method == "balance_snapshot":
        qs = ",".join(est.quarters_used) if est.quarters_used else "?"
        source_label = f"Proxy (Bilanz-Snapshot {qs})"
    elif est.method == "fy_fallback":
        source_label = f"Proxy (FY{target_fy - 1}-Wert, no-growth)"
    else:
        source_label = f"Proxy ({est.method})"

    return {
        "method": "q_factor_proxy",
        "value": str(est.value),
        "currency": currency if key in CURRENCY_KEYS else None,
        "source": source_label,
        "explanation": est.explanation,
    }


def _update_alternates(existing_alts: list[dict] | None, new_proxy: dict) -> list[dict]:
    out: list[dict] = []
    replaced = False
    for a in (existing_alts or []):
        if a.get("method") == "q_factor_proxy":
            out.append(new_proxy)
            replaced = True
        else:
            out.append(a)
    if not replaced:
        out.append(new_proxy)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True, help="z.B. NOW")
    parser.add_argument("--period-year", required=True, type=int, help="Forecast-Year, z.B. 2026")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.ticker == args.ticker).one_or_none()
        if not company:
            raise SystemExit(f"Company mit Ticker '{args.ticker}' nicht gefunden")

        target_fy = args.period_year
        print(f"Company: {company.name} (id={company.id}, ticker={company.ticker})")
        print(f"Target FY: {target_fy}")
        print(f"Keys: {sorted(ESTIMABLE_KEYS)}")
        print()

        print(f"[1/2] Q[target_fy] values aus extraction_results persistieren (falls fehlend)...")
        n_persisted = _persist_q_from_extraction(db, company.id, target_fy)
        print(f"     -> {n_persisted} CompanyValue-Rows neu/aktualisiert")
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
        print()
        print(f"[2/2] Q-Faktor neu berechnen fuer FY{target_fy}...")

        for key in sorted(ESTIMABLE_KEYS):
            try:
                est = compute_estimate(db, company.id, key, target_fy, currency=company.currency)
            except Exception as e:
                print(f"  {key:24s} FEHLER: {e}")
                continue
            if est is None:
                print(f"  {key:24s} kein Estimate moeglich (keine Q/FY-Daten)")
                continue

            proxy_alt = _proxy_alt_dict(est, key, company.currency, target_fy)

            row = (
                db.query(CompanyValue)
                .filter(
                    CompanyValue.company_id == company.id,
                    CompanyValue.value_key == key,
                    CompanyValue.period_type == "FY",
                    CompanyValue.period_year == target_fy,
                    CompanyValue.is_forecast.is_(True),
                )
                .one_or_none()
            )
            if row is None:
                row = (
                    db.query(CompanyValue)
                    .filter(
                        CompanyValue.company_id == company.id,
                        CompanyValue.value_key == key,
                        CompanyValue.period_type == "FY",
                        CompanyValue.period_year == target_fy,
                    )
                    .first()
                )

            if row is None:
                print(f"  {key:24s} kein CompanyValue-Row vorhanden -> skip (zuerst normaler Refresh notwendig)")
                continue

            new_alts = _update_alternates(row.forecast_alternates, proxy_alt)
            row.forecast_alternates = new_alts

            primary_updated = False
            if row.primary_method == "q_factor_proxy" and not row.manually_overridden and not row.from_ir_pdf:
                row.numeric_value = est.value
                row.source_name = proxy_alt["source"]
                primary_updated = True

            marker = " [primary aktualisiert]" if primary_updated else ""
            print(f"  {key:24s} {est.method:18s} = {est.value:>20,.2f}  -> alt aktualisiert{marker}")

        if args.dry_run:
            print("\nDry-run — kein commit.")
            db.rollback()
        else:
            db.commit()
            print("\nCommit OK.")

            from app.values.routes import _run_and_persist_calculations

            try:
                _run_and_persist_calculations(db, company.id, "FY", target_fy)
                db.commit()
                print(f"Berechnete Multiples (P/E, EV/EBITDA, Hohn-Rendite, ...) fuer FY{target_fy} neu persistiert.")
            except Exception as e:
                print(f"Re-Calc fehlgeschlagen: {e}")
                db.rollback()

    finally:
        db.close()


if __name__ == "__main__":
    main()
