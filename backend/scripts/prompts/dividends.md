---
key: dividends
label_de: Dividenden (Cash-Payout)
category: DIVIDENDS
data_type: NUMERIC
unit: Berichtswaehrung total (absoluter Cash-Payout, NIEMALS per-share)
---

# dividends — Cash-Dividende Total

## Definition
**Cash-Ausschuettung an ALLE Aktionaere, die IM KALENDERJAHR `{period_year}` GEZAHLT wurde** (Zahlungsjahr-Konvention, Kundenentscheidung 2026-07). Bei deutschen Einmal-Zahlern ist das die Dividende fuer das VORJAHRES-Geschaeftsjahr, beschlossen auf der HV im Fruehjahr von `{period_year}`.

Beispiel: `dividends`, `period_year=2025`, `period_type=FY` = **Cash-Zahlung im Mai 2025** = Dividende fuer GJ 2024 (adidas: 2.00 EUR x 178.6M = ~357M; NICHT die 500M fuer GJ 2025, die erst Mai 2026 fliessen).

**ACHTUNG — Abweichung von allen anderen Metriken**: period_year = Kalenderjahr des CASH-FLUSSES, nicht das Geschaeftsjahr des Verdienens. Fuer das laufende Jahr ist die Zahlung meist schon bekannt/beschlossen (HV im Fruehjahr) — dann is_estimate=false.

## Quelle im Report
1. **Kapitalflussrechnung `{period_year}`** → "Dividends paid to shareholders" (der tatsaechliche Cash-Fluss des Jahres — PRIMAERQUELLE)
2. **Annual Report `{period_year - 1}`** → "Dividendenvorschlag" fuer GJ `{period_year - 1}` (wird auf der HV im Fruehjahr `{period_year}` beschlossen und dann gezahlt)
3. **HV-/IR-Meldungen Fruehjahr `{period_year}`** → beschlossene Dividende + Zahltermin
4. **Statement of Changes in Equity `{period_year}`** → Cash-Bewegung der Auszahlung (Verifikation)

## Einheit & Format
- **Absolute Berichtswaehrung der Firma** (EUR fuer DAX, USD fuer US-Firmen)
- **KEINE** Millionen-Skalierung (nicht `440`, sondern `440000000`)
- Vorzeichen: **positiv**

## Sanity-Range (DAX-typisch)
- Payout-Ratio (Div / Net Income): 20–60% typisch, 60–80% bei stabilen Cash-Cows (DTE, ALV), <20% bei Wachstum
- Dividendrendite (Div / Market Cap): 2–6% DAX-Norm, >8% verdaechtig, >10% fast sicher Fehler
- Bei Verlust-Jahr: Div kann trotzdem gezahlt werden (aus Ruecklagen)

## Anti-Confusion (typische Fehler)

**PER-SHARE vs TOTAL** (der Continental-Case):
- FALSCH: "Dividende 2,70 EUR" als `2700000000` = 2,7 Mrd interpretiert.
- RICHTIG: 2,70 EUR/Aktie × 200,18M Aktien = **540.500.000 EUR**
- **PFLICHT**: immer `per_share × shares_outstanding` rechnen wenn du per-share findest.

**Geschaeftsjahr vs Kalenderjahr der Zahlung** (kritisch — Konvention seit 2026-07 ZAHLUNGSJAHR):
- RICHTIG: `period_year=2025` = Cash-Payout im Kalenderjahr 2025 (= Dividende fuer GJ 2024, HV Fruehjahr 2025).
- FALSCH: den AR-`{period_year}`-Vorschlag eintragen (der fliesst erst im Folgejahr).
- Beispiel Continental:
  - `period_year=2025 FY dividends` = 2,20 EUR × 200,18M = **440.500.000 EUR** (fuer GJ 2024, Zahlung Mai 2025)
  - `period_year=2026 FY dividends` = 2,70 EUR × 200,18M = **540.500.000 EUR** (fuer GJ 2025, Zahlung Mai 2026)

