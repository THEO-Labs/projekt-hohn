---
key: st_investments
label_de: Kurzfristige Anlagen
category: CASH
data_type: NUMERIC
unit: EUR (fuer QIA: USD)
---

# st_investments — Kurzfristige Anlagen

## Definition
**Short-term financial investments** aus der Bilanz: liquide Anlagen mit Restlaufzeit 3-12 Monate. Umfasst kurzfristige Wertpapiere, Termeinlagen, Geldmarktfonds.

## Quelle
1. **Bilanz Aktiva** → "Short-term investments" / "Kurzfristige Finanzanlagen" / "Marketable securities"
2. Notes zu Finanzinstrumenten: Aufschluesselung nach Laufzeit

## Einheit & Format
- Absolute EUR, positiv
- KEINE Mio-Skalierung

## Sanity-Range
- 0-5 Mrd EUR typisch
- Kann 0 sein (nicht alle Firmen halten das)
- Cash-reiche Software-Firmen (SAP) haben oft signifikante ST-Investments

## Anti-Confusion

**Long-term vs Short-term**:
- LT Investments (>1 Jahr) NICHT einbeziehen (kein value_key dafuer aktuell)
- Konvention: **nur ST (bis 12 Monate)**

**Cash vs ST-Investments**:
- Cash: bis 3 Monate → separate value_key
- ST-Investments: 3-12 Monate → dieser value_key

**Equity Investments**:
- Aktienbeteiligungen NICHT einbeziehen (das sind LT)

## Cross-References
1. **net_debt = st_debt + lt_debt − cash − st_investments**

## Output-Format
```json
{
  "ticker": "SAP.DE",
  "period_year": 2025,
  "st_investments_eur": 4200000000,
  "reference_date": "2025-12-31",
  "source_report": "SAP AR 2025 Balance Sheet"
}
```

## Query-Template
```
Fuer {ticker} zum {reference_date}:
1. Bilanz -> "Short-term investments" / "Marketable securities" (3-12 Monate Laufzeit)
2. NICHT: Cash&Equiv (separater Key), NICHT: LT-Anlagen
3. Positive Absolutbetraege
4. Wenn Firma nicht reported: 0 statt null
```
