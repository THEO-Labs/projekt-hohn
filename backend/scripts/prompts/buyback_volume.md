---
key: buyback_volume
label_de: Buyback-Volumen (Cash-Ausgabe)
category: BUYBACKS
data_type: NUMERIC
unit: EUR (fuer QIA: USD) — Cash-Ausgabe fuer Aktienrueckkaufe
---

# buyback_volume — Aktienrueckkauf Cash-Volumen

## Definition
**Gross Cash-Ausgabe fuer Aktienrueckkaufe** in der Periode, aus dem Cash Flow Statement (Financing Activities) oder Statement of Changes in Equity.

## Quelle im Report
1. **CFS Financing Activities**: "Purchase of treasury shares" / "Aktienrueckkaufe"
2. **Statement of Changes in Equity**: "Treasury shares" negative Bewegung
3. **IR-Press Releases**: Buyback-Programm-Volumen und -Zeitraum

## Einheit & Format
- **Absolute EUR (Cash-Outflow)** als POSITIVER Wert
- **KEINE per-share-Werte**
- (Falls Firma nur Anzahl Aktien reported: × durchschnittlicher Kurs)

## Sanity-Range
- Buyback-Yield (Buyback / MC) typisch 0-5% DAX
- Grosse Programme: bis 10% (BAS 2007, DTE 2025)
- Ausserhalb 0-15% = red flag

## Anti-Confusion (typische Fehler)

**Per-share vs Total** (der Dividenden-Fallstrick):
- FALSCH: "2,70 EUR pro Aktie zurueckgekauft" als 2,7 Mrd interpretieren
- RICHTIG: **Total Cash Outflow** aus CFS

**Programm-Ankuendigung vs Ausfuehrung**:
- SAP kuendigte 10 Mrd Programm an (2023-2025)
- Ausgefuehrt in 2025: nur ~2,5 Mrd (nicht 10 Mrd)
- Konvention: **tatsaechlicher Cash-Outflow der Periode**, nicht Programm-Volumen

**Programm ueber mehrere Quartale**:
- DBK 750M-Programm April-September 2025 gestartet: pro-rata auf Q2/Q3 splitten
- Realistisch: 750M / (Tage) × Q-Tage

**Buyback vs Kapitalerhoehung**:
- Umgekehrt: Neuemission ist NEGATIVER Buyback
- Konvention: nur Rueckkaufe zaehlen, keine Emissionen

**Treasury Shares Cancellation vs Buyback**:
- Cancellation ist bilanzieller Vorgang (Reduktion Grundkapital), nicht Cash
- Nur der Cash-Rueckkauf ist buyback_volume

**Employee Stock Plans (SBC-Bewegung)**:
- Wenn Firma Aktien fuer SBC zurueckkauft: technisch Buyback, aber typisch klein
- Konvention: **einbeziehen wenn im CFS als Cash-Rueckkauf**

**Vorzeichen**:
- CFS zeigt negativ (Cash-Outflow) → als **positiver Absolutbetrag** speichern
- MUV2 Report: -1,878M → speichern als 1.878M

**Fiskaljahr-Konzerne**:
- Wie ueberall: auf Kalender-Q mappen

## Cross-References
1. **buyback_yield = buyback / market_cap × 100**
2. **net_buyback = buyback_volume - sbc** (Hohn-Formel)
3. **Q-Sum = FY** exakt

## Output-Format (Agent-Response)
```json
{
  "ticker": "SAP.DE",
  "period_year": 2025,
  "period_type": "FY",
  "buyback_volume_eur": 2500000000,
  "type": "Cash outflow (gross)",
  "programme_announced": 10000000000,
  "programme_executed_ytd": 2500000000,
  "source_report": "SAP Q4 2025 CFS Purchase of Treasury Shares",
  "note": "Cash outflow NOT programme announcement"
}
```

## Referenz-Beispiele
| Ticker | period_year | Q | Correct | Falsches Muster |
|---|---|---|---|---|
| SAP.DE | 2025 | FY | 2.500 Mio (ausgefuehrt) | 10.000 Mio (angekuendigtes Programm) |
| CBK.DE | 2025 | Q1 | 528 Mio (Programm #3 400M + Ueberhang #2 128M) | 400M (nur ein Programm) |
| BAYN.DE | * | * | **0** (kein Buyback Bayer) | Positive Schaetzung |
| MUV2.DE | 2025 | FY | 1.878 Mio (positiv gespeichert, CFS neg) | -1.878 (negatives Vorzeichen) |
| DBK.DE | 2025 | Q2 | 406 Mio (pro-rata Apr-Sep 750M-Programm) | 750M (nicht anteilig) |

## Query-Template fuer Agent
```
Fuer {ticker} in Periode {period_year} {period_type}:
1. CFS Financing -> "Purchase of treasury shares" (Cash-Outflow)
2. Als POSITIVER Absolutbetrag speichern
3. Tatsaechlicher Cash-Outflow (NICHT Programm-Ankuendigung)
4. Programme ueber mehrere Perioden: pro-rata auf Kalender-Q verteilen
5. Employee Stock Plan Buybacks einbeziehen wenn im CFS
6. Sanity: buyback/mc in 0-15%
```
