---
key: eps_diluted
label_de: Verwaesserter Gewinn je Aktie
category: NI_GROWTH
data_type: NUMERIC
unit: Berichtswaehrung per share — pro Aktie, NIEMALS absolute Total
---

# eps_diluted — Diluted Earnings per Share

## Definition
**IFRS Reported diluted EPS**: net income attributable to shareholders / weighted average diluted shares outstanding (inkl. potential Aktien aus Wandelanleihen, Optionen etc.).

## Quelle im Report
1. **Konzern-GuV Bottom**: "Ergebnis je Aktie (verwaessert)" / "Diluted earnings per share"
2. **Notes to EPS**: mit Erklaerung der Diluted-shares-Berechnung

## Einheit & Format
- **EUR per share** als Dezimalzahl: `1.66` fuer 1,66 EUR
- **NIEMALS in Cent oder als Total** speichern
- Vorzeichen: negativ bei Verlust

## Sanity-Range
- Typisch 0,10 – 50 EUR/Aktie im DAX
- Hoch-EPS: MTX 15-18 EUR, MUV2 30-40 EUR, RHM 40-70 EUR (nach Boom)
- SAP: 5-10 EUR/Aktie
- BEI: 2-5 EUR/Aktie
- DTE: 0,50-1,50 EUR (viele Aktien, niedriges EPS)
- Ausserhalb 0,01 – 100 EUR = red flag

## Anti-Confusion (typische Fehler)

**Per-share vs Total (der Continental-Fallstrick fuer Dividenden)**:
- FALSCH: 1,66 EUR × 1.166M Aktien = 1,94 Mrd als EPS speichern
- RICHTIG: **1.66** (nur per share)

**Reported (IFRS) vs Adjusted/Core**:
- SAP: reported EPS 6.42 vs Non-IFRS EPS 8.15
- BAYN: reported EPS -3.68 vs Core EPS 5.68
- Konvention: **IFRS reported diluted** (mit Sondereffekten)

**Basic vs Diluted**:
- Basic EPS = NI / weighted avg shares (ohne Verwaesserung)
- Diluted EPS = NI / weighted avg diluted shares (mit potentieller Verwaesserung)
- Konvention: **Diluted** (as-if-converted)

**Continuing vs Total**:
- Wenn Firma discontinued had: reported EPS = Total EPS
- Konvention: **Total EPS** (fuer Konsistenz mit net_income)

**Dual-Class-Firmen**:
- Wenn Preferred und Common unterschiedliche EPS haben (rare in DE): Preferred nutzen
- Bei Merck KGaA: einheitliches EPS fuer beide Klassen (Winst dividend Alignment)

## Cross-References
1. **eps_diluted ≈ net_income / shares_outstanding_avg** (approximativ, weil diluted shares abweichen)
2. **pe_ratio = stock_price / eps_diluted** (wenn beide > 0)
3. **Q-Sum ≈ FY** aber NICHT exakt weil weighted avg shares pro Q anders

## Output-Format (Agent-Response)
```json
{
  "ticker": "SAP.DE",
  "period_year": 2025,
  "period_type": "Q1",
  "eps_diluted_eur": 1.52,
  "type": "IFRS reported diluted",
  "diluted_shares_used": 1166000000,
  "source_report": "SAP Q1 2025 Quarterly Statement",
  "note": "Non-IFRS EPS 1.75 zusaetzlich vorhanden - NICHT nutzen"
}
```

## Referenz-Beispiele
| Ticker | period_year | Q | Correct | Falsches Muster |
|---|---|---|---|---|
| SAP.DE | 2025 | Q1 | 1.52 EUR | 1520 (in Cent) oder 1520000 (Total × 1000) |
| BAYN.DE | 2025 | FY | -3.68 EUR (reported) | 5.68 EUR (Core, adjusted) |
| MTX.DE | 2025 | FY | **~15-18 EUR** | 18000000 (Total durch shares vergessen) |
| ZAL.DE | 2026 | Q1 | -0.33 EUR (Verlust) | positive Adjusted-Zahl |

## Query-Template fuer Agent
```
Fuer {ticker} in Periode {period_year} {period_type}:
1. GuV Bottom -> "Diluted earnings per share" / "Ergebnis je Aktie verwaessert"
2. IFRS reported (NICHT Core/Adjusted/Non-IFRS)
3. Total (inkl. discontinued)
4. Gib Dezimalzahl per share (z.B. 1.52), NIEMALS Total × shares
5. Vorzeichen: negativ bei Verlust
6. Sanity: eps × shares_outstanding ≈ net_income (±5%)
```
