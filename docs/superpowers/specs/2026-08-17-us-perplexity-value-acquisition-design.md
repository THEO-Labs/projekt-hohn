# Neuansatz Werte-Beschaffung (US) via EDGAR-Anker + Perplexity

**Datum:** 2026-08-17
**Kunde:** Turning Point Investments (Dr. Ernst Ludes)
**Status:** Design Draft
**Ersetzt:** die gewachsene Werte-Beschaffung ("Schicht B") unter `backend/app/values/` + Nicht-US-Pfad

## Ziel

Die historisch gewachsene Werte-Beschaffung (~15k Zeilen: LLM-Recherche, Schaetz-
modell, Plausibilitaets-Gates, Two-Stage/Ratchet/Verifier) wird komplett ersetzt.
Neue, radikal schlanke Pipeline fuer **US-Firmen**:

1. **EDGAR-XBRL-Anker zuerst** — alle dort verfuegbaren Zahlen 1:1 uebernehmen (exakt, Quelle SEC).
2. **Perplexity (Agent-API) fuer alles andere** — sowohl bereits berichtete Werte, die EDGAR nicht hatte, als auch Konsens-Schaetzungen fuers Forwardjahr. Jeder Wert traegt eine Quelle (Zitat-URL).
3. **Keine Gates.** Vertrauen liegt auf Quelle (EDGAR exakt, Perplexity zitiert, Domain-gefiltert auf offizielle Quellen). Kontrolle beim User ueber die Quelle + manuellen Override.

Weg von Claude in der Beschaffung; Perplexity ist die einzige LLM-Quelle. Die
H-Rendite-Formeln (`calculations/engine.py`) und die gesamte Infrastruktur bleiben
unveraendert.

**UI-Sprache:** Deutsch, Finanzbegriffe englisch (unveraendert).

## Scope

### In Scope
- **US-Firmen** (ISIN-gated; Firmenanlage ist bereits ISIN/US-only).
- EDGAR-XBRL-Anker als exakte Primaerquelle (bestehender Provider, ggf. leicht getrimmt).
- Perplexity-Agent-API fuer: reported-Luecken, Konsens-Forecasts, Adjusted-Werte.
- Adjusted/Non-GAAP-Werte (adjusted NI/EBITDA/FCF), wo die Firma sie berichtet — von Perplexity mitgeliefert, mit Quelle; `engine.py` rechnet die Adjusted-Multiples wie bisher.
- Manueller Override pro Zelle (bestehend) hat hoechste Prioritaet.
- Markt­daten-Feed (Yahoo) **nur** fuer Stammdaten: `stock_price`, `market_cap`, `shares_outstanding`.

### Out of Scope (spaeter)
- **Nicht-US-Firmen (DAX/EU/IFRS).** Der gesamte Nicht-US-Pfad und ESEF fallen jetzt weg; kommt in einem spaeteren Block zurueck.
- Quartals-Vollstaendigkeit fuer Nicht-US (entfaellt mangels Nicht-US).
- Plausibilitaets-Gates, Two-Stage-Recherche, eigenes Schaetzmodell, Consistency-Verifier — ersatzlos gestrichen.

## Behalten / Wiederverwenden / Ersetzen

### Bleibt unveraendert (out of scope)
- `calculations/engine.py` — H-Rendite-Formeln (Kern-Rechnen bleibt).
- Gesamtes Frontend (Dashboard, Zellen, Source-Popover, Refresh, Override, Adjusted-Toggle).
- `auth/`, `portfolios/`, `companies/`, `ir_documents/` (PDF-Upload als Override), `fx/`.
- DB-Schema: `company_values`, `value_definitions`-Katalog, bestehende Migrationen.

