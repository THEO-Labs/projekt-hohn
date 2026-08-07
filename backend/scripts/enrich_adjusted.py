"""Backfill der Non-GAAP-Adjusted-Werte aus 8-K-Earnings-Releases.

Laeuft enrich_adjusted_from_earnings_releases pro Firma (US-Filer;
non-US wird uebersprungen) und druckt das Summary — fuer den manuellen
Nachlauf auf Prod nach dem Provider-First-Rollout.

Nicht in Tests eingebunden (macht echte Netz- und Claude-Calls).

Beispiel:
  uv run python scripts/enrich_adjusted.py --tickers MSFT,NOW,V --years 2025,2026
"""
import argparse

from app.companies.models import Company
from app.db import SessionLocal
from app.values.adjusted_enrichment import enrich_adjusted_from_earnings_releases
from app.values.routes import _run_and_persist_calculations


def main() -> None:
    parser = argparse.ArgumentParser(description="Non-GAAP adjusted backfill aus 8-K-Releases")
    parser.add_argument("--tickers", required=True,
                        help="Komma-getrennte Ticker (muessen in companies existieren)")
    parser.add_argument("--years", required=True,
                        help="Komma-getrennte Jahre, z.B. 2025,2026")
    parser.add_argument("--max-llm-calls", type=int, default=8,
                        help="Deckel fuer Claude-Calls pro Firma (default: 8)")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    years = [int(y.strip()) for y in args.years.split(",") if y.strip()]

    total = 0
    db = SessionLocal()
    try:
        for ticker in tickers:
            company = db.query(Company).filter(Company.ticker == ticker).one_or_none()
            if company is None:
                print(f"{ticker}: NICHT in companies — uebersprungen")
                continue
            try:
                enriched = enrich_adjusted_from_earnings_releases(
                    db, company, years, max_llm_calls=args.max_llm_calls,
                )
                # Adjusted-Varianten der berechneten Kennzahlen (PE adj etc.)
                # direkt nachziehen — sonst stimmen sie erst nach dem
                # naechsten Refresh.
                for year in years:
                    _run_and_persist_calculations(db, company.id, "FY", year)
                db.commit()
            except Exception as e:
                db.rollback()
                print(f"{ticker}: FEHLER — {e}")
                continue
            total += enriched
            print(f"{ticker}: {enriched} Perioden angereichert (Jahre {years})")
    finally:
        db.close()

    print(f"\nGesamt: {total} Perioden angereichert ({len(tickers)} Ticker)")


if __name__ == "__main__":
    main()
