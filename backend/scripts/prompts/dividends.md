---
key: dividends
label_de: Dividenden (Cash-Payout)
category: DIVIDENDS
data_type: NUMERIC
unit: Berichtswaehrung total (absoluter Cash-Payout, NIEMALS per-share)
---

# dividends — Cash-Dividende Total

## Definition
**Cash-Ausschuettung an ALLE Aktionaere fuer das Geschaeftsjahr `{period_year}`**. Das ist die Dividende die im Geschaeftsjahr `{period_year}` **verdient** wurde (aus dem NI dieses GJ) und typisch im Mai des Folgejahres nach der HV ausgezahlt wird.

Beispiel: `dividends`, `period_year=2025`, `period_type=FY` = **Dividende fuer GJ 2025** = Vorschlag im Annual Report 2025 = Cash-Zahlung Mai 2026 nach HV.

**Konvention konsistent mit allen anderen Metriken**: `period_year` = Geschaeftsjahr der Firma (nicht Kalenderjahr der Cash-Zahlung).

## Quelle im Report
1. **Annual Report `{period_year}`** → "Dividendenvorschlag" / "Proposed dividend" fuer GJ `{period_year}`
2. **Notes** → "Dividends per share" fuer GJ `{period_year}`
3. **Statement of Changes in Equity** naechstes Jahr → Cash-Bewegung der Auszahlung (Verifikation)
4. **Kapitalflussrechnung** `{period_year+1}` → "Dividends paid to shareholders" (Verifikation der tatsaechlichen Zahlung)

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

**Geschaeftsjahr vs Kalenderjahr der Zahlung** (kritisch):
- FALSCH: `period_year=2025` mit dem Cash-Payout aus dem Kalenderjahr 2025 (= Div fuer GJ 2024) fuellen.
- RICHTIG: `period_year=2025` = Div die im GJ 2025 **verdient** wurde (Vorschlag im AR 2025, Cash Mai 2026).
- Beispiel Continental:
  - `period_year=2024 FY dividends` = 2,20 EUR × 200,18M = **440.500.000 EUR** (Zahlung Mai 2025)
  - `period_year=2025 FY dividends` = 2,70 EUR × 200,18M = **540.500.000 EUR** (Zahlung Mai 2026)

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
1. **Q-Split (Rollup-Konvention, KEIN Zahlungs-Timing)**: Die Pipeline summiert
   Q1+Q2+Q3+Q4 = FY fuer dividends. Deutsche Einmal-Zahler: **Q1 = Q2 = Q3 = 0
   und Q4 = FY-Wert** eintragen (source_quote: "Rollup-Konvention: Jahresdividende
   dem Q4 des verdienten GJ zugeordnet"). Quartalszahler (US-Firmen, QIA, REITs):
   echte Quartals-Cash-Dividenden pro Q. So bleibt Q-Sum = FY immer konsistent.
2. **dividend_yield = dividends / market_cap × 100**: muss in Sanity-Range fallen (2–6% DAX)
3. **per_share_check = dividends / shares_outstanding**: sollte plausibel sein (0,5–20 EUR fuer DAX)

## Output-Format (Agent-Response)
```json
{
  "ticker": "CON.DE",
  "period_year": 2025,
  "period_type": "FY",
  "dividends_eur": 540500000,
  "per_share_eur": 2.70,
  "shares_used": 200178074,
  "for_fiscal_year": 2025,
  "expected_payment_date": "2026-05-XX",
  "source_url": "https://www.continental.com/en/investors/shares/dividend/",
  "source_quote": "Continental Executive Board has proposed increasing the dividend by €0.20 to €2.70 per share for the past fiscal year, with a total distribution to shareholders of approximately €540 million"
}
```

## Referenz-Beispiele (DAX, verifizierte Werte)
| Ticker | period_year | Correct | Herleitung |
|---|---|---|---|
| CON.DE | 2025 | 540.500.000 EUR | 2,70 EUR/Aktie × 200,18M Aktien (Vorschlag im AR 2025, Zahlung 2026) |
| CON.DE | 2024 | 440.500.000 EUR | 2,20 EUR/Aktie × 200,18M Aktien (Vorschlag im AR 2024, Zahlung 2025) |
| SAP.DE | 2024 | ~3.320.000.000 EUR | 2,85 EUR × 1.166M Aktien (Zahlung Mai 2025) |
| ALV.DE | 2024 | ~6.160.000.000 EUR | 15,40 EUR × 400M Aktien (Zahlung Mai 2025) |
| DTE.DE | 2024 | ~4.292.000.000 EUR | 0,90 EUR × 4.769M Aktien (Zahlung Mai 2025) |
| PAH3.DE | 2024 | ~1.170.000.000 EUR | 1,91 EUR × 612,50M Aktien inkl. Common (Zahlung Mai 2025) |
| BAYN.DE | 2024 | ~110.000.000 EUR | 0,11 EUR × 982M Aktien (Sonderkuerzung wegen Verlusten) |

## Query-Template fuer Agent
```
Fuer {ticker} ({company_name}) Geschaeftsjahr {period_year}:
1. Finde im Annual Report {period_year} den Dividendenvorschlag ("proposed dividend" /
   "Dividendenvorschlag" fuer GJ {period_year}).
2. Wenn nur per-share Wert verfuegbar: multipliziere mit total shares outstanding
   (bei Dual-Class-Firmen: BEIDE Klassen zusammen).
3. Gib absolute Berichtswaehrung zurueck.
4. Verifiziere: dividends_eur / shares_outstanding sollte plausibel per-share sein (0,5-20 EUR).
5. Verifiziere: dividends_eur / market_cap sollte 2-6% ergeben (max 10%, sonst Fehler).
6. WICHTIG: period_year=2025 heisst "fuer GJ 2025" (Zahlung 2026), NICHT "gezahlt in 2025".
```
