---
key: sbc
label_de: Stock-Based Compensation
category: SBC
data_type: NUMERIC
unit: EUR (fuer QIA: USD) — absolute Waehrung
---

# sbc — Stock-Based Compensation Expense

## Definition
**Aktienbasierte Verguetung als P&L-Aufwand** unter IFRS 2. Umfasst Aktienoptionen, Restricted Stock Units (RSU), Performance Share Units (PSU), Employee Stock Purchase Plans (ESPP). Cash-settled UND equity-settled Anteile.

## Quelle im Report
1. **Notes to FS -> "Share-based payments"** (IFRS 2 Note)
2. **Cash Flow Statement**: "Share-based compensation expense" als Non-Cash-Add-Back
3. **Segment- oder Konzern-Notes** je nach Firma
4. **P&L-Aufwandsposten**: verteilt auf Cost of Sales / R&D / SG&A

## Einheit & Format
- Absolute EUR (fuer QIA: USD)
- Vorzeichen: **positiv** (Aufwand)
- Cash-settled + equity-settled zusammen

## Sanity-Range
- SBC-Yield (SBC / MCap) typisch 0.1-2% DAX
- SBC / NetIncome typisch 5-30%
- Software (SAP, Qiagen, Symrise): hoeher (25-40% NI)
- Bank/Versicherer: sehr niedrig
- Familien-Konzerne (PAH3, Beiersdorf traditionell): oft 0

## Anti-Confusion (typische Fehler)

**SBC vs Buyback** (SAP-Case):
- FALSCH: 10 Mrd Buyback-Programm als SBC eintragen
- SBC ist P&L-Aufwand (Non-Cash), Buyback ist Cash-Outflow zur Aktienreduktion
- **Getrennt tracken**

**Grant Value vs Amortized Expense**:
- FALSCH: neuer Grant von 1,1 Mrd als SBC eintragen (vested erst ueber 3-4 Jahre)
- RICHTIG: **P&L-Aufwand der Periode** (typisch 25-33% des Grant-Values pro Jahr)

**Cash-settled vs Equity-settled**:
- Beide sind IFRS 2 Aufwand. **Beide einbeziehen**.
- Manche Firmen reporten getrennt (Henkel: "LTI + LTP" beide)

**FY-only vs Q-Split**:
- Die meisten Firmen disclosen SBC nur FY-Notes
- Ausnahmen mit Q-Disclosure: Infineon, Qiagen, Zalando, Porsche SE
- **Fuer ABGESCHLOSSENE FYs**: wenn nur FY disclosed → Q1-Q4 = null (nicht FY/4-Interpolation)
- **Fuer LAUFENDES FY**: wenn nur FY-Guidance/Analyst-Total: schaetze Q's mittels
  Vorjahres-Q-Verteilung (Prior-Year Q-Anteil x FY-Estimate). Beispiel:
  Adidas FY 2026 SBC-Schaetzung 95M, Vorjahr Q1-Q4 waren 18/24/37/43 (Summe 122M).
  Q-Anteile: 15%/20%/30%/35%. Adidas 2026 SBC Q1 = 95M x 15% = 14M usw. Dokumentiere
  als "prior-year seasonality: Q1=15% of FY". is_estimate=true.
  User braucht ausgefuellte Q-Zeilen — Halluzination nicht, Extrapolation ja.

**Employee Stock Purchase Plan (ESPP)**:
- Kleiner Anteil - einbeziehen falls disclosed

**Cash Bonus vs SBC**:
- Klassische Cash-Boni sind KEIN SBC
- Nur wenn in Aktien oder Aktien-linked

**Familien-Konzerne mit "kein SBC"**:
- PAH3: keine SBC (Note bestaetigt)
- Vonovia: minimale/keine

## Cross-References
1. **sbc_yield = sbc / market_cap × 100** typisch 0.1-2%
2. **net_buyback = buyback_volume - sbc** (Kern-Formel fuer Hohn-Rendite)
3. **Q-Sum = FY** wenn alle Q reported (rare)

## Output-Format (Agent-Response)
```json
{
  "ticker": "SAP.DE",
  "period_year": 2025,
  "period_type": "FY",
  "sbc_eur": 2700000000,
  "type": "IFRS 2 P&L expense",
  "components": {
    "equity_settled": 2100000000,
    "cash_settled": 600000000
  },
  "source_report": "SAP Annual Report 2025, Note 27 Share-Based Payments",
  "grant_value_new": null,
  "note": "P&L expense NOT grant value; new grants in 2025 = 1.1 Mrd but amortized over 3-4 years"
}
```

## Referenz-Beispiele
| Ticker | period_year | Correct | Falsches Muster |
|---|---|---|---|
| SAP.DE | 2025 | 2.700 Mio (P&L expense) | 10.000 Mio (Buyback-Programm) |
| ZAL.DE | 2025 | 91,9 Mio (aus Q-Werten summiert) | 300 Mio (Grant Value) |
| PAH3.DE | 2025 | **0** (Note explizit) | Interpolierte Schaetzung |
| BEI.DE | 2025 | **0** (nur Cash-LTP, kein IFRS 2) | Cash-Boni verwechselt |
| IFX.DE | 2025 | 227 Mio (aus Q1-Q4 summiert) | Nur FQ2 |

## Query-Template fuer Agent
```
Fuer {ticker} in Periode {period_year} {period_type}:
1. Notes to FS -> "Share-based payments" (IFRS 2)
2. P&L-Aufwand der Periode (NICHT grant value neuer Awards)
3. Cash-settled + Equity-settled zusammen
4. Positive Absolutzahl (Aufwand)
5. Wenn nur FY disclosed: Q1-Q4 = null (NICHT FY/4 interpolieren)
6. Wenn Firma explizit "no SBC" reported (PAH3, BEI): 0
7. Sanity: sbc/net_income typisch 5-30%, sbc/mc 0.1-2%
```
