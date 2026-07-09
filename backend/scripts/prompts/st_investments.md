---
key: st_investments
label_de: Kurzfristige Anlagen
category: CASH
data_type: NUMERIC
unit: EUR (fuer QIA: USD)
---

# st_investments — Kurzfristige Anlagen

## Definition
**Short-term financial investments** aus der Bilanz: liquide Anlagen mit Restlaufzeit 3–12 Monate. Umfasst kurzfristige Wertpapiere (Bonds, T-Bills), Termeinlagen, Geldmarktfonds, marketable securities held for trading.

Konzeptionell **hoch-liquide** aber nicht "sofort verfuegbar" wie Cash — deshalb separater Bilanzposten.

## Quelle im Report
1. **Bilanz Aktiva** → "Short-term investments" / "Kurzfristige Finanzanlagen" / "Marketable securities" / "Financial assets — current"
2. **Notes zu Finanzinstrumenten**: Aufschluesselung nach Restlaufzeit; nimm nur "3–12 Monate"

## Einheit & Format
- Absolute EUR, POSITIVER Wert
- KEINE Mio-Skalierung

## Sanity-Range (DAX)
- Meiste DAX-Industrie: 0–1 Mrd EUR (nutzen Cash-Pool statt separate ST-Investments)
- Cash-reiche Software (SAP): **3–5 Mrd EUR**
- Versicherer (ALV, MUV2, HNR1): sehr hoch (10–50 Mrd, Teil ihres Anlage-Portfolios; aber typisch separat als "Financial assets" reported)
- Kann 0 sein — nicht alle Firmen halten das
- Ausserhalb 0–50 Mrd = red flag

## Anti-Confusion (typische Fehler)

**Long-term vs Short-term**:
- LT Investments (>1 Jahr Restlaufzeit) NICHT einbeziehen (kein value_key dafuer aktuell)
- Konvention: **nur ST (bis 12 Monate Restlaufzeit)**
- Test: wenn im Report "Non-current financial assets" → nicht hier

**Cash vs ST-Investments**:
- Cash & Equivalents: bis 3 Monate Ursprungslaufzeit → separater value_key `cash_and_equivalents`
- ST-Investments: 3–12 Monate → dieser value_key
- Manche Firmen fassen beides als "Cash + ST Investments" zusammen — dann in **cash_and_equivalents** verbuchen und hier **0** setzen

**Equity Investments**:
- Aktienbeteiligungen an anderen Firmen NICHT einbeziehen (LT-strategische Beteiligung)
- Auch nicht: at-Equity-konsolidierte Beteiligungen

**Held-for-Sale Assets (IFRS 5)**:
- Wenn Firma Assets zur Veraeusserung klassifiziert (z.B. Spin-off pending): das ist ein separater Bilanzposten "Assets classified as held for sale"
- NICHT in st_investments einbeziehen

**Restricted / Escrow-Investments**:
- Wenn Firma Cash/Investments als Escrow oder Sicherheit gebunden hat: separat als "Restricted"-Line
- Konvention: **einbeziehen** wenn im Standard-ST-Investments-Line, sonst separat markieren

**Trading vs Available-for-Sale**:
- Beide Kategorien einbeziehen wenn im "ST Financial Assets"-Bilanzposten

## Cross-References
1. **net_debt = st_debt + lt_debt − cash − st_investments** (Kern-Formel — st_investments erhoeht Netto-Cash)
2. **liquid_assets = cash + st_investments** (Total-Liquiditaet)

## Output-Format (Agent-Response)
```json
{
  "ticker": "SAP.DE",
  "period_year": 2025,
  "period_type": "FY",
  "st_investments_eur": 4200000000,
  "includes": ["marketable_securities_current", "term_deposits", "money_market_funds"],
  "excludes": ["cash_and_equivalents", "lt_investments", "equity_stakes", "held_for_sale_assets"],
  "reference_date": "2025-12-31",
  "source_report": "SAP AR 2025 Balance Sheet + Note Financial Assets"
}
```

## Referenz-Beispiele (DAX FY 2024)
| Ticker | Correct (Mrd EUR) | Comment |
|---|---|---|
| SAP.DE | **~4** | Signifikant durch Cash-Ueberschuss |
| BEI.DE | **~2** | Konservative Liquiditaets-Anlage |
| SIE.DE | **~2–3** | Teil des Cash-Managements |
| BAS.DE | **~0** | Nutzt Cash-Pool statt separate Investments |
| BAYN.DE | **~0–1** | Nach Monsanto-Deal reduziert |
| BMW.DE | **~10** | Autofinancer mit Anlage-Portfolio |
| ALV.DE | **~50–80** | Versicherer — allerdings typisch separat als "Financial assets, at fair value" reported |

## Query-Template fuer Agent
```
Fuer {ticker} zum Stichtag Ende {period_year}:
1. Bilanz Aktiva -> "Short-term investments" / "Marketable securities" / "Current financial assets" (Restlaufzeit 3-12 Monate)
2. NICHT: Cash & Equivalents (separater Key), LT-Anlagen, Aktienbeteiligungen, Held-for-Sale-Assets
3. Positive Absolutbetraege
4. Wenn Firma Cash + ST-Investments nur zusammen reported: hier 0, alles in cash_and_equivalents
5. Wenn Firma nicht reported: 0 (nicht null)
6. Sanity: Industrie 0-5 Mrd, Autofinancer/Versicherer viel hoeher
```
