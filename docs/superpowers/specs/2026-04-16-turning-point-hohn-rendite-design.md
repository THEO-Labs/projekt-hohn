# Turning Point Hohn-Rendite Tool - Design

**Datum:** 2026-04-16
**Kunde:** Turning Point Investments (Dr. Ernst Ludes)
**Status:** Design Draft

## Ziel

Internes Webtool fuer Turning Point Investments, mit dem Portfolios von Firmen verwaltet und die erweiterte Hohn-Rendite pro Firma/Zeitraum interaktiv berechnet werden kann. Daten kommen aus mehreren Finanz-APIs, qualitative Faktoren werden durch Claude recherchiert und durch Gemini reviewt. Nutzer koennen pro Wert die Quelle einsehen und qualitative Werte per Chat mit Claude verfeinern.

**UI-Sprache:** Deutsch. Finanzbegriffe bleiben in Englisch (z.B. "Market Cap", "EBITDA", "FCF Yield"). "Hohn-Rendite" bleibt als Eigenname deutsch.

## Scope

### In Scope (Phase 1)
- Login fuer 2-5 interne Nutzer (Email + Passwort, eine Rolle)
- Portfolios anlegen und verwalten
- Firmen in Portfolios anlegen (Ticker, Name, Currency, optional ISIN)
- Eine hypothetische Position pro Firma (Purchase Date, Price, Volume)
- Excel-artiges Dashboard pro Firma mit allen Werten in 8 Kategorien
- Zeit-Selector (Single-Year und Year-Range)
- Automatische Daten-Recherche beim Anlegen einer Firma (API-Werte + LLM-Recherche qualitativer Werte mit Gemini-Review)
- On-Demand Chat mit Claude pro qualitativem Wert
- Manuelles Ueberschreiben jedes Wertes mit Quelle
- Berechnung: Hohn-Rendite (basic 1), Hohn-Rendite (basic 2), Adjusted Hohn-Rendite
- Deployment auf AWS Lightsail, DB auf AWS RDS PostgreSQL

### Out of Scope (Phase 1)
- Mehrere Positionen/Szenarien pro Firma (TBD spaeter)
- Value-History / Versionierung
- Rollen-System (Admin/Analyst)
- CI/CD-Pipeline (nice to have, Phase 2)
- E2E-Tests mit Playwright (Phase 2)
- Mobile-Optimierung

## Wert-Katalog (8 Kategorien)

Statisch per Seed-Migration. `value_definitions`-Tabelle haelt den Katalog.

1. **Transaction Data** - Datum, Next Earnings, Currency, Actual stock price, Purchase Price, Volume, Investment, Actual Investment, Profit/Loss, Change %, Dividends, Dividend return, Total Profit/Loss, Total return, Analysts Target, Potential Change
2. **Basic Company Data** - Market Cap, Shares outstanding, Sales, Sales Growth, Op. Margin, Op. Profit, Net Profit, Op. Cash Flow, Free Cash Flow, Cash, Debt, Leasing Liabilities, Net debt, EV, Ebitda/op. CF
3. **Hohn Return (basic 1)** - Dividend return, EPS ttm, EPS ttm adjusted, EPS 26, EPS Growth, Buybacks, Buyback return, Hohn-Rendite
4. **Hohn Return (basic 2)** - FCF yield 26, EPS Growth, Hohn-Rendite
5. **Valuation Adjustments** - PE ttm, PE ratio, PE Target, PE Target Analysts, Upside Potential, EV/Ebitda, PEG, Judgement
6. **Risk Adjustments** - Business Model, Regulatory, Macro, Risk factor
7. **Management Adjustments** - Participation, Insider Transactions, Age, Mgt. factor
8. **Total Adjustments (Factor)** - aggregiert aus 5-7

Jeder Wert hat einen `source_type`: USER_INPUT, API, CALCULATED oder QUALITATIVE.
Berechnete Werte werden zur Request-Zeit kalkuliert, nicht persistiert.

**Hinweis zu mehrfach genannten Werten:** "Dividend return" und "EPS Growth" tauchen in mehreren Kategorien oben auf, weil sie Bestandteil mehrerer Hohn-Rendite-Formelgruppen sind. Im Katalog (`value_definitions`) gibt es trotzdem nur **einen** Eintrag pro Wert mit **einer** Hauptkategorie (Zuordnung mit Dr. Ludes klaeren). Alternativ kann der Katalog spaeter eine n:m-Beziehung zu Kategorien bekommen, falls zwingend doppelte Anzeige gewuenscht.

