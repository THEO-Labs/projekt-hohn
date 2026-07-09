---
key: net_income
label_de: Nettogewinn
category: NI_GROWTH
data_type: NUMERIC
unit: EUR (fuer QIA: USD) — absolute Waehrungseinheiten
---

# net_income — Nettogewinn / Konzernergebnis

## Definition
**IFRS Konzernergebnis nach Steuern und Minderheiten**, aus fortgefuehrten UND aufgegebenen Geschaeftsbereichen (Total). Positive Werte = Gewinn, negative = Verlust.

Bei Konzernen mit Minderheiten (VOW3, DBK, RWE): **Net income attributable to shareholders of parent** (nicht Total inkl. NCI).

## Quelle im Report
1. **Konzern-GuV** → Zeile "Konzernergebnis" / "Net income" / "Profit for the period"
2. Bei Split "attributable to shareholders of parent" vs "attributable to non-controlling interests" → **parent-attributable nutzen**

## Einheit & Format
- **Absolute EUR** (fuer QIA: USD)
- KEINE Mio-Skalierung
- Vorzeichen: **negativ bei Verlust** (z.B. BAYN 2025 = -3.620.000.000)

## Sanity-Range
- Kleine DAX: 100M–1 Mrd
- Mittlere: 1–5 Mrd
- Grosse: 5–20 Mrd (SAP, DTE)
- Absoluter Verlust: bis -10 Mrd realistisch (BAYN historisch mit Impairments)
- Extreme: nur bei Sondereinfluessen

**ni_margin = NI / Revenue** typisch:
- Banken/Versicherer: 3-15%
- Software/Chemie: 5-20%
- Retail/Auto: 2-8%
- Utilities: 3-10%

## Anti-Confusion (typische Fehler)

**Reported (IFRS) vs Adjusted (Non-IFRS)**:
- BAYN 2024: reported -2,55 Mrd EUR (mit Monsanto-Impairment), adjusted +4,5 Mrd
- ENR 2024: reported -1,6 Mrd (Wind-Sparte), adjusted positiv
- Konvention: **IFRS reported** (mit allen Sondereffekten)

**Continuing vs Total Operations**:
- Continental 2025: reported ist Total inkl. Aumovio-Zeit
- Konvention: **Total** (fuer Konsistenz mit revenue)

**Parent vs Total inkl. NCI**:
- VW: 2024 Net income 15 Mrd Total, 12 Mrd shareholders-of-parent
- Konvention: **shareholders-of-parent** (nicht Total)
- DBK: analog

**Diluted vs Basic**:
- net_income ist absolute Zahl (nicht per share) - keine dilution-Frage
- (dilution ist bei eps_diluted relevant)

**Non-recurring items**:
- Gewinn aus Spin-off (Continental/Aumovio 2025): Teil des reported NI
- One-off tax gains: Teil des reported NI
- Konvention: **wie reported, nicht bereinigt**

## Cross-References
1. **Q-Sum = FY** (exakt bei actuals)
2. **ni_margin = NI / Revenue × 100** in Sektor-Range
3. **pe_ratio = MC / NI** (nur wenn NI > 0)
4. **ni_growth = (NI_curr - NI_prev) / abs(NI_prev) × 100**
5. **eps_diluted = NI / shares_outstanding** (approximativ)

## Output-Format (Agent-Response)
```json
{
  "ticker": "BAYN.DE",
  "period_year": 2025,
  "period_type": "FY",
  "net_income_eur": -3620000000,
  "attributable_to": "shareholders of parent",
  "includes_discontinued": true,
  "source_report": "Bayer Annual Report 2025",
  "source_url": "https://www.bayer.com/.../annual-report-2025.pdf",
  "reconciliation": {
    "core_eps_adjusted": 5.68,
    "reported_eps": -3.68,
    "explanation": "Monsanto Impairment 4.7 Mrd, Restructuring 1.2 Mrd, Legal Reserves 3.5 Mrd"
  }
}
```

## Referenz-Beispiele
| Ticker | period_year | Correct | Falsches Muster |
|---|---|---|---|
| BAYN.DE | 2025 | **-3.620.000.000** (Verlust) | +5.680.000.000 (Adjusted Core EPS × Shares) |
| ENR.DE | 2024 | **-1.600.000.000** | Positive Adjusted-Zahl |
| VNA.DE | 2024 | **~-6.700.000.000** (IFRS Gruppenergebnis nach Immobilien-Abwertungen) | -965M (Adjusted Group FFO) oder positiv (Adjusted Result) |
| SAP.DE | 2025 | 7.492.000.000 EUR | 7.492 Mio USD (Currency) |
| PAH3.DE | 2026 | -923.000.000 (Q1) | Positive Beteiligungsergebnis-Schaetzung |

## Query-Template fuer Agent
```
Fuer {ticker} in Periode {period_year} {period_type}:
1. Konzern-GuV -> "Konzernergebnis" / "Net income for the period"
2. IFRS reported (mit Sondereffekten, NICHT Core/Adjusted)
3. "Attributable to shareholders of parent" (nicht Total inkl. NCI)
4. Inklusive discontinued operations (fuer Konsistenz)
5. Vorzeichen: negativ bei Verlust
6. Wenn Discrepancy zu Adjusted > Faktor 2: separate im JSON reconciliation-Block dokumentieren
```
