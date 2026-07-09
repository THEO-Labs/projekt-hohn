---
key: net_debt
label_de: Net Debt
category: DEBT
data_type: NUMERIC
unit: EUR (fuer QIA: USD)
---

# net_debt — Nettoverschuldung

## Definition
**Net Debt = Gross Debt − Liquid Assets**. Klassische Formel:
```
net_debt = st_debt + lt_debt − cash_and_equivalents − st_investments
```

Positiv = Firma hat mehr Schulden als Cash (Netto-Schuldner)
Negativ = Firma hat mehr Cash als Schulden (Netto-Cash-Position)

## Quelle
1. **Company-eigene Definition**: viele DAX-Firmen reporten Net Debt direkt (mit eigener Formula)
2. **Aus Bilanz-Komponenten**: st_debt + lt_debt - cash - st_investments

## Einheit & Format
- Absolute EUR
- Vorzeichen: **positiv** bei Netto-Schuld, **negativ** bei Netto-Cash
- KEINE Mio-Skalierung

## Sanity-Range
- Netto-Cash-Konzerne (Software, Familie): -5 bis 0 Mrd (SAP: -3,5 Mrd)
- Kapital-intensive: 5-100 Mrd (BAS, DTE, VW, ALV)
- Autofinancers: sehr hoch aus FS-Sparte

## Anti-Confusion

**Klassische Formel vs Company-Definition**:
- BMW: Net Debt = Automotive Net Cash Position (nur Industrie-Sparte, ohne FS)
- VW analog
- DTE: eigene Definition mit Pensions
- Konvention: **Klassisch** (st + lt - cash - st_investments)
- Bei Abweichung dokumentieren

**Vorzeichen** (haeufigster Fehler):
- Netto-Cash-Firma: Net Debt = **negativ** (-3,5 Mrd SAP)
- Netto-Schulden-Firma: Net Debt = **positiv** (+40 Mrd BAS)
- Manche Quellen zeigen Netto-Cash als positiv → dann Vorzeichen umkehren

**Pensionsrueckstellungen**:
- Klassische Formel: NICHT einbeziehen (das ist keine Finanzschuld)
- Manche Firmen (DTE, BAYN) reporten Net Debt inkl. Pensions
- Konvention: **Klassisch ohne Pensions**

**Leasing (IFRS 16)**:
- Konvention: **einbeziehen** (in st_debt + lt_debt)

**Autofinancers**:
- VW: Net Debt Total-Konzern (Auto + FS) vs nur Automotive
- Konvention: **Total-Konzern** (Konsistenz mit MC/Revenue)

## Cross-References
1. **net_debt = st_debt + lt_debt − cash − st_investments** (Klassisch)
2. **net_debt_change = net_debt_curr − net_debt_prev** (YoY)
3. **net_debt_change_pct = net_debt_change / market_cap × 100**
4. **ev = market_cap + net_debt** (Enterprise Value)
5. **ev_ebitda = ev / ebitda**

## Output-Format
```json
{
  "ticker": "SAP.DE",
  "period_year": 2025,
  "net_debt_eur": -3500000000,
  "type": "classic (ST+LT debt - Cash - ST Investments)",
  "components": {
    "st_debt": 498000000,
    "lt_debt": 5900000000,
    "cash": 8800000000,
    "st_investments": 1098000000
  },
  "excludes_pensions": true,
  "includes_ifrs16": true,
  "note": "Negative = SAP hat mehr Cash als Debt (Netto-Cash-Position)"
}
```

## Referenz-Beispiele
| Ticker | period_year | Correct | Falsches Muster |
|---|---|---|---|
| SAP.DE | 2025 | **-3.500 Mio** (Netto-Cash) | +3.500 (Vorzeichen falsch) |
| BAS.DE | 2025 | ~+40 Mrd (Netto-Schuld) | negativ |
| DTE.DE | 2025 | ~+120 Mrd (Netto-Schuld inkl. T-Mobile US Kredite) | 60 Mrd (nur Konzern DE) |
| BMW.DE | 2025 | Netto-Cash Automotive ~+40 Mrd, Total-Konzern positiv ~+80 Mrd (FS-Kredite) | Nur Auto |

## Query-Template
```
Fuer {ticker} zum {reference_date}:
1. Klassische Formel: st_debt + lt_debt - cash - st_investments
2. Oder: Company-Reported Net Debt (wenn klassisch)
3. Vorzeichen: positiv = Netto-Schuld, negativ = Netto-Cash
4. IFRS 16 Leasing EINBEZIEHEN
5. Pensionsrueckstellungen NICHT
6. Total-Konzern (bei Auto: inkl. FS)
7. Sanity: bei Netto-Cash-Firma pruefen ob Vorzeichen negativ ist!
```