## Tech-Stack

- **Backend:** FastAPI (Python 3.12), SQLAlchemy 2.x, Alembic, pydantic-settings
- **Frontend:** Vite + React + TypeScript + Shadcn/ui + Tailwind
- **DB:** PostgreSQL auf AWS RDS
- **LLM:** Anthropic Python SDK (Claude Opus 4.6 fuer Research, Sonnet 4.6 fuer Chat), Google Gemini SDK
- **HTTP-Client:** `httpx` (async)
- **Deployment:** Single Docker-Container auf AWS Lightsail, Backend serviert das gebuilte React-Frontend als Static Files
- **Auth:** JWT in HttpOnly-Cookie
- **Secrets:** ENV via pydantic-settings, `.env`-File auf Lightsail

## Architektur

```
Browser (React + Vite + TS + Shadcn/ui)
    |
    | HTTPS (JWT-Cookie)
    v
FastAPI
    |-- auth/          (Login, JWT, deps)
    |-- portfolios/    (CRUD)
    |-- companies/     (CRUD)
    |-- values/        (refresh, override, get-with-source)
    |-- providers/     (Pluggable API-Adapters + Registry)
    |-- calculations/  (Hohn-Rendite Formeln, reine Funktionen mit Decimal)
    |-- llm/           (Claude + Gemini, Conversations, Streaming via SSE)
    |-- jobs/          (BackgroundTasks + jobs-Tabelle fuer Status-Polling)
    |
    v
PostgreSQL (RDS)        External APIs (Yahoo, Alpha Vantage, ..., Anthropic, Gemini)
```

**Kern-Entscheidungen:**
- Single Container: FastAPI serviert `/api/*` und mountet das React-`dist/` unter `/`. Kein separater Reverse-Proxy.
- JWT im HttpOnly-Cookie (kein CORS-Stress, kein XSS-Token-Leak).
- Provider-Pattern: jeder Wert kann mehrere Provider haben, Priority-Reihenfolge in einer Config. Erster non-None gewinnt.
- Hintergrund-Jobs: FastAPI `BackgroundTasks` + `jobs`-Tabelle. Kein Redis/Celery (Overkill bei 2-5 Nutzern).
- Alle Geld-/Kennzahlen-Berechnungen in `Decimal` (keine Float-Drift).
- Berechnete Werte werden nicht persistiert, sondern zur Request-Zeit aus den Inputs kalkuliert.

## Datenmodell

```
users
  id (uuid), email (unique), password_hash, created_at, last_login_at

portfolios
  id (uuid), name, owner_user_id -> users.id, created_at, updated_at

companies
  id (uuid), portfolio_id -> portfolios.id,
  name, ticker, isin (optional), currency,
  created_at, updated_at

positions   # 1:1 mit company, hypothetische Position (TBD: spaeter evtl. mehrere Szenarien)
  id (uuid), company_id -> companies.id (UNIQUE),
  purchase_date, purchase_price (numeric), volume (numeric),
  updated_at

value_definitions   # Katalog, per Seed befuellt
  key (z.B. "market_cap"),
  label_de, label_en,
  category (enum: TRANSACTION | BASIC_COMPANY | HOHN_BASIC_1 | HOHN_BASIC_2 |
            VALUATION_ADJ | RISK_ADJ | MGMT_ADJ | TOTAL_ADJ),
  source_type (enum: USER_INPUT | API | CALCULATED | QUALITATIVE),
  data_type (enum: NUMERIC | TEXT | FACTOR),
  unit (optional: "EUR", "%", "Mio")

company_values   # Ein Eintrag pro (company, value_key, period_year, is_forecast)
  id (uuid),
  company_id -> companies.id,
  value_key -> value_definitions.key,
  period_year (int, nullable),      # NULL = Snapshot/Current/Qualitativ
  is_forecast (bool, default false),
  numeric_value (numeric, nullable),
  text_value (text, nullable),
  source_name (text, kontrolliertes Vokabular - siehe unten),
  source_link (optional URL),
  fetched_at,
  manually_overridden (bool),
  UNIQUE (company_id, value_key, period_year, is_forecast)

llm_conversations   # Eine Conversation pro (company, value_key)
  id (uuid),
  company_id -> companies.id,
  value_key,
  created_at, updated_at

llm_messages
  id (uuid),
  conversation_id -> llm_conversations.id,
  role (user | claude | gemini | system),
  content (text),
  created_at

jobs
  id (uuid),
  type (enum: REFRESH_API_VALUES | LLM_RESEARCH | GEMINI_REVIEW),
  status (pending | running | done | failed),
  company_id (nullable),
  result (jsonb, nullable),
  error (text, nullable),
  created_at, finished_at
```