### Wird wiederverwendet (Infrastruktur, kein Rechnen)
- `providers/edgar.py` — der XBRL-Anker.
- `providers/yahoo.py` — **nur** fuer Stammdaten (`stock_price`, `market_cap`, `shares_outstanding`) als Markt­daten-Feed.
- `values/always_current.py` (`ALWAYS_CURRENT_KEYS = {stock_price, market_cap, shares_outstanding}`) — isoliert die Stammdaten-Keys; trennt sie vom Perplexity-Schema und vom Anker-/Perplexity-Pfad.
- `values/persistence.py` — Schreibpfad-Invarianten (`normalize_sign`, `currency_conflict`, `stamp_attempt_and_fill_not_found`), `models.py`, `period_keys.py`, `sign_keys.py`, `currency_keys.py`.
- `provider_anchor.running_fy_year` — geschaeftsjahr-bewusstes laufendes FY (Rest von `provider_anchor.py` entfaellt).
- `llm/` **generische** Helfer (`rate_limiter.py`, `cost_tracker.py`, `json_utils.py`) — der Perplexity-Client baut darauf auf. Das Anthropic-spezifische Geruest in `llm/claude.py` (`get_client`, `WEB_SEARCH_TOOL`) ist **nicht** wiederverwendbar; `PerplexityClient` ist ein neuer, duenner Adapter (siehe §2, Vertrag zur Plan-Zeit verifizieren).
- `values/progress.py`, `scripts/fill_gaps.py` (not_found-Platzhalter + Completeness-Report).
- `values/batch.py` — Portfolio-weiter Recompute; ruft statt des Alt-Pfads den neuen `ValueOrchestrator`.

### Wird geloescht / ersetzt (die "Berechnungslogik" = Schicht B)
- `statement_research.py`, `guidance_estimates.py`, `consistency.py`, `gaap_bridge.py`, `adjusted_enrichment.py`.
- `providers/esef.py` und dessen Verdrahtung (Nicht-US; deaktiviert, Code darf liegenbleiben).
- Der grosse Orchestrierungs-Kern in `values/routes.py` (`_process_one_key`, Two-Stage-, Backfill-, N-2-Anker-, Ratchet-, Gate-Aufrufe) → ersetzt durch den schlanken `ValueOrchestrator`.
- `provider_anchor.py` (ausser `running_fy_year`), `dedupe.py` nach Bedarf. (`always_current.py` bleibt — siehe Wiederverwenden.)

## Architektur / Komponenten

Jede Einheit hat eine klar umrissene Aufgabe und ein definiertes Interface.

### 1. `EdgarAnchor` (behalten: `providers/edgar.py`)
Liefert exakte XBRL-Werte je `(key, jahr, periode)` mit Quelle `SEC EDGAR` + Source-Link.

```
fetch(company, years) -> dict[(key, year, period_type), AnchorValue]
AnchorValue = (numeric_value, source_name="SEC EDGAR", source_link, currency)
```

US-EDGAR liefert Quartale + FY XBRL-exakt. Adjusted-Werte liefert EDGAR nicht
(Non-GAAP steht nicht im XBRL) — die kommen aus Perplexity.

### 2. `PerplexityClient` (neu: `llm/perplexity.py`)
Kapselt die Perplexity-Agent-API. **Neuer, duenner Adapter** (kein Reuse des Anthropic-Clients).

> **Plan-Zeit-Verifizierung (load-bearing):** Der genaue Vertrag der Agent-API ist vor der Implementierung an der offiziellen Doku zu bestaetigen — Endpoint, Request-Schema (die aeltere Sonar-Chat-Completions-API war OpenAI-chat-foermig; die neue Agent-API laut Doku Anthropic-Messages-foermig), `response_format`-JSON-Schema-Support und das Zitat-/Streaming-Format. Weicht das ab, aendert sich nur die Innerei von `PerplexityClient`, nicht die uebrige Architektur.

