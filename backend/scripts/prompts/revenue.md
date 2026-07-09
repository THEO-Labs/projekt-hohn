---
key: revenue
label_de: Umsatz
category: NI_GROWTH
data_type: NUMERIC
unit: EUR (fuer QIA: USD) — absolute Waehrungseinheiten
---

# revenue — Umsatz

## Definition
**Konzern-Gesamtumsatz** aus fortgefuehrten Geschaeftstaetigkeiten (continuing operations) nach IFRS. Bei Versicherern: "Insurance revenue from insurance contracts issued". Bei Banken: "Net revenues" (Zinsueberschuss + Provisionsueberschuss + Handelsergebnis).

## Quelle im Report
1. **Konzern-GuV** → erste Zeile "Umsatz" / "Revenue" / "Sales" / "Net revenues"
2. **Segment-Report** → Total-Zeile
3. **Quarterly Statements Trading-Update** → Sales-Zahl

## Einheit & Format
- **Absolute EUR** (fuer QIA: USD)
- KEINE Mio-Skalierung: `36800000000` fuer 36,8 Mrd EUR
- Vorzeichen: **positiv** (Retouren/Discounts sind schon abgezogen)

## Sanity-Range (DAX)
- Small: 1–5 Mrd EUR (SY1, G24, MTX)
- Mid: 5–30 Mrd EUR
- Large: 30–100 Mrd EUR (BAS, BAYN, SAP)
- Mega: 100–350 Mrd EUR (VOW3, ALV, DHL, DTE, SIE)

Q ~= FY / 4 (mit Saisonalitaet ±20%). Wenn Q > FY oder Q < 0 = red flag.

## Anti-Confusion (typische Fehler)

**Continuing vs Discontinued Operations**:
- Continental 2025: continuing (nach Aumovio-Spin-off) vs Total (mit Aumovio-Zeit bis 18.09.)
- Konvention: **Total inklusive discontinued** fuer die betreffende Periode wo discontinued noch drin war

**Reported vs Organic**:
- "Organic revenue growth" = FX-neutral + acquisition-neutral
- Konvention: **reported IFRS revenue** (nominal, wie in GuV)

**Segment-Sum vs Konzern**:
- Segment-Sum enthaelt oft Intersegment-Umsatz
- Konvention: **Konzern-Umsatz nach Eliminationen**

**Net vs Gross Revenue (fuer Handel/Vermittler)**:
- Wenn Firma als Agent auftritt: nur Provision als revenue
- Wenn als Principal: Full Gross
- Konvention: wie im IFRS-Abschluss reported

**Versicherer (ALV, MUV2, HNR1)**:
- IFRS 17 seit 2023: "Insurance revenue" statt frueher "Gross written premiums"
- Konvention: **Insurance revenue** (net of reinsurance ceded)

**Banken (DBK, CBK)**:
- Konvention: **Net revenues** = Zinsueberschuss + Provisionsueberschuss + Handelsergebnis + sonstige Ertraege

**Fiskaljahr-Verwechslung**:
- IFX Fiskaljahr Okt-Sep: Kal-Q1 2025 = FQ2 FY25
- SIE, SHL analog
- ENR analog
- Konvention: **auf Kalender-Quartale mappen**

**Auto-Konzerne mit Financial Services**:
- VOW3, MBG, BMW: revenue inkl. Financial Services
- Konvention: **Total Group Revenue** (inkl. FS)

## Cross-References
1. **Q1 + Q2 + Q3 + Q4 = FY** (exakt bei realen actuals)
2. **ni_margin = net_income / revenue × 100** (typisch 3-15% DAX)
3. **ps_ratio = market_cap / revenue** (0,5–15 typisch)
4. **fcf_margin = fcf / revenue × 100**
5. YoY: sollte mit Guidance-Range konsistent sein

## Output-Format (Agent-Response)
```json
{
  "ticker": "SAP.DE",
  "period_year": 2025,
  "period_type": "Q1",
  "revenue_eur": 9013000000,
  "source_report": "SAP Quarterly Statement Q1 2025 (April 22, 2025)",
  "source_url": "https://www.sap.com/investors/en/reports/quarterly-reports.html",
  "note": "IFRS Cloud + Software + Services revenue, total group"
}
```

## Referenz-Beispiele
| Ticker | period_year | Q | Correct | Falsches Muster |
|---|---|---|---|---|
| SAP.DE | 2025 | FY | 36.800 Mio EUR | 36.800 Mio USD (Waehrung) |
| ALV.DE | 2025 | FY | ~180 Mrd EUR (Insurance Revenue IFRS 17) | 100 Mrd (nur Life ohne P&C) |
| DTE.DE | 2025 | Q1 | ~29 Mrd EUR (inkl. T-Mobile US in USD-EUR-konvertiert) | 15 Mrd (nur Deutschland) |
| CON.DE | 2025 | FY | ~35 Mrd EUR (inkl. Aumovio bis 18.09.) | 20 Mrd (nur continuing) |

## Query-Template fuer Agent
```
Fuer {ticker} in Periode {period_year} {period_type}:
1. Konzern-GuV -> erste Umsatz-Zeile (Revenue/Sales/Net revenues/Insurance revenue)
2. IFRS reported (nicht adjusted, nicht organic)
3. Total Group (inkl. Financial Services bei Auto, inkl. discontinued bis Spin-off-Datum)
4. Fuer Fiskaljahr-Firmen (IFX/SIE/SHL/ENR): Kal-Q1 2025 = FQ2 FY25 etc.
5. Bei Q: Sum von Q1-Q4 sollte FY exakt matchen (Sanity-Check)
6. Gib absolute EUR (nicht Mio), Vorzeichen positiv
```
