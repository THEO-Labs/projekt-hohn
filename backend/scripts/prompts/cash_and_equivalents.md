---
key: cash_and_equivalents
label_de: Cash und Aequivalente
category: CASH
data_type: NUMERIC
unit: EUR (fuer QIA: USD)
---

# cash_and_equivalents — Zahlungsmittel und -aequivalente

## Definition
**IFRS "Cash and cash equivalents"** aus der Bilanz zum Stichtag. Umfasst Bargeld, Sichteinlagen, kurzfristige (bis 3 Monate) hochliquide Anlagen.

## Quelle im Report
1. **Bilanz Aktiva** → "Cash and cash equivalents" / "Zahlungsmittel und Zahlungsmitteleaequivalente"
2. Bei Banken: "Cash and central bank balances" (unterschiedliches Konzept)

## Einheit & Format
- Absolute EUR
- Vorzeichen: **positiv** (Aktivposten)
- KEINE Mio-Skalierung

## Sanity-Range
- 1-15% der Bilanzsumme
- Absolute: 500 Mio – 30 Mrd EUR im DAX
- Ausserhalb: red flag

## Anti-Confusion (typische Fehler)

**Cash vs Short-term Investments**:
- Cash & Equivalents: bis 3 Monate
- Short-term Investments (`st_investments`): 3-12 Monate → **separater value_key**
- Nicht mischen

**Restricted Cash**:
- Manche Firmen splitten "unrestricted" vs "restricted" cash
- Konvention: **beides zusammen** wenn im Standard-Cash-Line

**Banken**:
- Bei DBK, CBK: "Cash and central bank balances" (viel groesser als bei Industrie-Konzernen)
- Konvention: **wie reported** (nicht mit Industrie vergleichen)

**Konsolidierungskreis**:
- Fuer discontinued operations (Aumovio 2025): separater Line?
- Konvention: **Total inkl. discontinued** wenn noch Konzern-Teil

## Cross-References
1. **net_debt = st_debt + lt_debt − cash − st_investments** (Kern-Formel)
2. Q-Werte: typisch konstant (BS-Approximation) wenn nicht real reported

## Output-Format (Agent-Response)
```json
{
  "ticker": "SAP.DE",
  "period_year": 2025,
  "period_type": "FY",
  "cash_and_equivalents_eur": 8800000000,
  "reference_date": "2025-12-31",
  "excludes_st_investments": true,
  "source_report": "SAP Annual Report 2025 Balance Sheet"
}
```

## Referenz-Beispiele
| Ticker | period_year | Correct | Falsches Muster |
|---|---|---|---|
| SAP.DE | 2025 | 8.800 Mio EUR | 20.000 (inkl. ST-Investments) |
| DBK.DE | 2025 | ~150 Mrd (Bank) | 5 Mrd (Industrie-Level) |
| BAS.DE | 2025 | ~2 Mrd | 15 Mrd (Working-Capital-verwechselt) |

## Query-Template fuer Agent
```
Fuer {ticker} zum {reference_date}:
1. Bilanz -> "Cash and cash equivalents" (Standard IFRS-Line)
2. NICHT einbeziehen: ST-Investments (separater Key), Long-term securities
3. Bei Banken: "Cash and central bank balances"
4. Als positiver Absolutbetrag
5. Sanity: 1-15% der Bilanzsumme
```