**Kontrolliertes Vokabular fuer `source_name`** (Konstante im Backend, kein DB-Enum damit neue Provider ohne Migration moeglich sind):
- `"<Provider-Name>"` - Wert kommt aus einem registrierten API-Provider (z.B. `"Yahoo Finance"`, `"Alpha Vantage"`)
- `"User Input"` - vom Nutzer beim Anlegen eingetragen (Transaction-Daten)
- `"Manual Override"` - User hat einen API/berechneten Wert manuell ueberschrieben
- `"Claude+Gemini"` - Auto-Recherche mit Konsens
- `"Manual via Claude Chat"` - vom User aus Chat uebernommen

**Zeit-Dimension fuer Werte:**

| Wert-Typ                    | period_year     | Beispiel                          |
|-----------------------------|-----------------|-----------------------------------|
| Aktueller Snapshot          | NULL            | Market Cap heute, Stock Price     |
| Historisches Geschaeftsjahr | 2024            | Sales FY2024, EPS ttm 2024        |
| Forecast                    | 2026 + forecast | EPS 26                            |
| Qualitatives Urteil         | NULL            | Judgement, Business Model Risk    |

## User-Flows

### 1. Firma anlegen (mit Auto-Recherche)

```
User: "Firma hinzufuegen" -> tippt Ticker + Name + Currency
POST /api/portfolios/{id}/companies
Backend: legt company an,
         erstellt Job REFRESH_API_VALUES (background)
         erstellt Job LLM_RESEARCH (background, qualitative Werte)
Response: company-Objekt + 2 job_ids
Frontend: zeigt Firma direkt an, pollt /api/jobs/{id} alle 2s
Wenn API-Job done: API-Werte in Tabelle
Wenn LLM_RESEARCH done: startet automatisch GEMINI_REVIEW-Job
Wenn Review done: qualitative Werte in Tabelle mit Quelle "Claude+Gemini"
```

### 2. Wert refreshen

```
User: Refresh-Icon (pro Wert oder fuer alle API-Werte)
POST /api/companies/{id}/values/refresh?keys=market_cap,sales
Provider Registry: versucht Provider in Priority-Reihenfolge, erster Erfolg gewinnt
Schreibt in company_values, source_name = Provider-Name
```

### 3. Quelle anschauen

```
User: Klick auf Wert in Tabelle
Popover zeigt: source_name, source_link (klickbar), fetched_at,
               "Manuell ueberschrieben"-Badge falls gesetzt
Bei qualitativen Werten: Button "Chat oeffnen"
```

### 4. Qualitativen Wert via Chat verfeinern

```
User: "Chat oeffnen" auf z.B. "Business Model"
Drawer oeffnet, laedt llm_conversations fuer (company, value_key) oder erstellt neu
User schreibt Nachricht -> POST /api/llm/conversations/{id}/messages
Backend streamt Claude-Response (SSE), persistiert messages
User klickt "Wert uebernehmen" -> finaler Vorschlag aus dem Chat
  wird in company_values geschrieben, source_name = "Manuell via Claude-Chat"
Optional: "Gemini reviewen lassen" -> GEMINI_REVIEW-Job,
  Review wird als System-Message im Chat angehaengt
```

### 5. Period-Selector

```
User waehlt "FY2024" oder Range "2022-2026"
Frontend: re-rendert Tabelle aus bereits geladenen company_values
          (kein API-Call fuer die Wert-Anzeige)
Frontend triggert separat: POST /api/companies/{id}/calculate?years=2022,...,2026
-> Server-seitige Berechnung: Hohn-Rendite pro Jahr als Reihe
-> Ergebnis wird in der Bottom-Zeile/Footer der Tabelle angezeigt
```

## Komponenten

### Backend

