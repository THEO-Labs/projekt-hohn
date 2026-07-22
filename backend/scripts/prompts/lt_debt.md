---
key: lt_debt
label_de: Langfristige Schulden
category: DEBT
data_type: NUMERIC
unit: Berichtswaehrung der Firma (absolute Einheiten)
---

# lt_debt — Langfristige Finanzverbindlichkeiten

## Definition
**Non-current financial liabilities** aus der Bilanz mit Restlaufzeit >12 Monate. Umfasst langfristige Anleihen, langfristige Bank-Kredite, langfristige Leasingverbindlichkeiten (IFRS 16), Convertible Bonds (zum Book Value).

**AUSSCHLIESSEN**: Pensionsrueckstellungen, sonstige langfristige Rueckstellungen, Deferred Tax Liabilities, Other Post-Employment Benefits (OPEB), langfristige Deferred Revenue.

## Quelle im Report
1. **Bilanz Passiva** → "Non-current financial liabilities" / "Langfristige Finanzverbindlichkeiten"
2. **Notes zu Finanzschulden** mit Restlaufzeit-Aufschluesselung
3. **Notes zu Bonds** mit Faelligkeits-Tabelle

## Einheit & Format
- Absolute EUR, POSITIVER Wert
- KEINE Mio-Skalierung

## Sanity-Range (DAX)
- Industrie klein (SAP, BEI, HEN3): 500 Mio – 5 Mrd EUR
- Grosse Industrie (BAS, BAYN, SIE, DHL): 10–40 Mrd EUR
- Autofinancers (VOW3, MBG, BMW): 100–160 Mrd EUR (FS dominiert)
- Utilities/Telco (RWE, EOAN, DTE): 30–120 Mrd EUR (Infrastruktur-Refi)
- Ausserhalb: red flag

## Anti-Confusion (typische Fehler)

**Pensionsrueckstellungen**:
- FALSCH: einbeziehen (Pensions sind eine Ruecklage, nicht Finanzschuld)
- RICHTIG: separater Bilanzposten "Pensionsverpflichtungen"
- Test: Zinstragende Auslieferung an externe Investoren? Nein → keine Finanzschuld

**Current portion of LT debt**:
- Der kurzfristige Teil eines LT-Bonds (naechste 12 Monate faellig) gehoert in **st_debt**
- Konvention: lt_debt = nur >12 Monate Restlaufzeit

**Deferred Tax Liabilities**:
- NICHT einbeziehen (keine Cash-Verpflichtung an externe Investoren)

**Convertible Bonds**:
- Einbeziehen zum Book Value (nach IAS 32 typisch getrennt in Debt- + Equity-Komponente; nur Debt-Komponente)
- Rheinmetall 2025 Convertible ~700M: LT-Debt-Position

**Bond Issuance im Berichtsjahr**:
- Wichtig: der Bilanz-Stichtag zeigt den Stand nach Emission. Netto-Effekt ist positiv (mehr LT-Debt), was auch die Cash-Position erhoeht (Zufluss im CFS).

**Cross-Currency Debt**:
- Zum Stichtags-Kurs bewerten (wie in Bilanz reported)

**IFRS 16 Leasing**:
- Konvention: **einbeziehen** (langfristiger Teil der Lease Liability)
- Erkennbar in Bilanz als "Non-current lease liabilities"

## Cross-References
1. **net_debt = st_debt + lt_debt − cash − st_investments** (Kern-Formel)
2. Sum st_debt + lt_debt = Total Financial Debt (Bruttoverschuldung)

## Output-Format (Agent-Response)
```json
{
  "ticker": "SAP.DE",
  "period_year": 2025,
  "period_type": "FY",
  "lt_debt_eur": 5900000000,
  "includes": ["bonds", "bank_loans_lt", "ifrs16_lease_lt", "convertibles_debt_component"],
  "excludes": ["pensions", "opeb", "deferred_tax", "provisions", "deferred_revenue"],
  "reference_date": "2025-12-31",
  "source_report": "SAP Annual Report 2025 Balance Sheet + Note Financial Liabilities"
}
```

## Referenz-Beispiele (DAX FY 2024)
| Ticker | Correct (Mrd EUR) | Comment |
|---|---|---|
| SAP.DE | **~5–6** | Meist EUR-Bonds und Convertibles |
| BAS.DE | **~15** | Diversifizierte EUR/USD-Bonds |
| VOW3.DE | **~130–150** | FS-Portfolio treibt |
| MBG.DE | **~100** | Analog VW-Struktur |
| BMW.DE | **~110** | Analog VW-Struktur |
| DTE.DE | **~80–90** | T-Mobile US Kredite dominieren |
| BAYN.DE | **~30** | Post-Monsanto-Struktur |
| DHL.DE | **~15** | Infrastruktur-Refi |
| HEI.DE | **~7** | Zement-Sektor Refi |

## Query-Template fuer Agent
```
Fuer {ticker} zum Stichtag Ende {period_year}:
1. Bilanz Passiva -> "Non-current financial liabilities" / "Langfristige Finanzverbindlichkeiten"
2. EINBEZIEHEN: langfr. Bonds, langfr. Bank-Kredite, IFRS 16 (lt), Convertibles (Debt-Komponente)
3. NICHT: Pensions, OPEB, Provisions, Deferred Tax, Deferred Revenue
4. Der kurzfr. Teil eines LT-Bonds gehoert in st_debt (nicht hier)
5. Positive Absolutbetraege
6. Sanity: fuer Industrie 5-40 Mrd, fuer Autofinancers/Telcos deutlich hoeher
```
