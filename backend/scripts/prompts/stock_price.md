---
key: stock_price
label_de: Aktienkurs
category: STAMMDATEN
data_type: NUMERIC
unit: Berichtswaehrung per share — Preis pro Aktie
---

# stock_price — Aktienkurs pro Aktie

## Definition
Boersenkurs pro Aktie zum Stichtag. Bei Dual-Class: **Preferred-Kurs** (der boersengehandelten Klasse). Adjustierter Close (Dividenden- und Split-adjustiert), typisch Yahoo Adj Close oder Bloomberg PX_LAST_ADJ.

Fuer FY-Perioden: Close am **31.12.{period_year-1}** (Start-of-FY-Anker).
Fuer SNAPSHOT: aktueller Close (letzter Handelstag).

## Quelle
- **Yahoo Finance** (fuer historische Adj Close, dividend-adjustiert)
- **Bloomberg** (PX_LAST_ADJ zum Stichtag)
- **Xetra** (Referenz-Boerse fuer .DE-Ticker)
- **Paris** fuer .PA (Airbus)

## Einheit & Format
- EUR per share als Dezimalzahl: `140.50` fuer 140,50 EUR
- Vorzeichen: immer positiv
- KEINE Skalierung (nicht in Cent, nicht in Mille)

## Sanity-Range (DAX)
- Cent-Aktien: <5 EUR (Commerzbank historisch)
- Standard: 5–200 EUR (Grossteil DAX)
- Hoch-Preisige: 200–800 EUR (MTX, SAP nach Split, MUV2)
- Mega: >800 EUR (Rheinmetall Rueckung 2026, Beiersdorf historisch)

Ausserhalb 1 EUR – 3000 EUR = red flag.

## Anti-Confusion (typische Fehler)

**Adj Close vs Raw Close**:
- Adj Close berucksichtigt Dividenden + Splits (backward-adjustiert)
- Raw Close ist Nominalkurs am Handelstag
- **Fuer historische MC/Rendite-Berechnung**: Adj Close
- **Fuer Sanity-Check gegen aktuelle Kotierungen**: Raw Close
- Konvention: Adj Close nutzen

**Waehrung**:
- SAP handelt auf Xetra (EUR) und NYSE (USD als ADR)
- **Konvention: Xetra-EUR-Kurs verwenden fuer deutsche Firmen**
- Bei ADR: USD nicht verwechseln (typisch ADR-Ratio 1:1 aber nicht immer)

**Preferred vs Common**:
- Bei Dual-Class: der Preferred handelt an der Boerse
- Common (Familien-Anteil) hat keinen Marktpreis
- Convention: Preferred-Kurs auch fuer Common (Approximation fuer MC-Rechnung)

**Split-adjustment**:
- Nach Split (z.B. SAP 4:1 in 2014): alle historischen Kurse zurueck-adjustieren
- Yahoo Adj Close macht das automatisch

**Vor-/Nach-Handel-Kurse**:
- Nur regulaerer Schluss-Kurs, keine Pre-Market/After-Hours

## Cross-References
1. **market_cap = stock_price × shares_outstanding** (Ankerformel)
2. **eps_diluted × pe_ratio ≈ stock_price** (approximativ)
3. **actual_return = stock_price_end / stock_price_start - 1** (fuer FY)

## Output-Format (Agent-Response)
```json
{
  "ticker": "MRK.DE",
  "period_year": 2025,
  "period_type": "FY",
  "stock_price_eur": 134.66,
  "reference_date": "2024-12-31",
  "source": "Yahoo Adj Close",
  "source_url": "https://finance.yahoo.com/quote/MRK.DE/history"
}
```

## Referenz-Beispiele
| Ticker | Stichtag | Kurs korrekt | Falsches Muster |
|---|---|---|---|
| SAP.DE | 31.12.2024 | 234,50 EUR | 234,50 USD (falsche Waehrung) |
| MRK.DE | 31.12.2024 | 134,66 EUR (Ordinary) | 200 EUR (Common-Schaetzung, existiert nicht) |
| PAH3.DE | 31.12.2024 | ~33 EUR (Preferred) | 30 EUR (approx Common, aber Preferred hat den Marktpreis) |

## Query-Template fuer Agent
```
Fuer {ticker} zum Stichtag {reference_date}:
1. Yahoo Finance / Bloomberg: Adj Close am {reference_date}
2. Fuer .DE Ticker: Xetra-Kurs (EUR)
3. Fuer .PA: Paris-Kurs (EUR)
4. Bei Dual-Class: der PREFERRED-Kurs (boersengehandelt)
5. Verifiziere: stock_price × shares_outstanding ≈ Bloomberg Referenz-MC ±2%
```
