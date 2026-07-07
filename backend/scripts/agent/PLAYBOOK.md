# Agent Playbook: Company-Fill

Dieses Playbook wird von Claude Code (nicht der Anthropic-API) ausgefuehrt.
Ziel: fuer Companies aus `QUEUE.md` die kompletten FY2025 + FY2026 Werte
recherchieren und direkt in die Postgres-DB schreiben.

## Grundprinzip

- Jede Zahl kommt aus einer **primaeren Quelle** (Investor-Relations Press-Release,
  8-K Exhibit, 10-Q, 10-K). Keine Aggregatoren (macrotrends, stockanalysis).
- Werte werden als `primary_method = "manual"` gespeichert (Agent-Research =
  authoritativ, wird von Full-Recompute nicht ueberschrieben).
- Existing Rows mit `primary_method IN (pdf, manual)` oder `manually_overridden=True`
  werden **nie ueberschrieben** (User-Data bleibt).

## Ablauf pro Company

Fuer jede Company aus `QUEUE.md` (status = "todo"):

1. **Company-Metadata pruefen**
   - `docker exec projekt-hohn-db-1 psql -U hohn -d hohn -c "SELECT id, ticker, name, fiscal_year_end_month, currency FROM companies WHERE ticker='XXX';"`
   - Falls Company nicht existiert -> user fragen ob anlegen
   - Fiscal Year End merken (Dec-Filer FY = calendar year, non-Dec-Filer FY endet in ihrem FY-Monat)

2. **Existing Daten holen** (nichts kaputt machen)
   ```
   docker exec projekt-hohn-db-1 psql -U hohn -d hohn -c \
     "SELECT value_key, period_type, period_year, primary_method,
      (numeric_value/1000000)::numeric(15,0) AS mio
      FROM company_values WHERE company_id='<id>'
      AND period_year IN (2025,2026)
      ORDER BY value_key, period_year DESC, period_type;"
   ```

3. **Per-Quartal Recherche** (Reihenfolge: Q1 2025 -> Q2 2025 -> Q3 2025 -> Q4 2025 -> FY 2025 -> Q1 2026 -> Q2 2026 -> ggf. Q3 2026 falls bereits reported)

   Fuer jedes Quartal:
   - **WebSearch**: `"<Company> Q<n> fiscal <year> earnings press release"` (fuer non-Dec-Filer: fiscal quarter)
   - Aus Suchresultaten: URL der pressrewire.com / businesswire.com / IR-Site auswaehlen
   - **WebFetch** mit strukturiertem Prompt (siehe Template unten)
   - Fallback: 2. WebSearch mit spezifischeren Zahlen wenn erste Antwort unvollstaendig

   **Fetch-Prompt-Template:**
   ```
   Extract <Company> Q<n> FY<year> (quarter ended <date>) exact numbers in USD millions:
   Revenue, GAAP Net Income, Non-GAAP Net Income, Diluted EPS GAAP,
   Diluted EPS Non-GAAP, Cash from Operations, Capital Expenditures,
   Free Cash Flow, Adjusted EBITDA, Stock-Based Compensation,
   Dividends paid, Share Repurchases. Give exact numbers.
   ```

4. **Full-Year Recherche**
   - Aus dem Q4 Press-Release (der enthaelt immer auch Full-Year)
   - Sanity: `FY_Revenue == Sum(Q1..Q4)_Revenue` (Toleranz 0.5%)
   - Sanity: `FY_NI == Sum(Q1..Q4)_NI` (fuer summable Keys)

5. **Balance-Sheet FY2025** (Stichtag = Fiscal-Year-End)
   - Aus 10-K / Q4-Earnings-Release Balance-Sheet-Sektion
   - Values: Cash and Cash Equivalents, Short-Term Investments (falls reported),
     Short-Term Debt (Current Portion LT Debt + Commercial Paper),
     Long-Term Debt (non-current)