- **Endpoint:** `POST /v1/agent` (Doku-Stand: Anthropic-Messages-Schema, `stream: true`).
- **Modell:** `sonar-pro` (strukturierte Ausgabe + reichere Zitate).
- **Strukturierte Ausgabe:** `response_format` mit JSON-Schema (vom `SchemaBuilder`).
- **Domain-Filter:** Web-Search-Allowlist auf offizielle Quellen (sec.gov, IR-Domain der Firma, etablierte Finanzberichts-Quellen).
- **Zitate:** aus der Agent-API-Response extrahiert (Streaming-Citation-Parsing), als `source_link` pro Wert persistiert.
- **Auth:** `PERPLEXITY_API_KEY` via ENV (pydantic-settings).
- **Throttling/Retry:** bestehender `rate_limiter` + Exponential-Backoff.

Methoden:
```
fetch_period(company, fiscal_year, missing_keys)
    -> dict[key, PerplexityValue]        # berichtete Werte + adjusted, mit Zitat
fetch_consensus(company, forward_year, keys)
    -> dict[key, PerplexityValue]        # Analysten-Konsens/Guidance, mit Zitat
PerplexityValue = (numeric_value, numeric_value_adjusted|None, source_url, source_title, currency)
```

### 3. `SchemaBuilder` (neu)
Baut das JSON-Schema fuer die Perplexity-Abfrage aus dem Wert-Katalog
(`value_definitions`, source_type=API) — **abzueglich der Stammdaten-Keys**
(`ALWAYS_CURRENT_KEYS`: `stock_price`, `market_cap`, `shares_outstanding`),
die aus dem Markt­daten-Feed kommen und nie an Perplexity gehen. Perplexity
sieht also nur die berichteten Fundamental-Keys. Pro Key eine **Metrik-Definition als
Feldbeschreibung** — das "Pinning", das frueher in verstreuten Prompts lag
(z.B. `operating_cash_flow` = "Cashflow aus laufender Geschaeftstaetigkeit,
Konzern, aus der Kapitalflussrechnung"). Einzige Quelle der Wahrheit fuer:
- Metrik-Definitionen (Feldbeschreibungen),
- Einheiten-Konvention (Mio, Waehrung),
- optionales `*_adjusted`-Feld pro Kennzahl (NI/EBITDA/FCF),
- Domain-Allowlist.

### 4. `ValueOrchestrator` (neu, ersetzt `refresh_company_values`-Kern)
Pro Firma:
1. Zieljahre bestimmen: Historien-Fenster + laufendes FY via `running_fy_year`.
2. **Stammdaten** (`ALWAYS_CURRENT_KEYS`) aus `providers/yahoo.py` (Markt­daten-Feed) → `stock_price`, `market_cap`, `shares_outstanding` mit Quelle `Market Data Feed`. Diese Keys sind vom Anker- und Perplexity-Pfad ausgenommen.
3. `EdgarAnchor.fetch` → exakte Fundamental-Zellen fuellen (Quelle SEC).
4. Je Historienjahr: noch leere Fundamental-Keys (ohne Stammdaten) sammeln → **1** `PerplexityClient.fetch_period`-Call pro (Firma, FY) → Rest mit Zitat fuellen (inkl. Adjusted, wo vorhanden).
5. Forwardjahr(e): **1** `fetch_consensus`-Call pro Firma → Forecast-Zellen (`is_forecast=true`).
6. Prioritaet pro Zelle strikt einhalten: `Manual Override` > `SEC EDGAR` > `Perplexity` > leer/not_found; Stammdaten kommen ausschliesslich aus dem Markt­daten-Feed. `manually_overridden`-Zellen werden nie beschrieben; EDGAR schlaegt Perplexity.
7. Persistieren ueber `persistence.py`-Invarianten (`source_name`, `source_link`, Sign/Currency).
8. **Keine Gates, kein Consistency-Pass.**
9. `engine.calculate_fy` / `calculate_stammdaten` unveraendert → CALCULATED-Zellen ableiten.

## Datenfluss

```
Refresh(Firma) [US]
  → Yahoo (Markt­daten-Feed)     Stammdaten: stock_price/market_cap/shares
  → EdgarAnchor.fetch          exakte Fundamental-Zellen + Quelle SEC (1:1)
  → je FY: fetch_period         reported-Luecken + Adjusted + Zitat
  → Forwardjahr: fetch_consensus  Konsens-Forecast + Zitat
  → persist (Prioritaet Manual > EDGAR > Perplexity; Stammdaten = Feed; keine Gates)
  → engine.py                   CALCULATED (H-Rendite, Multiples) ableiten
  → progress / fill_gaps        not_found-Platzhalter + Completeness-Report
```

**Source-Vokabular (`source_name`):** `SEC EDGAR`, `Perplexity`, `Manual Override`,
`Market Data Feed` (Stammdaten), `No source found (research attempted)` (Platzhalter).

## Fehlerbehandlung

- **EDGAR fehlt/fehlerhaft** fuer einen Key → Zelle bleibt fuer Perplexity offen (kein Fehler).
- **Perplexity liefert `null`/nichts** fuer einen Key → Zelle bleibt leer → not_found-Platzhalter (`stamp_attempt_and_fill_not_found`), UI markiert rot.
- **Perplexity-API-Fehler** (429/Timeout) → Backoff via `rate_limiter`; bei dauerhaftem Ausfall loggen, Zellen offen lassen, Firma laeuft weiter, Batch meldet partiell. Nie Crash.
- **Manual Override** wird nie ueberschrieben.
- **Lange Calls:** Agent-API **gestreamt** aufrufen (`stream: true`, `get_final_message`-Aequivalent) — verhindert den frueheren >10-min-Hard-Error der Nicht-Streaming-Aufrufe.

## Testing

- **`PerplexityClient`** (respx-gemockt): Agent-API-Antworten inkl. Zitaten + strukturiertem JSON → Schema-Parsing, `null`-Handling, `source_name`/`source_link`-Zuordnung, Adjusted-Felder, Domain-Filter im Request.
- **`SchemaBuilder`**: Schema enthaelt alle Fundamental-API-Keys (ohne `ALWAYS_CURRENT_KEYS`) mit Definition + Adjusted-Feldern; Stammdaten-Keys sind **nicht** enthalten; Domain-Allowlist gesetzt.
- **`ValueOrchestrator`**: Prioritaet (Manual > EDGAR > Perplexity), Luecken-Erkennung (nur fehlende Keys werden abgefragt), Forecast/Konsens-Pfad, Adjusted-Befuellung, **Nachweis: keine Gate-Logik greift**, `running_fy_year`-Nutzung.
- **EDGAR-Anker**: bestehende Tests behalten.
- **`engine.py`**: Tests unveraendert.
- Entfernte Module: zugehoerige Alt-Tests loeschen (consistency, statement_research, guidance, gaap_bridge, adjusted_enrichment, Nicht-US/ESEF).

## Secrets & Config

Neu in ENV (pydantic-settings):
```
PERPLEXITY_API_KEY=...
```
`ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` werden in der Beschaffung nicht mehr genutzt
(koennen nach dem Umbau entfernt werden, sofern nirgends sonst referenziert).

## Migrations-/Umbau-Hinweise

- Keine DB-Schema-Aenderung noetig (bestehende `company_values`-Felder decken alles ab, inkl. `numeric_value_adjusted`, `source_name`, `source_link`, `manually_overridden`, `is_forecast`, `primary_method`).
- `primary_method`-Werte vereinfachen sich auf: `provider` (EDGAR), `perplexity`, `perplexity_consensus`, `manual`, `not_found`.
- Alte Werte in der Prod-DB bleiben; ein Full-Recompute pro Portfolio schreibt sie mit neuen Quellen.

## Offene Punkte (spaeter mit Dr. Ludes / im Plan)

1. Historien-Fenster (Anzahl zurueckliegender FYs) — Default uebernehmen aus bestehender Logik, im Plan fixieren.
2. Konkrete Domain-Allowlist fuer Perplexity (sec.gov + IR-Domains + welche Finanzquellen).
3. Perplexity-Kostenbudget pro Full-Recompute (per-request Search-Fee + Tokens) — beobachten.
4. Nicht-US-Wiederaufnahme als eigener spaeterer Block (eigene Spec).
