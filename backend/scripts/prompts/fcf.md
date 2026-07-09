---
key: fcf
label_de: Free Cash Flow
category: FCF
data_type: NUMERIC
unit: EUR (fuer QIA: USD)
---

# fcf — Free Cash Flow

## Definition
**Free Cash Flow = Operating Cash Flow − CapEx**, klassische Konvention. Bei einigen Firmen abweichende eigene Definition (Airbus "FCF as reported", VW "Automotive Net Cash Flow", Siemens Energy "FCF pre-tax").

## Quelle im Report
1. **Cash Flow Statement**: OCF - CapEx (klassische Ableitung)
2. **Company-eigene FCF-Definition**: aus Press Release / Segmentanalyse
3. Konvention: **beide Zahlen dokumentieren** wenn abweichend, primaer OCF-CapEx nehmen

## Einheit & Format
- Absolute EUR (fuer QIA: USD)
- Vorzeichen: **kann negativ sein** (Q1 saisonal oft negativ bei Auto/Chemie/Retail)

## Sanity-Range
- FCF-Marge (FCF / Revenue) typisch 3-20%
- Q1 saisonal oft negativ oder klein (Working Capital Aufbau)
- Q4 saisonal stark positiv (Working Capital Aufloesung)
- Ausserhalb -30% bis +40% Marge = red flag

## Anti-Confusion (typische Fehler)

**Klassisch (OCF - CapEx) vs Company-eigen**:
- Airbus reported "FCF" = OCF - CapEx + FS-Portfolio-Change (nicht klassisch)
- Brenntag reported "FCF" = OCF - CapEx + Divestitures
- Konvention: **klassisch OCF - CapEx** (bei Abweichung: dokumentieren, klassisch nehmen)

**Continuing vs Total**:
- Wie bei revenue: **Total inkl. discontinued**

**Auto-Konzerne**:
- VW: "Net Cash Flow Automotive" ist eigene VW-Definition (nur Automotive-Sparte, ohne Financial Services)
- MBG: "Free Cash Flow of the Industrial Business"
- Konvention: Total-Konzern-FCF wenn moeglich, sonst company-def dokumentieren

**Pre-tax vs Post-tax**:
- Siemens Energy: "FCF pre tax" ist deren primary
- Konvention: **Post-tax** (klassisch) — wenn nur pre-tax verfuegbar, klar markieren

**CapEx-Definition (siehe capex.md)**:
- Wenn CapEx nur PP&E: FCF = OCF - PP&E-CapEx
- Wenn CapEx PP&E + Intangibles: FCF = OCF - (PP&E + Intangibles)
- Konvention: **Intangibles + PP&E** (breiter CapEx)

## Cross-References
1. **fcf = operating_cash_flow − capex** (Kern-Formel)
2. **Q-Sum = FY** (exakt bei actuals)
3. **fcf_margin = fcf / revenue × 100**
4. **fcf_yield = fcf / market_cap × 100** typisch 2-10% DAX

## Output-Format (Agent-Response)
```json
{
  "ticker": "SAP.DE",
  "period_year": 2025,
  "period_type": "Q1",
  "fcf_eur": 3583000000,
  "derivation": "OCF 3780 − CapEx 197 = 3583",
  "type": "IFRS classical (OCF - CapEx)",
  "company_own_fcf_if_diff": null,
  "source_report": "SAP Q1 2025 Quarterly Statement"
}
```

## Referenz-Beispiele
| Ticker | period_year | Q | Correct | Falsches Muster |
|---|---|---|---|---|
| SAP.DE | 2025 | Q1 | 3.583 Mio EUR (OCF - CapEx) | Andere Zahlen bei Company-Def |
| AIR.PA | 2025 | Q4 | 5.531 Mio EUR (Airbus-eigene Def) vs 4.031 klassisch | Ohne Klarheit |
| VOW3.DE | 2025 | Q4 | 4.648 (Net Cash Flow Automotive) vs Konzern-Total | Nur eine Kennung |
| BAS.DE | 2025 | Q1 | **-1.798 Mio** (Working Capital Aufbau) | positive Schaetzung |

## Query-Template fuer Agent
```
Fuer {ticker} in Periode {period_year} {period_type}:
1. Cash Flow Statement -> OCF und CapEx separat extrahieren
2. FCF = OCF - CapEx (klassisch)
3. Wenn Company-eigene FCF-Def abweicht (Airbus, VW, ENR): beide Werte dokumentieren, klassisch als primaer
4. Total inkl. discontinued
5. Q1 saisonal oft negativ - kein Fehler
6. Sanity: fcf/revenue in -30%..+40% Range
```