6. **Data-Modul erstellen**: `backend/scripts/agent/companies/<ticker_lower>.py`
   - Basierend auf Template `_TEMPLATE.py` (siehe unten)
   - Alle Werte in Mio USD; EPS als raw ($ per share)
   - Source-Referenz pro Row: `"<Date> 8-K"` oder `"<Date> 10-K"`

7. **Fill ausfuehren**
   ```
   PYTHONPATH=/Users/till-olelohse/projekt-hohn/backend uv run python \
     scripts/agent/fill.py --ticker <TICKER>
   ```

8. **Review**: Nach jedem Insert kurze Zusammenfassung ausgeben (was NEU, was SKIPPED weil pdf/manual)

9. **Queue-Update**: Ticker in `QUEUE.md` von `todo` auf `done` setzen mit Timestamp

## Sanity-Checks pro Company (mandatory)

Vor dem Commit:

- **Summe-Konsistenz**: Q1+Q2+Q3+Q4 der summable Keys (Revenue/NI/OCF/CapEx/FCF/SBC/Div/Buyback)
  muss FY entsprechen (±0.5%)
- **EPS Konsistenz**: Q1_EPS + Q2_EPS + Q3_EPS + Q4_EPS ~ FY_EPS (Toleranz 1% wegen
  weighted-average shares)
- **FCF = OCF - CapEx** (exakt)
- **Non-GAAP >= GAAP** fuer NI (Adj wird typisch nur nach oben adjusted)
- **Buyback + Dividends <= FCF** (in FY, sonst extern finanziert)
- **CapEx immer positiv** (Absolutwert)

Bei Verletzung: **STOPPEN** und user fragen bevor eingespielt wird.

## Template `_TEMPLATE.py`

Siehe `companies/_TEMPLATE.py`. Struktur:
```python
TICKER = "XXX"
COMPANY_NAME = "Company Full Name"
FISCAL_YEAR_END_MONTH = 12  # oder 9, 10, 6 etc.

# Q-Data: (period_type, period_year, source_ref, {key: gaap_mio, key_adj: adj_mio})
Q_DATA = [
    ("Q1", 2025, "Mar 5 2025 8-K", {
        "revenue": (1234, 1234),         # (gaap, adj) — adj gleich = kein separates Non-GAAP
        "net_income": (100, 130),        # 130 = Non-GAAP NI
        "ebitda": (200, 200),
        "operating_cash_flow": (150, 150),
        "capex": (30, 30),
        "fcf": (120, 120),
        "sbc": (20, 20),
        "dividends": (25, 25),
        "buyback_volume": (0, 0),
    }),
    # ... Q2, Q3, Q4, FY
]

EPS_DATA = [
    ("Q1", 2025, ("1.14", "1.60"), "Mar 5 2025 8-K"),  # (gaap $, adj $)
    # ...
]

BS_DATA = {
    2025: {
        "cash_and_equivalents": (16178, "Dec 2025 10-K"),
        "st_debt": (3152, "Dec 2025 10-K"),
        "lt_debt": (61984, "Dec 2025 10-K"),
    },
}
```

## Wann NICHT einspielen (Guard-Rails)

- Wenn der Web-Fetch keine exakte Zahl liefert, sondern Ranges ("etwa 5 Milliarden") -> **skip diese Zelle**, nicht raten
- Wenn Sanity-Check failed -> stopp + user fragen
- Wenn Fiscal-Year-End nicht klar (z.B. neue Company) -> user fragen
- Wenn Company Restatement gemacht hat und alte Zahl von neuer abweicht:
  neue nehmen, alte im source-Feld erwaehnen

## Loop-Verhalten

- Fuer jede Company: recherchieren + einspielen + reviewen
- Nach 5 Companies: Zwischenpause (User-Ansprache mit Progress-Report)
- Bei API-Rate-Limit: 30 Sekunden warten, dann weiter
