"""ESEF-Trefferquoten-Check gegen die Live-API (filings.xbrl.org).

Nimmt eine Ticker-Liste + Jahr, ruft den ESEF-Provider pro Key und druckt
Wert/None — zur Messung der Provider-Coverage nach einem Deploy.
ISIN und FY-Ende kommen aus der companies-Tabelle.

Nicht in Tests eingebunden (macht echte Netz-Calls).

Beispiel:
  uv run python scripts/check_esef_coverage.py --tickers SAP,BAS,AIR --year 2025
"""
import argparse

from app.companies.models import Company
from app.db import SessionLocal
from app.providers.esef import CONCEPT_MAP, ESEFProvider

DEFAULT_KEYS = sorted(set(CONCEPT_MAP.keys()) | {"fcf", "ebitda", "capex"})


def main() -> None:
    parser = argparse.ArgumentParser(description="ESEF provider coverage check")
    parser.add_argument("--tickers", required=True,
                        help="Komma-getrennte Ticker (muessen in companies existieren)")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--keys", default=",".join(DEFAULT_KEYS),
                        help=f"Komma-getrennte value_keys (default: {','.join(DEFAULT_KEYS)})")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    keys = [k.strip() for k in args.keys.split(",") if k.strip()]

    provider = ESEFProvider()
    hits: dict[str, int] = {k: 0 for k in keys}

    db = SessionLocal()
    try:
        for ticker in tickers:
            company = db.query(Company).filter(Company.ticker == ticker).one_or_none()
            if company is None:
                print(f"\n{ticker}: NICHT in companies — uebersprungen")
                continue
            if not company.isin:
                print(f"\n{ticker}: keine ISIN hinterlegt — uebersprungen")
                continue
            print(f"\n{ticker} (ISIN {company.isin}) FY{args.year}:")
            for key in keys:
                try:
                    result = provider.fetch(
                        ticker, key, "FY", args.year,
                        fy_end_month=getattr(company, "fiscal_year_end_month", None),
                        fy_end_day=getattr(company, "fiscal_year_end_day", None),
                        isin=company.isin,
                    )
                except Exception as e:
                    print(f"  {key:24s} EXC: {e}")
                    continue
                if result is not None:
                    hits[key] += 1
                    cur = f" {result.currency}" if result.currency else ""
                    print(f"  {key:24s} {result.value}{cur}")
                else:
                    print(f"  {key:24s} None")
    finally:
        db.close()

    print(f"\nTrefferquote pro Key ({len(tickers)} Ticker):")
    for key in keys:
        print(f"  {key:24s} {hits[key]}/{len(tickers)}")


if __name__ == "__main__":
    main()
