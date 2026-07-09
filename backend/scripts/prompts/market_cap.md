---
key: market_cap
label_de: Marktkapitalisierung
category: STAMMDATEN
data_type: NUMERIC
unit: EUR (fuer QIA: USD) — absolute Waehrungseinheiten
---

# market_cap — Marktkapitalisierung Total

## Definition
Total-Marktbewertung aller ausstehenden Aktien = `stock_price × shares_outstanding_total`. Verwendet den boersengehandelten Kurs (bei Dual-Class: Preferred-Kurs als Approximation fuer Common, klassische Convention).

Fuer FY-Perioden: Kurs zum Stichtag **Ende des vorangegangenen GJ** (das ist der Preis mit dem das GJ gestartet wurde). Das ist die "Anker-Konvention" fuer Rendite-Berechnung.

Fuer SNAPSHOT: aktueller Kurs × aktuelle Total Shares.

## Quelle
- **Preis**: Bloomberg / Yahoo Adj Close des Stichtags
- **Shares**: siehe `shares_outstanding.md` (TOTAL, alle Klassen)

## Einheit & Format
- Absolute EUR (fuer QIA: USD)
- Format: `163240000000` fuer 163,24 Mrd EUR (keine Kilo/Mio-Skalierung)
- Vorzeichen: immer positiv

## Sanity-Range (DAX)
- Small-Cap DAX: 5–15 Mrd EUR (SY1, BNR, HNR1)
- Mid-Cap: 15–60 Mrd EUR (typische DAX)
- Mega-Cap: 60–300 Mrd EUR (SAP, DTE, SIE, ALV)

## Anti-Confusion (typische Fehler)

**Freefloat MC vs Total MC** (der Merck/Porsche-Case):
- FALSCH: nur boersengehandelte Klasse (MRK: 129M x Kurs = 17 Mrd)
- RICHTIG: **Total Shares × Kurs** (MRK: 434M x Kurs = 58 Mrd)
- Convention: MC ist immer Total-Bewertung, nicht Freefloat

**Waehrungsverwechslung**:
- SAP: 163B EUR ODER 175B USD? Beides valide je nach Quelle. Konvention: **EUR fuer deutsche Firmen, USD nur Qiagen**.
- Wenn Bloomberg/Reuters USD-Preis liefert: mit EUR/USD-Kurs konvertieren.

**Stichtag-Verwechslung**:
- FY 2025 market_cap = Kurs am **31.12.2024** × Shares (nicht 31.12.2025).
- FY 2024 market_cap = Kurs am **31.12.2023** × Shares.
- SNAPSHOT = **heute** × Shares.

**Mio vs Absolute**:
- FALSCH: `163240` (nur Mio-Einheit)
- RICHTIG: `163240000000` (absolute EUR)

## Cross-References
1. **market_cap = stock_price × shares_outstanding** (exakt)
2. **pe_ratio = market_cap / net_income** (nur wenn NI > 0)
3. **ev_ebitda = (market_cap + net_debt) / ebitda**
4. Alle Yields (dividend_yield, sbc_yield, buyback_yield, fcf_yield) haengen von MC ab

## Output-Format (Agent-Response)
```json
{
  "ticker": "MRK.DE",
  "period_year": 2025,
  "period_type": "FY",
  "market_cap_eur": 58546739356,
  "stock_price_eur": 134.66,
  "shares_used": 434777877,
  "reference_date": "2024-12-31",
  "source_url": "https://www.bloomberg.com/quote/MRK:GR",
  "note": "Total shares (Ordinary + Common), Bloomberg Adj Close 31.12.2024"
}
```

## Referenz-Beispiele
| Ticker | period_year | MC korrekt | Falsches Muster |
|---|---|---|---|
| MRK.DE | 2025 | 58,55 Mrd (134,66 × 434,78M **Total**) | 17,40 Mrd (nur 129,24M Ordinary) |
| PAH3.DE | 2025 | ~20,2 Mrd (33 × 612,5M Total) | 5,0 Mrd (nur Preferred) |
| SAP.DE | 2026 | ~163 Mrd (140 × 1,166M) | 163 Mio (Faktor 1000 vergessen) |
| VOW3.DE | 2025 | **~46 Mrd** (Preferred-Kurs ~92 EUR × 501,30M Total, Stichtag 31.12.2024) | ~27 Mrd (nur Preferred-Klasse 295M ohne Common) |

## Query-Template fuer Agent
```
Fuer {ticker} zum Stichtag {reference_date}:
1. Finde Adj Close (Bloomberg/Yahoo) am {reference_date}.
2. Finde total shares outstanding (INKL. alle Klassen) zum {reference_date}.
3. MC = Kurs × Total Shares.
4. Verifiziere gegen Bloomberg/Reuters Reference MC ±5% (wegen Zeitpunkt-Rounding).
5. Wenn Diff >10%: pruefe ob Freefloat vs Total confusion.
```
