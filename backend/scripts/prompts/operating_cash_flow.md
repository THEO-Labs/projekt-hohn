---
key: operating_cash_flow
label_de: Operativer Cashflow
category: FCF
data_type: NUMERIC
unit: EUR (fuer QIA: USD)
---

# operating_cash_flow — Cash Flow aus operativer Taetigkeit

## Definition
**IFRS Cash Flow aus operativer Taetigkeit** wie im Cash Flow Statement direkt reported. Netto (nach Working Capital Changes, nach Steuern, nach Zinsen typisch).

## Quelle im Report
1. **Cash Flow Statement** → Zeile "Net cash provided by operating activities" / "Mittelfluss aus laufender Geschaeftstaetigkeit"
2. **Notes to CFS**: Detailkomponenten (falls Analyse noetig)

## Einheit & Format
- Absolute EUR
- Vorzeichen: **kann negativ sein** (RWE Q1 2025: -2.065 Mio, VW Q1 saisonal auch)

## Sanity-Range
- OCF-Marge (OCF / Revenue) typisch 5-30%
- Q1 saisonal oft negativ oder klein
- Q4 stark positiv
- Ausserhalb -20% bis +50% Marge = red flag

## Anti-Confusion (typische Fehler)

**Direct vs Indirect Method**:
- Beide IFRS-erlaubt, gleiches Ergebnis. Konvention: **wie reported**

**Continuing vs Total**:
- Total inkl. discontinued (Konsistenz mit revenue/NI)

**Pre-Interest, Pre-Tax Adjustments**:
- Einige Firmen reporten "OCF vor Zinsen und Steuern" (E.ON)
- Konvention: **Netto nach Interest+Tax** (klassisch)

**Automotive Split**:
- VW: OCF Total-Konzern (Automotive + FS), nicht nur Automotive
- MBG analog

## Cross-References
1. **fcf = ocf - capex**
2. **Q-Sum = FY**
3. **ocf_margin = ocf / revenue × 100**

## Output-Format (Agent-Response)
```json
{
  "ticker": "SAP.DE",
  "period_year": 2025,
  "period_type": "Q1",
  "operating_cash_flow_eur": 3780000000,
  "type": "IFRS as reported",
  "source_report": "SAP Q1 2025 Quarterly Statement"
}
```

## Referenz-Beispiele
| Ticker | period_year | Q | Correct | Falsches Muster |
|---|---|---|---|---|
| SAP.DE | 2025 | Q1 | 3.780 Mio EUR | 3780 (Faktor 1M vergessen) |
| RWE.DE | 2025 | Q1 | **-2.065 Mio** (saisonal) | 0 (negative Werte als "0" ersetzt) |
| BAS.DE | 2025 | Q1 | **-982 Mio** (Working Capital) | positive Schaetzung |

## Query-Template fuer Agent
```
Fuer {ticker} in Periode {period_year} {period_type}:
1. Cash Flow Statement -> "Cash flows from operating activities" Total-Zeile
2. Netto (nach Interest+Tax)
3. Total inkl. discontinued
4. Q1 negative Werte sind saisonal normal
5. Sanity: OCF/Revenue in -20%..+50%
```
