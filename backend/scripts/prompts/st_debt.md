---
key: st_debt
label_de: Kurzfristige Schulden
category: DEBT
data_type: NUMERIC
unit: EUR (fuer QIA: USD)
---

# st_debt — Kurzfristige Finanzverbindlichkeiten

## Definition
**Current financial liabilities** aus der Bilanz mit Restlaufzeit <12 Monate. Umfasst kurzfristige Bank-Kredite, kurzfristigen Teil von Anleihen, Commercial Papers, kurzfristige Leasingverbindlichkeiten (IFRS 16).

**AUSSCHLIESSEN**: Trade Payables, sonstige operative Verbindlichkeiten.

## Quelle
1. **Bilanz Passiva** → "Kurzfristige Finanzverbindlichkeiten" / "Current financial debt"
2. Notes zu Finanzschulden mit Laufzeit-Aufschluesselung

## Einheit & Format
- Absolute EUR, positiv
- KEINE Mio-Skalierung

## Sanity-Range
- 0-10 Mrd EUR typisch DAX
- Autofinancers (VW, MBG) und Banken haben viel groesser
- Ausserhalb: red flag

## Anti-Confusion

**Finanzschulden vs Operative Passiva**:
- FALSCH: Trade Payables einbeziehen
- RICHTIG: nur zinstragende Finanzverbindlichkeiten

**IFRS 16 Leasing**:
- Konvention: **einbeziehen** (kurzfristiger Teil der Lease Liability)

**Current portion of LT debt**:
- Konvention: **einbeziehen** in st_debt (nicht in lt_debt)

## Cross-References
1. **net_debt = st_debt + lt_debt − cash − st_investments**

## Output-Format
```json
{
  "ticker": "SAP.DE",
  "period_year": 2025,
  "st_debt_eur": 498000000,
  "includes": ["bank_loans", "commercial_paper", "current_portion_of_bonds", "ifrs16_lease"],
  "excludes": ["trade_payables", "provisions"],
  "reference_date": "2025-12-31"
}
```

## Query-Template
```
Fuer {ticker} zum {reference_date}:
1. Bilanz Passiva -> "Current financial liabilities" / "Kurzfristige Finanzschulden"
2. Einbeziehen: Bank-Kredite, Commercial Paper, current portion of LT bonds, IFRS 16 Leasing (kurzfr.)
3. NICHT: Trade Payables, Provisions
4. Positive Absolutbetraege
```
