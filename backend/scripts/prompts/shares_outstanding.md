---
key: shares_outstanding
label_de: Ausstehende Aktien
category: STAMMDATEN
data_type: NUMERIC
unit: Absolute Anzahl Aktien (keine Millionen, keine Tausend)
---

# shares_outstanding — Ausstehende Aktien Total

## Definition
**Total Anzahl aller ausstehenden Aktien** aller Aktienklassen (Common + Preferred + Ordinary) zum Stichtag Ende {period_year} (bzw. aktueller Stand bei SNAPSHOT). **NICHT** nur die boersengehandelte Klasse.

## Quelle im Report
1. **Notes to Financial Statements** → Note "Share Capital" oder "Equity" mit Aufschluesselung nach Aktienklasse
2. **Cover-Seite Annual Report** → oft "Total shares outstanding"
3. **IR-Fact-Sheet** → typisch mit Preferred/Common Split
4. **Fuer Dual-Class: BEIDE Klassen addieren**

## Einheit & Format
- **Absolute Anzahl** (nicht `200`, sondern `200000000` fuer 200 Mio Aktien)
- Vorzeichen: **immer positiv**
- Keine Fractional shares

## Sanity-Range (DAX)
- Kleine DAX: 50–200M Aktien (BEI, HNR1, MTX)
- Mittlere: 200–800M (BAS, MBG, VOW3, CON)
- Grosse: 1–5 Mrd (SAP, DTE, ALV, DBK)
- Groesste: >5 Mrd (nur DTE mit ~4,77 Mrd)
- Ausserhalb 10M–10 Mrd = red flag

## Anti-Confusion (typische Fehler)

**Dual-Class-Firmen — Preferred/Ordinary + Common**:
- MRK.DE (Merck KGaA): 129,24M Ordinary (an Boerse) + 305,54M Common (Familie E.Merck) = **434,78M Total**
- **FALSCH**: nur 129M eintragen → Market Cap unterschaetzt um Faktor 3,4
- **RICHTIG**: 434,78M Total

- PAH3.DE (Porsche SE): 306,25M Vorzuege + 306,25M Stamm = **612,50M Total**
- **FALSCH**: nur 306,25M (Preferred) → MC halbiert
- **RICHTIG**: 612,50M

- VOW3.DE (Volkswagen): 295,09M Preferred + 206,21M Common = **501,30M Total**

- BMW.DE: 601,99M Common + 54,77M Preferred = **656,77M Total**

- HEN3.DE (Henkel): 178,16M Preferred + 259,80M Common = **437,96M Total**

**Common Aktien nicht vergessen bei deutschen Familien-Konzernen** (Merck, Henkel, Porsche SE, BMW, Beiersdorf, VW etc.).

**Treasury Shares**:
- Aussuenderzahlen enthalten oft Treasury Shares (zurueckgekaufte). Fuer MC-Rechnung: **shares outstanding INKLUSIVE Treasury** (weil das der aktuelle Umlauf ist bevor Cancellation).
- Wenn eine Firma Treasury eingezogen hat: dann sind die weg.

**Kapitalerhoehungen / Splits**:
- Nach Kapitalerhoehung (z.B. Rheinmetall 2025) neue shares nutzen fuer aktuelle Periode
- Nach Split: alle historischen shares zurueck-adjustieren

**Vor vs. nach Buyback**:
- shares_outstanding ist Stand am Perioden-Ende (nach Buybacks der Periode)

## Cross-References (Konsistenz-Checks)
1. **market_cap = stock_price × shares_outstanding** (muss exakt matchen bis auf Rundung <1%)
2. **eps_diluted = net_income / shares_outstanding** (approximativ, echte diluted rechnet mit anderen Faktoren)
3. Historische Konsistenz: shares_outstanding sollte nur bei Emission steigen oder bei Cancellation sinken. Sprung >10% ohne begleitendes Event = red flag.

## Output-Format (Agent-Response)
```json
{
  "ticker": "MRK.DE",
  "period_year": 2025,
  "period_type": "FY",
  "shares_outstanding_total": 434777877,
  "breakdown": {
    "preferred_or_ordinary_listed": 129242251,
    "common_or_family": 305535626
  },
  "source_url": "https://www.merckgroup.com/.../annual-report-2025.pdf",
  "source_quote": "129,242,251 no-par-value bearer shares + 305,535,626 registered no-par-value shares",
  "reference_date": "2025-12-31"
}
```

## Referenz-Beispiele (Dual-Class)
| Ticker | Preferred/Ordinary | Common/Family | **Total (nutzen!)** |
|---|---|---|---|
| MRK.DE | 129,24M | 305,54M | **434,78M** |
| PAH3.DE | 306,25M (Vorzug) | 306,25M (Stamm) | **612,50M** |
| VOW3.DE | 295,09M (Vorzug) | 206,21M (Stamm) | **501,30M** |
| BMW.DE | 601,99M (Stamm) | 54,77M (Vorzug) | **656,77M** |
| HEN3.DE | 178,16M (Vorzug) | 259,80M (Stamm) | **437,96M** |
| SAP.DE | — | — | **1.166,00M** (nur eine Klasse) |
| SIE.DE | — | — | **800,00M** (nur eine Klasse) |

## Query-Template fuer Agent
```
Fuer {ticker} ({company_name}) zum Stichtag {reference_date}:
1. Suche in den Notes to FS die "Share Capital"-Note.
2. Liste ALLE Aktienklassen auf (Preferred, Common, Ordinary, Bearer, Registered).
3. Total = Summe aller ausstehenden Aktien aller Klassen (INKL. Treasury im Umlauf).
4. Verifiziere: Total × Stock-Price sollte den Market-Cap-Referenzwert (Bloomberg/FT) treffen ±2%.
5. Wenn Dual-Class: gib Breakdown separat an im Response.
```
