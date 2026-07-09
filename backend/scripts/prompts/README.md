# Recherche-Prompts pro Kennzahl

Ein Prompt pro `value_key` fuer LLM-Agents die Q- und FY-Werte aus offiziellen Firmen-Reports extrahieren.

## Warum ein Prompt pro Kennzahl statt einem generischen Prompt?

Der Continental-Case: ein generischer Prompt fragt "Continental Dividende FY 2025" - Agent findet "2,70 EUR/Aktie fuer FY 2025 vorgeschlagen" und schreibt **2,7 Mrd EUR** in die DB (Verwechslung per-share vs. total). Ein kennzahlspezifischer Prompt haette explizit gefordert: "Total Cash-Payout in EUR, NICHT per-share × 100" - Verwechslung ausgeschlossen.

## Prompt-Struktur (jede .md Datei)

- **Definition**: was diese Kennzahl konzeptionell IST (nicht was sie nicht ist)
- **Quelle**: wo im offiziellen Report sie steht (welches Statement, welche Note, welche Zeile)
- **Einheit & Format**: absolute EUR? Millionen? pro Aktie? mit/ohne Vorzeichen?
- **Sanity-Range**: DAX-typische Bandbreite; ausserhalb = red flag
- **Anti-Confusion**: typische Verwechslungen (per-share vs. total, gross vs. net, adjusted vs. reported, continuing vs. discontinued)
- **Cross-References**: muss zu welchen anderen Kennzahlen passen (Q-Sum = FY, MC = P × N, etc.)
- **Output-Format**: exakter JSON-Response-Schema
- **Referenz-Beispiele**: 2-3 DAX-Firmen mit korrekten Werten als Anker

## Verwendung

Diese Prompts sind Template-Bausteine fuer Recherche-Agents. Bei einem Batch-Run wird pro Firma+Kennzahl der jeweilige Prompt aus dieser Datei geladen, mit Ticker/Periode substituiert, und an den Agent uebergeben. **Kein Auto-Deployment** — Prompts werden per Hand review'd und angepasst.

## Kennzahlen-Uebersicht

| value_key | category | source_type | Prompt |
|---|---|---|---|
| revenue | NI_GROWTH | RESEARCH | [revenue.md](revenue.md) |
| net_income | NI_GROWTH | RESEARCH | [net_income.md](net_income.md) |
| ebitda | VALUATION | RESEARCH | [ebitda.md](ebitda.md) |
| eps_diluted | NI_GROWTH | RESEARCH | [eps_diluted.md](eps_diluted.md) |
| fcf | FCF | RESEARCH | [fcf.md](fcf.md) |
| operating_cash_flow | FCF | RESEARCH | [operating_cash_flow.md](operating_cash_flow.md) |
| capex | FCF | RESEARCH | [capex.md](capex.md) |
| sbc | SBC | RESEARCH | [sbc.md](sbc.md) |
| buyback_volume | BUYBACKS | RESEARCH | [buyback_volume.md](buyback_volume.md) |
| dividends | DIVIDENDS | RESEARCH | [dividends.md](dividends.md) |
| cash_and_equivalents | CASH | RESEARCH | [cash_and_equivalents.md](cash_and_equivalents.md) |
| st_investments | CASH | RESEARCH | [st_investments.md](st_investments.md) |
| st_debt | DEBT | RESEARCH | [st_debt.md](st_debt.md) |
| lt_debt | DEBT | RESEARCH | [lt_debt.md](lt_debt.md) |
| net_debt | DEBT | RESEARCH | [net_debt.md](net_debt.md) |
| market_cap | STAMMDATEN | RESEARCH | [market_cap.md](market_cap.md) |
| stock_price | STAMMDATEN | RESEARCH | [stock_price.md](stock_price.md) |
| shares_outstanding | STAMMDATEN | RESEARCH | [shares_outstanding.md](shares_outstanding.md) |

CALCULATED-Kennzahlen (ratios, yields, hohn_returns) haben keine Recherche-Prompts — sie werden aus den API-Werten deterministisch berechnet in `backend/app/calculations/engine.py`.
