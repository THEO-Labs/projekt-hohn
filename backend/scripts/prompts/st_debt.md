---
key: st_debt
label_de: Kurzfristige Schulden
category: DEBT
data_type: NUMERIC
unit: EUR (fuer QIA: USD)
---

# st_debt — Kurzfristige Finanzverbindlichkeiten

## Definition
**Current financial liabilities** aus der Bilanz mit Restlaufzeit <12 Monate. Umfasst kurzfristige Bank-Kredite, kurzfristigen Teil von Anleihen, Commercial Papers, kurzfristige Leasingverbindlichkeiten (IFRS 16), sowie den innerhalb 12 Monaten faelligen Teil langfristiger Anleihen.

**AUSSCHLIESSEN**: Trade Payables, sonstige operative Verbindlichkeiten, Pensionsrueckstellungen, Steuerverbindlichkeiten, Deferred Revenue.

## Quelle im Report
1. **Bilanz Passiva** → "Kurzfristige Finanzverbindlichkeiten" / "Current financial debt" / "Short-term borrowings"
2. **Notes zu Finanzschulden** mit Laufzeit-Aufschluesselung: nimm nur die "innerhalb 12 Monate"-Zeile

## Einheit & Format
- Absolute EUR, POSITIVER Wert
- KEINE Mio-Skalierung

## Sanity-Range (DAX)
- Industrie (SAP, BEI, HEN3): 200 Mio – 3 Mrd EUR
- Grosse Industrie (BAS, BAYN, SIE, DHL): 3–15 Mrd EUR
- Autofinancers (VOW3, MBG, BMW): 30–70 Mrd EUR (grosser FS-Anteil)
- Utilities (RWE, EOAN, DTE): 3–15 Mrd EUR
- Banken (DBK, CBK): NICHT vergleichbar, ganz eigene Bilanzstruktur — bis 500 Mrd
- Ausserhalb: red flag

## Anti-Confusion (typische Fehler)

**Finanzschulden vs Operative Passiva**:
- FALSCH: Trade Payables ("Verbindlichkeiten aus Lieferungen und Leistungen") einbeziehen
- RICHTIG: nur zinstragende Finanzverbindlichkeiten
- Test: wenn keine Zins-Komponente → keine Finanzschuld

**IFRS 16 Leasing**:
- Konvention: **einbeziehen** (kurzfristiger Teil der Lease Liability aus IFRS 16)
- Erkennbar in der Bilanz als separate "Lease liabilities current"-Zeile

**Current portion of LT debt**:
- Der innerhalb 12 Monate faellige Teil eines Bonds mit Ursprungslaufzeit >1J gehoert in **st_debt** (nicht in lt_debt)
- Wird oft in Notes gezeigt als "of which due within 12 months"
- Konvention: **einbeziehen** in st_debt

**Bank-Overdrafts (Kontokorrent)**:
- Einbeziehen (technisch Cash-Management, aber zinstragend)

**Interne Konzern-Loans**:
- NICHT einbeziehen (konsolidiert weg auf Konzern-Ebene)

**Pensions vs Debt**:
- Pensionsrueckstellungen NICHT einbeziehen (separater Bilanzposten)
- Auch nicht: OPEB (Other Post-Employment Benefits)

## Cross-References
1. **net_debt = st_debt + lt_debt − cash − st_investments** (Kern-Formel)
2. Sum st_debt + lt_debt = Total Financial Debt (Bruttoverschuldung)

## Output-Format (Agent-Response)
```json
{
  "ticker": "SAP.DE",
  "period_year": 2025,
  "period_type": "FY",
  "st_debt_eur": 498000000,
  "includes": ["bank_loans_current", "commercial_paper", "current_portion_of_bonds", "ifrs16_lease_current", "bank_overdrafts"],
  "excludes": ["trade_payables", "provisions", "pensions", "deferred_tax", "deferred_revenue"],
  "reference_date": "2025-12-31",
  "source_report": "SAP Annual Report 2025 Balance Sheet + Note Financial Liabilities"
}
```

## Referenz-Beispiele (DAX FY 2024)
| Ticker | Correct (Mrd EUR) | Comment |
|---|---|---|
| SAP.DE | **~0,5** | Software-Konzern, sehr geringe kurzfristige Verschuldung |
| BAS.DE | **~7–10** | Chemie mit Working-Capital-Finanzierung |
| VOW3.DE | **~65** | Autofinancer, FS-Refinanzierung dominiert |
| MBG.DE | **~50** | Analog VW-Struktur |
| BMW.DE | **~40** | Analog VW-Struktur |
| DTE.DE | **~10** | Teil der Netzwerk-Refi |
| BAYN.DE | **~5–8** | Post-Monsanto-Refi |
| HEN3.DE | **~1–2** | Konsumgueter mit stabilen Cashflows |

## Query-Template fuer Agent
```
Fuer {ticker} zum Stichtag Ende {period_year}:
1. Bilanz Passiva -> "Current financial liabilities" / "Kurzfristige Finanzschulden"
2. Notes zu Finanzverbindlichkeiten mit Laufzeit-Aufschluesselung: nur "< 12 Monate" Zeile
3. EINBEZIEHEN: Bank-Kredite kurzfr., Commercial Paper, current portion of LT bonds, IFRS 16 Leasing (kurzfr.), Bank Overdrafts
4. NICHT: Trade Payables, Provisions, Pensions, Deferred Tax, Deferred Revenue, interne Konzernkredite
5. Positive Absolutbetraege
6. Sanity: fuer Industrie 200M-15 Mrd, fuer Autofinancers/Banken viel groesser
```