```
backend/
  app/
    main.py              # App-Factory, Static-Mount fuer Frontend
    config.py            # pydantic-settings
    db.py                # SQLAlchemy Engine, Session
    auth/
      routes.py          # POST /login, /logout, /me
      jwt.py             # Cookie-JWT
      deps.py            # current_user dependency
    portfolios/
      routes.py, models.py, schemas.py
    companies/
      routes.py, models.py, schemas.py
    values/
      routes.py          # refresh, override, get-with-source
      catalog.py         # value_definitions seed
      models.py
    providers/
      base.py            # ValueProvider Protocol
      registry.py        # Priority-Mapping pro value_key
      yahoo.py
      alpha_vantage.py
    calculations/
      hohn_rendite.py    # Formeln basic 1, basic 2, adjusted
      routes.py          # POST /calculate
    llm/
      routes.py          # Conversations, Messages, SSE-Streaming
      claude.py          # Anthropic SDK + Prompt-Caching
      gemini.py          # Google Gemini SDK
      workflows.py       # auto_research, gemini_review
      models.py
    jobs/
      routes.py, runner.py, models.py
    alembic/             # Migrations
  tests/
  pyproject.toml
  Dockerfile
  alembic.ini
```

### Frontend

```
frontend/
  src/
    main.tsx
    App.tsx                       # Router
    api/
      client.ts                   # fetch-Wrapper, JWT-Cookie
      portfolios.ts, companies.ts, values.ts
      llm.ts                      # inkl. SSE-Streaming
      jobs.ts                     # Polling-Hook
    pages/
      LoginPage.tsx
      PortfolioListPage.tsx
      PortfolioDetailPage.tsx     # Firmen-Liste
      CompanyDashboardPage.tsx    # Excel-artige Tabelle
    components/
      ValueCell.tsx               # Zelle + Source-Popover
      SourcePopover.tsx
      PeriodSelector.tsx          # Single-Year / Range Toggle
      LlmChatDrawer.tsx
      RefreshButton.tsx
      JobProgressBadge.tsx
      ui/                         # Shadcn-Komponenten
    hooks/
      useAuth.ts
      useJobPolling.ts
      useLlmStream.ts
    lib/
      i18n.ts                     # DE-UI-Strings + EN-Finanzbegriffe
      format.ts                   # Zahlen, Waehrungen
  index.html
  package.json
  vite.config.ts
  tailwind.config.ts
```

Build: Vite baut `dist/` -> kopiert in den Backend-Container -> FastAPI mountet unter `/`.

## Provider-Pattern

```python
class ValueProvider(Protocol):
    name: str
    supported_keys: set[str]

    async def fetch(
        self, ticker: str, key: str, period_year: int | None
    ) -> ProviderResult | None:
        """Return None if this provider cannot supply this value."""
```

- `registry.py` haelt Config: `{value_key: [provider_name, provider_name, ...]}`
- Refresh-Endpoint iteriert die Liste, erster non-None gewinnt
- Neue Provider = neue Datei + Registry-Config-Eintrag (keine Aenderung woanders)
- Tests pro Provider mit `respx`-gemockten HTTP-Responses

## Calculation Engine

Reine Funktionen in `calculations/hohn_rendite.py`, alles `Decimal`.

```python
def hohn_rendite_basic_1(values: dict[str, Decimal], year: int) -> Decimal: ...
def hohn_rendite_basic_2(values: dict[str, Decimal], year: int) -> Decimal: ...
def adjusted_hohn_rendite(
    basic: Decimal, valuation_factor: Decimal,
    risk_factor: Decimal, mgmt_factor: Decimal
) -> Decimal: ...
```

- Input: dict aus `company_values`, gefiltert nach period_year
- Output: pro Jahr ein Ergebnis-Objekt -> JSON ans Frontend
- **Formeln muessen mit Dr. Ludes verifiziert werden.** Bis dahin Platzhalter-Formeln mit expliziten TODOs im Code.

## LLM-Layer

### Auto-Research (beim Anlegen einer Firma)

