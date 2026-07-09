---
key: capex
label_de: CapEx (Investitionen in Sachanlagen + Immat.)
category: FCF
data_type: NUMERIC
unit: EUR (fuer QIA: USD)
---

# capex — Capital Expenditure

## Definition
**Cash-Ausgabe fuer Investitionen** in Sachanlagen (PP&E) + immaterielle Vermoegenswerte (Intangibles, exkl. Goodwill aus Akquisitionen) aus dem Cash Flow Statement (Investing Activities).

## Quelle im Report
1. **Cash Flow Statement -> Investing Activities**:
   - "Purchases of property, plant and equipment"
   - "Purchases of intangible assets"
   - Beide addieren
2. **Notes zu Investitionen**: Segment-Aufschluesselung

## Einheit & Format
- **Absolute EUR, POSITIVER Wert** (auch wenn im CFS als negativer Cash-Outflow dargestellt)
- KEINE Mio-Skalierung

## Sanity-Range
- CapEx-Intensity (CapEx / Revenue) typisch:
  - Utilities (RWE, EOAN): 15-30%
  - Auto (VOW3, MBG, BMW): 5-10%
  - Chemie (BAS): 5-8%
  - Software (SAP): 2-4%
  - Telco (DTE): 12-18%
- Ausserhalb 0-40% = red flag

## Anti-Confusion (typische Fehler)

**Vorzeichen** (Merck/MTX-Fall):
- FALSCH: negative Werte speichern (-487M) weil CFS negativ zeigt
- RICHTIG: **positiver Absolutbetrag** (487M)

**Nur PP&E vs PP&E + Intangibles**:
- Konvention: **BEIDE addieren** (PP&E + Intangibles ohne Goodwill)
- Bei Firmen die nur PP&E reporten: Intangibles versuchen zu finden

**Akquisitionen ausschliessen**:
- "Acquisitions of businesses" ist KEIN CapEx sondern M&A
- "Purchases of investments" ist auch keine CapEx (Finanzanlagen)

**Wachstums- vs Erhaltungs-CapEx**:
- Manche Firmen splitten (Growth vs Maintenance)
- Konvention: **Total (Growth + Maintenance)**

**Leasing (IFRS 16)**:
- Nach IFRS 16: Right-of-Use Assets → separate CapEx-Line "Additions to right-of-use assets"
- Konvention: **NICHT einbeziehen** in capex (klassisch nur Cash-CapEx)

**Segment-Split**:
- Automotive vs Financial Services (VOW3, MBG): Total-Konzern-CapEx
- Continuing vs discontinued: Total

## Cross-References
1. **fcf = ocf - capex** (Kern-Formel)
2. **Q-Sum = FY** exakt
3. **capex_intensity = capex / revenue** in Sektor-Range

## Output-Format (Agent-Response)
```json
{
  "ticker": "SAP.DE",
  "period_year": 2025,
  "period_type": "Q1",
  "capex_eur": 197000000,
  "breakdown": {
    "ppe": 123000000,
    "intangibles": 74000000
  },
  "excludes": ["acquisitions", "right-of-use additions", "financial investments"],
  "source_report": "SAP Q1 2025 CFS"
}
```

## Referenz-Beispiele
| Ticker | period_year | Q | Correct | Falsches Muster |
|---|---|---|---|---|
| SAP.DE | 2025 | Q1 | 197 Mio (positiver Absolutbetrag) | -197 Mio (CFS-Vorzeichen behalten) |
| MRK.DE | 2025 | Q1 | 487 Mio (positiv) | -487 Mio |
| VOW3.DE | 2025 | Q4 | 5.644 Mio (Total inkl. Automotive+FS) | 3.500 (nur Automotive) |
| EOAN.DE | 2026 | Q1 | 1.400 Mio (Investitionen aus IR) | 500 (nur PP&E ohne Networks-Investments) |

## Query-Template fuer Agent
```
Fuer {ticker} in Periode {period_year} {period_type}:
1. CFS Investing Activities -> Purchases PP&E + Purchases Intangibles
2. NICHT einbeziehen: Acquisitions, Financial Investments, Right-of-Use Additions
3. Als POSITIVER Absolutbetrag speichern (nicht negativ)
4. Total-Konzern (Automotive + FS)
5. Sanity: capex/revenue in Sektor-Range (0-40%)
```
