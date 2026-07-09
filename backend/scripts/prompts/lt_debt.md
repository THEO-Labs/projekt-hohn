---
key: lt_debt
label_de: Langfristige Schulden
category: DEBT
data_type: NUMERIC
unit: EUR (fuer QIA: USD)
---

# lt_debt — Langfristige Finanzverbindlichkeiten

## Definition
**Non-current financial liabilities** mit Restlaufzeit >12 Monate: langfristige Anleihen, langfristige Bank-Kredite, langfristige Leasingverbindlichkeiten (IFRS 16). **AUSSCHLIESSEN**: Pensionsrueckstellungen, sonstige langfristige Rueckstellungen.

## Quelle
1. **Bilanz Passiva** → "Non-current financial liabilities"
2. Notes zu Finanzschulden mit Restlaufzeit-Aufschluesselung

## Einheit & Format
- Absolute EUR, positiv
- KEINE Mio-Skalierung

## Sanity-Range
- 500 Mio – 200 Mrd EUR DAX
- Autofinancers, Utilities, Telcos hoch
- Software, Beiersdorf niedrig
- Ausserhalb: red flag

## Anti-Confusion

**Pensionsrueckstellungen**:
- FALSCH: einbeziehen (das ist eine Ruecklage, nicht Finanzschuld)
- RICHTIG: separater Bilanzposten "Pensionsverpflichtungen"

**Current portion of LT debt**:
- Der kurzfristige Teil eines LT-Bonds gehoert in **st_debt** (nicht in lt_debt)

**Deferred Tax Liabilities**:
- NICHT einbeziehen

**Convertible Bonds**:
- Einbeziehen (Nominalwert oder book value)

## Cross-References
1. **net_debt = st_debt + lt_debt − cash − st_investments**

## Output-Format
```json
{
  "ticker": "SAP.DE",
  "period_year": 2025,
  "lt_debt_eur": 5900000000,
  "includes": ["bonds", "bank_loans", "ifrs16_lease_lt", "convertibles"],
  "excludes": ["pensions", "deferred_tax", "provisions"],
  "reference_date": "2025-12-31"
}
```

## Query-Template
```
Fuer {ticker} zum {reference_date}:
1. Bilanz Passiva -> "Non-current financial liabilities"
2. Einbeziehen: langfr. Bonds, Bank-Kredite, IFRS 16 (lt), Convertibles
3. NICHT: Pensions, Provisions, Deferred Tax
4. Der kurzfr. Teil eines LT-Bonds gehoert in st_debt
5. Positive Absolutbetraege
```
