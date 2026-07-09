---
key: dividends
label_de: Dividenden (Cash-Payout)
category: DIVIDENDS
data_type: NUMERIC
unit: EUR total (absoluter Cash-Payout, NIEMALS per-share)
---

# dividends — Cash-Dividende Total

## Definition
**Absolute Cash-Ausschuettung an ALLE Aktionaere** die IM Kalenderjahr {period_year} gezahlt wurde. Das entspricht der Dividende die FUER Geschaeftsjahr {period_year-1} nach Hauptversammlung (typisch Mai) ausgeschuettet wurde.

Beispiel: `dividends`, `period_year=2025`, `period_type=FY` = **Cash-Payout im Kalenderjahr 2025** = Dividende fuer FY 2024, ausgeschuettet nach HV im Mai 2025.

## Quelle im Report
1. **Kapitalflussrechnung (Statement of Cash Flows)** → Position "Dividends paid to shareholders" oder "Dividendenzahlungen an Aktionaere" (Cash-Outflow, meist negativ dargestellt — als positiven Absolutbetrag speichern).
2. **Statement of Changes in Equity** → Position "Dividends" (Reduktion des Eigenkapitals).
3. **Notes** → oft eine eigene Note "Dividends per share" mit Total-Payout-Angabe.

## Einheit & Format
- **Absolute EUR** (fuer QIA.DE: USD)
- **KEINE** Millionen-Skalierung (nicht `440`, sondern `440000000`)
- Vorzeichen: **positiv** (Absolutbetrag, auch wenn im CF-Statement negativ)

## Sanity-Range (DAX-typisch)
- Payout-Ratio (Div / Net Income): 20–60% typisch, 60–80% bei stabilen Cash-Cows (DTE, ALV), <20% bei Wachstum (SAP historisch)
- Dividendrendite (Div / Market Cap): 2–6% DAX-Norm, >8% verdaechtig, >10% fast sicher Fehler
- Wenn Firma reported Verlust: Div kann trotzdem gezahlt werden (aus Ruecklagen), aber unwahrscheinlich >Payout-Ratio 100% des Vorjahres-NI

## Anti-Confusion (typische Fehler)

**PER-SHARE vs TOTAL** (der Continental-Case):
- FALSCH: "Dividende 2,70 EUR" als `2700000000` = 2,7 Mrd interpretiert.
- RICHTIG: 2,70 EUR/Aktie × 200,18M Aktien = **540.500.000 EUR**
- **PFLICHT**: immer `per_share × shares_outstanding` rechnen wenn du per-share findest.

**Vorschlag vs. Zahlung**:
- Div-Vorschlag fuer FY 2025 (im Annual Report 2025 vorgeschlagen) → wird IN 2026 gezahlt → gehoert in `period_year=2026`
- Div-Zahlung im Mai 2025 (nach HV) → das ist der 2024er Vorschlag → gehoert in `period_year=2025`
- **Konvention: period_year = Kalenderjahr der Cash-Ausschuettung**.

**Regular vs Special Dividend**:
- Sonderdividenden (z.B. aus Spin-offs) sind Teil des Total-Cash-Payout, wenn cash-relevant.
- Aktien-Dividenden (in-kind) NICHT einbeziehen — nur Cash.

**Dual-Class-Firmen (MRK, PAH3, VOW3)**:
- Wenn Preferred und Common existieren: **Total = per_share × total_shares_all_classes**
- MRK: 434,78M Total (Ordinary + Common)
- PAH3: 612,50M Total (306,25M Preferred + 306,25M Common)
- VOW3: 501,3M Total (295,1M Preferred + 206,2M Common)

**Continuing vs Discontinued Operations**:
- Div ist Corporate-Ebene, immer Total (nicht nach Segment splitten).

## Cross-References (Konsistenz-Checks)
1. **Q-Split**: Div wird typisch nur einmal jaehrlich gezahlt (in Q2 nach HV). Also `Q1=0, Q2=FY, Q3=0, Q4=0`. Ausnahmen: QIA (Q3), einige Immobilien-REITs (quartalsweise).
2. **dividend_yield = dividends / market_cap × 100**: muss in Sanity-Range fallen (2–6% DAX)
3. **per_share_check = dividends / shares_outstanding**: sollte plausibel sein (0,5–20 EUR fuer DAX)

## Output-Format (Agent-Response)
```json
{
  "ticker": "CON.DE",
  "period_year": 2025,
  "period_type": "FY",
  "dividends_eur": 440500000,
  "per_share_eur": 2.20,
  "shares_used": 200178074,
  "declared_for_fy": 2024,
  "payment_date": "2025-05-02",
  "source_url": "https://cdn.continental.com/.../continental_dividendenbekanntmachung_hv_2024.pdf",
  "source_quote": "€2.20 per eligible share, totaling €440,013,162.60"
}
```

## Referenz-Beispiele (DAX)
| Ticker | period_year | Correct | Falsches Muster (was NICHT reinschreiben) |
|---|---|---|---|
| CON.DE | 2025 | 440.500.000 EUR (2,20 × 200,18M) | 2.700.000.000 (Verwechslung mit 2,70 EUR/Aktie) |
| ALV.DE | 2025 | ~6,2 Mrd EUR (15,4 × 400M) | 15,40 (per-share) |
| DTE.DE | 2025 | ~4,2 Mrd EUR (0,90 × 4.769M shares) | Uebersehen dass DTE viele Aktien hat |
| SAP.DE | 2025 | ~3,4 Mrd EUR (2,90 × 1.166M) | 2.900.000 (Verwechslung EUR mit Cent) |
| PAH3.DE | 2025 | ~1,17 Mrd EUR (1,91 × 612,5M **Total**) | 585M (nur Preferred, ignoriert Common) |

## Query-Template fuer Agent
```
Fuer {ticker} ({company_name}):
1. Finde im Annual Report / Statement of Cash Flows die Position "Dividends paid to shareholders"
   fuer das Kalenderjahr {period_year}.
2. Wenn nur per-share Wert verfuegbar: multipliziere mit total shares outstanding
   (bei Dual-Class-Firmen: BEIDE Klassen zusammen).
3. Gib absolute EUR zurueck (fuer QIA: USD).
4. Verifiziere: dividends_eur / shares_outstanding sollte plausibel per-share sein (0,5-20 EUR).
5. Verifiziere: dividends_eur / market_cap sollte 2-6% ergeben (max 10%, sonst Fehler wahrscheinlich).
```