```
auto_research(company, value_key):
  1. Claude: System-Prompt "Du bewertest <Business Model> fuer <Firma>.
             Liefere Score 1-5 + Begruendung + Quellen-URLs als JSON."
  2. Speichere Claude-Output als Initial-Message in conversation
  3. Trigger gemini_review(conversation_id)

gemini_review(conversation_id):
  1. Gemini bekommt Claude-Output + Original-Prompt
  2. Gibt Review strukturiert zurueck (JSON):
     { "verdict": "agree" | "disagree" | "refine",
       "score": int, "notes": string, "refined_value": ... }
  3. Haengt Review als message (role=gemini) an
  4. Konsens-Regel (Phase 1, simpel):
     - verdict == "agree" UND abs(score_claude - score_gemini) <= 1
       -> schreibt finalen Wert in company_values (source_name = "Claude+Gemini")
     - sonst -> Wert wird NICHT geschrieben, User muss im Chat entscheiden
       (Frontend zeigt Hinweis "Gemini weicht ab - bitte pruefen")
```

### On-Demand Chat

- User oeffnet Chat pro qualitativem Wert
- Streaming via SSE
- Am Ende "Wert uebernehmen" und optional "Gemini reviewen"

### Claude-API-Details

- Prompt-Caching fuer System-Prompts (spart Tokens bei jedem Chat-Turn)
- Model `claude-opus-4-6` fuer Research
- Model `claude-sonnet-4-6` fuer Chat
- Streaming via Anthropic SDK
- Retry mit Exponential Backoff bei Rate-Limits

## Error Handling

- **Provider-Fehler** (Timeout, 429, 500): Job-Status `failed` mit `error`-Text, Frontend zeigt rotes Icon + Retry-Button
- **LLM-Fehler:** Conversation bleibt offen, System-Message mit Fehler, User kann erneut senden
- **Validation-Fehler:** FastAPI gibt 422 mit Pydantic-Detail zurueck, Frontend zeigt Inline-Hinweis
- **Rate-Limits:** Retry mit Exponential Backoff, max 3 Versuche

## Testing

### Backend (pytest)
- Unit-Tests pro Provider (HTTP via `respx` gemockt)
- Unit-Tests Calculation Engine (deterministische Decimal-Tests)
- Integration-Tests fuer Endpoints (Test-DB via `pytest-postgresql` oder Docker)
- LLM-Calls in Tests via Fake-Implementations gemockt (kein echter API-Call)

### Frontend (Vitest + React Testing Library)
- Component-Tests fuer ValueCell, PeriodSelector, LlmChatDrawer
- API-Mocks via MSW

### E2E (Phase 2, optional)
- Playwright: Login -> Portfolio anlegen -> Firma -> Werte sehen

## Secrets & Config

ENV-basiert via pydantic-settings:

```
DATABASE_URL=postgresql+psycopg://...rds.amazonaws.com/...
JWT_SECRET=...
ANTHROPIC_API_KEY=...
GOOGLE_API_KEY=...
ALPHA_VANTAGE_API_KEY=...
YAHOO_FINANCE_KEY=...
ALLOWED_ORIGINS=https://app...
```

- Lightsail: `.env`-File auf Server, referenziert in `docker-compose.yml`
- Lokal: `.env.local`, in `.gitignore`

## Internationalisierung

- `lib/i18n.ts` mit zwei Maps:
  - `de`: alle UI-Strings ("Portfolio anlegen", "Firma hinzufuegen", "Berechnen")
  - `terms`: Finanzbegriffe immer englisch ("Market Cap", "EBITDA", "FCF Yield")
- Keine i18n-Library - typed Konstanten reichen
- `value_definitions` haelt `label_de` und `label_en`; Tabelle zeigt `label_en` als Spaltenheader (Excel-Look), `label_de` als Tooltip
- "Hohn-Rendite" bleibt deutsch (Eigenname)

## CI/CD (Phase 2)

- GitHub Actions: Lint + Tests bei PR
- Bei Merge auf `main`: Docker-Image bauen + auf Lightsail deployen via SSH

## Offene Punkte (fuer spaeter mit Dr. Ludes)

1. **Konkrete Datenquellen/APIs** - Bloomberg zu teuer. ~10 Werte, nicht jede API deckt alles ab. Muss mit Dr. Ludes abgestimmt werden.
2. **Exakte Formeln** der Hohn-Rendite (basic 1, basic 2, Adjustment-Factors) - Platzhalter bis Verifikation.
3. **Positions-Modell:** 1:1 mit Firma fuer Phase 1. Evtl. spaeter mehrere Szenarien.