**Regular vs Special Dividend**:
- Sonderdividenden (z.B. aus Spin-offs) sind Teil des Total-Cash-Payout, wenn cash-relevant.
- Aktien-Dividenden (in-kind) NICHT einbeziehen — nur Cash.

**Dual-Class-Firmen (MRK, PAH3, VOW3, BMW, HEN3)**:
- Wenn Preferred und Common existieren: **Total = per_share × total_shares_all_classes**
- MRK: 434,78M Total (Ordinary + Common)
- PAH3: 612,50M Total (306,25M Preferred + 306,25M Common)
- VOW3: 501,30M Total (295,09M Preferred + 206,21M Common)
- BMW: 656,77M Total (601,99M Common + 54,77M Preferred)
- HEN3: 437,96M Total (178,16M Preferred + 259,80M Common)

**Continuing vs Discontinued Operations**:
- Div ist Corporate-Ebene, immer Total.

## Cross-References (Konsistenz-Checks)
1. **Q-Split (Zahlungs-Timing)**: Die Pipeline summiert Q1+Q2+Q3+Q4 = FY.
   Deutsche Einmal-Zahler zahlen nach der HV im Fruehjahr: **Q2 = FY-Wert,
   Q1 = Q3 = Q4 = 0** (source_quote nennt HV-/Zahldatum). Quartalszahler:
   echte Quartals-Cash-Dividenden pro Q. Q-Sum = FY bleibt konsistent.
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
  "for_fiscal_year": 2024,
  "payment_date": "2025-05-XX",
  "source_url": "https://www.continental.com/en/investors/shares/dividend/",
  "source_quote": "Continental Executive Board has proposed increasing the dividend by €0.20 to €2.70 per share for the past fiscal year, with a total distribution to shareholders of approximately €540 million"
}
```

## Referenz-Beispiele (DAX, verifizierte Werte)
| Ticker | period_year (=Zahljahr) | Correct | Herleitung |
|---|---|---|---|
| ADS.DE | 2025 | ~357.000.000 EUR | 2,00 EUR/Aktie (GJ 2024) × 178,6M, HV/Zahlung Mai 2025 |
| ADS.DE | 2026 | ~500.000.000 EUR | 2,80 EUR/Aktie (GJ 2025), HV 07.05.2026 |
| CON.DE | 2025 | 440.500.000 EUR | 2,20 EUR (GJ 2024) × 200,18M, Zahlung Mai 2025 |
| CON.DE | 2026 | 540.500.000 EUR | 2,70 EUR (GJ 2025) × 200,18M, Zahlung Mai 2026 |
| SAP.DE | 2025 | ~3.320.000.000 EUR | 2,85 EUR (GJ 2024) × 1.166M, Zahlung Mai 2025 |
| PAH3.DE | 2025 | ~1.170.000.000 EUR | 1,91 EUR (GJ 2024) × 612,50M inkl. Common |
| BAYN.DE | 2025 | ~110.000.000 EUR | 0,11 EUR (GJ 2024) × 982M (Sonderkuerzung) |

## Query-Template fuer Agent
```
Fuer {ticker} ({company_name}) Zahlungsjahr {period_year}:
1. Finde die im Kalenderjahr {period_year} GEZAHLTE Dividende (CFS "Dividends paid"
   bzw. HV-Beschluss Fruehjahr {period_year} = Vorschlag aus AR {period_year - 1}).
2. Wenn nur per-share Wert verfuegbar: multipliziere mit total shares outstanding
   (bei Dual-Class-Firmen: BEIDE Klassen zusammen).
3. Gib absolute Berichtswaehrung zurueck.
4. Verifiziere: dividends_eur / shares_outstanding sollte plausibel per-share sein (0,5-20 EUR).
5. Verifiziere: dividends_eur / market_cap sollte 2-6% ergeben (max 10%, sonst Fehler).
6. WICHTIG: period_year = Jahr des CASH-FLUSSES. period_year=2025 heisst "gezahlt in 2025"
   (= Dividende fuer GJ 2024), NICHT "fuer GJ 2025".
```
