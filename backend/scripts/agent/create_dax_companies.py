"""Legt die 40 DAX-Companies im Portfolio 'DAX' an.

Idempotent (skip wenn Ticker im Portfolio schon existiert).
"""
from __future__ import annotations

import logging
import sys
from uuid import uuid4

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

from app.db import SessionLocal
from app.companies.models import Company
from app.portfolios.models import Portfolio  # noqa: F401  (FK resolution)

PORTFOLIO_ID = "b3a10032-c646-4036-97eb-ee72331ae423"

# (ticker, name, currency, fiscal_year_end_month, fiscal_year_end_day, isin)
DAX40 = [
    ("ADS.DE", "adidas AG", "EUR", 12, 31, "DE000A1EWWW0"),
    ("AIR.PA", "Airbus SE", "EUR", 12, 31, "NL0000235190"),
    ("ALV.DE", "Allianz SE", "EUR", 12, 31, "DE0008404005"),
    ("BAS.DE", "BASF SE", "EUR", 12, 31, "DE000BASF111"),
    ("BAYN.DE", "Bayer AG", "EUR", 12, 31, "DE000BAY0017"),
    ("BEI.DE", "Beiersdorf AG", "EUR", 12, 31, "DE0005200000"),
    ("BMW.DE", "Bayerische Motoren Werke AG", "EUR", 12, 31, "DE0005190003"),
    ("BNR.DE", "Brenntag SE", "EUR", 12, 31, "DE000A1DAHH0"),
    ("CBK.DE", "Commerzbank AG", "EUR", 12, 31, "DE000CBK1001"),
    ("CON.DE", "Continental AG", "EUR", 12, 31, "DE0005439004"),
    ("DTG.DE", "Daimler Truck Holding AG", "EUR", 12, 31, "DE000DTR0CK8"),
    ("DBK.DE", "Deutsche Bank AG", "EUR", 12, 31, "DE0005140008"),
    ("DB1.DE", "Deutsche Boerse AG", "EUR", 12, 31, "DE0005810055"),
    ("DHL.DE", "Deutsche Post AG", "EUR", 12, 31, "DE0005552004"),
    ("DTE.DE", "Deutsche Telekom AG", "EUR", 12, 31, "DE0005557508"),
    ("EOAN.DE", "E.ON SE", "EUR", 12, 31, "DE000ENAG999"),
    ("FRE.DE", "Fresenius SE & Co KGaA", "EUR", 12, 31, "DE0005785604"),
    ("FME.DE", "Fresenius Medical Care AG", "EUR", 12, 31, "DE0005785802"),
    ("G1A.DE", "GEA Group AG", "EUR", 12, 31, "DE0006602006"),
    ("HNR1.DE", "Hannover Rueck SE", "EUR", 12, 31, "DE0008402215"),
    ("HEI.DE", "Heidelberg Materials AG", "EUR", 12, 31, "DE0006047004"),
    ("HEN3.DE", "Henkel AG & Co KGaA", "EUR", 12, 31, "DE0006048432"),
    ("IFX.DE", "Infineon Technologies AG", "EUR", 9, 30, "DE0006231004"),
    ("MBG.DE", "Mercedes-Benz Group AG", "EUR", 12, 31, "DE0007100000"),
    ("MRK.DE", "Merck KGaA", "EUR", 12, 31, "DE0006599905"),
    ("MTX.DE", "MTU Aero Engines AG", "EUR", 12, 31, "DE000A0D9PT0"),
    ("MUV2.DE", "Muenchener Rueckversicherungs AG", "EUR", 12, 31, "DE0008430026"),
    ("PAH3.DE", "Porsche Automobil Holding SE", "EUR", 12, 31, "DE000PAH0038"),
    ("QIA.DE", "Qiagen NV", "USD", 12, 31, "NL0012169213"),
    ("RHM.DE", "Rheinmetall AG", "EUR", 12, 31, "DE0007030009"),
    ("RWE.DE", "RWE AG", "EUR", 12, 31, "DE0007037129"),
    ("SAP.DE", "SAP SE", "EUR", 12, 31, "DE0007164600"),
    ("G24.DE", "Scout24 SE", "EUR", 12, 31, "DE000A12DM80"),
    ("SIE.DE", "Siemens AG", "EUR", 9, 30, "DE0007236101"),
    ("ENR.DE", "Siemens Energy AG", "EUR", 9, 30, "DE000ENER6Y0"),
    ("SHL.DE", "Siemens Healthineers AG", "EUR", 9, 30, "DE000SHL1006"),
    ("SY1.DE", "Symrise AG", "EUR", 12, 31, "DE000SYM9999"),
    ("VOW3.DE", "Volkswagen AG", "EUR", 12, 31, "DE0007664039"),
    ("VNA.DE", "Vonovia SE", "EUR", 12, 31, "DE000A1ML7J1"),
    ("ZAL.DE", "Zalando SE", "EUR", 12, 31, "DE000ZAL1111"),
]


def main() -> int:
    db = SessionLocal()
    try:
        existing = {
            c.ticker
            for c in db.query(Company).filter(Company.portfolio_id == PORTFOLIO_ID).all()
        }
        added = 0
        skipped = 0
        for ticker, name, currency, fye_month, fye_day, isin in DAX40:
            if ticker in existing:
                skipped += 1
                continue
            c = Company(
                id=uuid4(),
                portfolio_id=PORTFOLIO_ID,
                name=name,
                ticker=ticker,
                isin=isin,
                currency=currency,
                fiscal_year_end_month=fye_month,
                fiscal_year_end_day=fye_day,
            )
            db.add(c)
            added += 1
            print(f"INS {ticker}: {name}")
        db.commit()
        print(f"\nDone: {added} angelegt, {skipped} skipped (schon da)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
