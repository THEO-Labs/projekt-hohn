---
key: ebitda
label_de: EBITDA
category: VALUATION
data_type: NUMERIC
unit: EUR (fuer QIA: USD)
---

# ebitda — Earnings Before Interest, Tax, Depreciation, Amortization

## Definition
**Reported IFRS EBITDA** = Operating Profit (EBIT) + Depreciation + Amortization. Bei Firmen die kein IFRS-EBITDA reporten: EBIT + explicit D&A aus CF-Statement.

## Quelle im Report
1. **Konzern-GuV oder Notes**: "EBITDA" wenn explizit gezeigt
2. **Aus EBIT ableiten**: EBIT + Abschreibungen aus CF-Statement (Anpassung Non-Cash)
3. **Segment-Report**: manchmal EBITDA pro Segment mit Konzern-Sum

## Einheit & Format
- Absolute EUR
- KEINE Mio-Skalierung
- Vorzeichen: typisch positiv, kann bei Verlust negativ sein (ZAL Q1 2026: -80M)

## Sanity-Range
- EBITDA-Marge typisch 8–35% je Sektor:
  - Software (SAP): 25-35%
  - Chemie (BAS): 8-15%
  - Auto (BMW, VOW3): 8-15%
  - Utilities (RWE, EOAN): 15-25%
  - Retail/Fashion (ADS, ZAL): 8-15%
  - Telco (DTE): 30-40%
- Ausserhalb -5% bis +50% = red flag

## Anti-Confusion (typische Fehler)

**Reported IFRS EBITDA vs Adjusted EBITDA — beide sind MUST-HAVE**:
- **RWE, Bayer, MRK, E.ON, Merck KGaA, DTE, ENR** publizieren beides
  separat und prominent — extrahiere BEIDE Zahlen in einem Extraktions-Aufruf.
- `numeric_value` = IFRS reported EBITDA (aus dem Cash Flow Statement bzw.
  Konzern-GuV: EBIT + Depreciation + Amortization). Wenn die Firma
  reported EBITDA nicht direkt in der GuV zeigt: aus EBIT + D&A des CFS
  ableiten und in source_quote als "EBIT X + D&A Y = reported EBITDA Z"
  dokumentieren. NIE null lassen bei diesen Firmen — Reported IFRS ist
  immer aus den GuV-Komponenten ableitbar.
- `adjusted_value` = die Adjusted / Bereinigt / Core / Pre Variante:
  - RWE: "Bereinigtes EBITDA" (excl. Derivat-Bewertungseffekte)
  - Bayer: "EBITDA vor Sondereinfluessen"
  - MRK Merck KGaA: "EBITDA Pre" (excl. Sondereinfluesse)
  - E.ON: "Adjusted EBITDA" (excl. non-operating items, restructuring)
  - Deutsche Telekom: "Adjusted EBITDA AL" (After Leases)
  - Siemens Energy: "Adjusted EBITA vor Sondereffekten"
  - `adjustments_note` listet was rausgestrippt wurde
- Konvention: **immer beide extrahieren** wenn die Firma beide reported.
  Nur wenn Firma wirklich KEIN Adjusted publiziert (Continental, Vonovia,
  meiste Banken): adjusted_value=null. Reported ist IMMER Pflicht.

**EBIT vs EBITDA**:
- Verwechsel nicht: EBIT = EBITDA - D&A
- Wenn nur EBIT reported: EBITDA = EBIT + D&A(aus CF)

**Banken/Versicherer**:
- Haben KEINE EBITDA-Konzept
- Konvention: **null** fuer DBK, CBK, ALV, MUV2, HNR1

**Auto mit Financial Services**:
- VOW3, MBG: EBITDA nur Automotive OR Total inkl. FS
- Konvention: **Total inkl. FS**

**Fiskaljahr**:
- IFX/SIE/SHL/ENR: Kal-Q1 2025 = FQ2 FY25

## Cross-References
1. **Q-Sum = FY** (exakt bei actuals)
2. **ebitda_margin = EBITDA / Revenue × 100** in Sektor-Range
3. **ev_ebitda = (MC + net_debt) / EBITDA** typisch 5-25
4. EBITDA > EBIT (D&A ist positiv)
5. EBITDA > net_income + tax + interest

## Output-Format (Agent-Response)
```json
{
  "ticker": "SAP.DE",
  "period_year": 2025,
  "period_type": "Q1",
  "ebitda_eur": 2333000000,
  "type": "IFRS reported",
  "derivation": "Operating Profit 2003 + D&A 330 from CFS",
  "source_report": "SAP Q1 2025 Quarterly Statement",
  "note": "Wenn Firma nur adjusted reported -> null"
}
```

## Referenz-Beispiele
| Ticker | period_year | Q | Correct | Falsches Muster |
|---|---|---|---|---|
| SAP.DE | 2025 | FY | 9.830 Mio EUR (IFRS) | 12.500 Mio (Non-IFRS Cloud EBITDA) |
| RWE.DE | 2026 | Q1 | **null** (nur adjusted 1,63 Mrd) | 1,63 Mrd (adjusted eingetragen) |
| DBK.DE | * | * | **null** immer (Bank) | 3 Mrd (Netto-Zinsertrag verwechselt) |
| MRK.DE | 2026 | Q1 | **null** (nur "EBITDA Pre" 1,53 Mrd) | 1,53 Mrd (das ist Pre, nicht reported) |

## Query-Template fuer Agent
```
Fuer {ticker} in Periode {period_year} {period_type}:
1. Suche im Konzern-Report explizit "EBITDA" (nicht "Adjusted EBITDA", nicht "EBITDA Pre")
2. Wenn nicht direkt reported: EBIT + D&A aus Cash Flow Statement ableiten
3. Wenn Firma nur adjusted/pre-Sondereinfluesse reported: null, KEINE Adjusted eintragen
4. Banken (DBK/CBK) und Versicherer (ALV/MUV2/HNR1): immer null
5. Gib absolute EUR, Vorzeichen wie reported
```
