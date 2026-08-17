# US EDGAR+Perplexity Value Acquisition — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ersetze die gewachsene Werte-Beschaffung ("Schicht B") durch eine schlanke US-Pipeline: EDGAR-XBRL-Anker 1:1, dann Perplexity (Agent-API) fuer reported-Luecken + Adjusted + Konsens-Forecasts, jeweils mit Quelle, ohne Gates.

**Architecture:** Ein `ValueOrchestrator` fuehrt pro Firma: Yahoo-Stammdaten → EDGAR-Anker → je FY ein Perplexity-`fetch_period`-Call fuer fehlende Fundamental-Keys → ein `fetch_consensus`-Call fuers Forwardjahr → Persistenz mit strikter Prioritaet (Manual > EDGAR > Perplexity) ueber die bestehenden `persistence.py`-Invarianten → `engine.py` leitet Calculated ab. Keine Gates, kein Consistency-Pass. Nur US (ISIN-gated). Nicht-US/ESEF entfaellt (spaeterer Block).

**Tech Stack:** FastAPI, SQLAlchemy 2.x, `httpx` (async→sync wrapper wie bestehend), Perplexity Agent-API (`POST /v1/agent`, `response_format` json_schema, `web_search`-Tool mit `search_domain_filter`), pytest + `respx` fuer HTTP-Mocks.

**Verifizierter Perplexity-Vertrag (Doku-Stand 2026-08):**
- Endpoint: `POST https://api.perplexity.ai/v1/agent`, Header `Authorization: Bearer <key>`.
- Body: `model` (String-ID, via ENV), `input` (Prompt-String), `response_format = {"type":"json_schema","json_schema":{"name":..,"schema":..}}`, `tools = [{"type":"web_search","search_context_size":"high","filters":{"search_domain_filter":[..]}}]`, `stream: false`.
- Response: `output_text` (JSON-String, dem Schema entsprechend) + `citations` + `search_results`. **URLs NICHT ins JSON aufnehmen** — Quelle pro Call aus `citations`/`search_results` ziehen und an alle in dem Call gefuellten Zellen haengen.

---

## Ziel-Konstanten (im Plan gepinnt, spaeter konfigurierbar)

- `HISTORY_YEARS = 5` — Zielfenster = `running_fy` + die 4 vorangegangenen FYs. Reported fuer abgeschlossene FYs, Konsens fuer das laufende/kommende FY.
- `DEFAULT_PERPLEXITY_MODEL = "sonar-pro"` — ENV-ueberschreibbar (`PERPLEXITY_MODEL`); exakte Agent-API-Modell-ID beim ersten echten Call verifizieren.
- `PERIOD_DOMAIN_ALLOWLIST = ["sec.gov"]` — reported-Abfragen (offizielle Filings/8-K-Exhibits). IR-Domain der Firma optional additiv.
- Konsens-Abfragen: **kein** `search_domain_filter` (Konsens liegt nicht auf sec.gov) — breit suchen, Citations bleiben Pflicht.

---

## File Structure

**Neu:**
- `backend/app/values/metric_definitions.py` — `METRIC_DEFINITIONS` (Key → praezise Definition fuer das JSON-Schema-Feld), `ADJUSTED_KEYS`, Domain-Allowlists. Einzige Quelle der Wahrheit fuers "Pinning".
- `backend/app/values/schema_builder.py` — baut das json_schema aus dem Katalog minus `ALWAYS_CURRENT_KEYS`; fuegt `*_adjusted`-Felder fuer `ADJUSTED_KEYS` hinzu.
- `backend/app/llm/perplexity.py` — `PerplexityClient` (`fetch_period`, `fetch_consensus`), duenner httpx-POST.
- `backend/app/values/orchestrator.py` — `ValueOrchestrator` (der neue Refresh-Kern).

**Geaendert:**
- `backend/app/config.py` — `perplexity_api_key`, `perplexity_model`, `perplexity_base_url`.
- `backend/app/values/routes.py` — `refresh_company_values` delegiert an `ValueOrchestrator`; Alt-Orchestrierung (`_process_one_key`, Two-Stage/Backfill/N-2/Gate-Aufrufe) entfernt.
- `backend/app/values/batch.py` — `_recompute_one` ruft `ValueOrchestrator` (bzw. das delegierende `refresh_company_values`).
- `backend/app/values/provider_anchor.py` — auf `running_fy_year` (+ direkt genutzte Helfer) reduziert.
- `.env.example` — `PERPLEXITY_API_KEY=`.

**Geloescht (inkl. Tests):**
- `backend/app/values/statement_research.py`, `guidance_estimates.py`, `consistency.py`, `gaap_bridge.py`, `adjusted_enrichment.py`
- `backend/app/providers/esef.py` (+ Registry-Verdrahtung)
- zugehoerige Tests: `test_statement_research*.py`, `test_guidance_*.py`, `test_consistency*.py`, `test_gaap_bridge*.py`, `test_adjusted_enrichment.py`, `test_esef_provider.py`, `test_open_quarter_adjusted*.py`, `test_derive_*`, `test_prev_year_backfill.py`, `test_n2_fy_backfill.py`, `test_clear_stale_forecasts.py`, `test_explain_open_gaps.py` (nur die, die geloeschte Module importieren — pro Datei beim Loeschen verifizieren).

---

## Task 1: Config — Perplexity-Settings

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/test_config_perplexity.py` (Create)

- [ ] **Step 1: Failing test**

```python
# backend/tests/test_config_perplexity.py
from app.config import Settings

def test_perplexity_settings_present(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pk-test")
    s = Settings(_env_file=None)
    assert s.perplexity_api_key == "pk-test"
    assert s.perplexity_model  # non-empty default
    assert s.perplexity_base_url.startswith("https://")
```

- [ ] **Step 2: Run — expect FAIL** `cd backend && uv run pytest tests/test_config_perplexity.py -v`
- [ ] **Step 3: Implement** — in `config.py` `Settings` hinzufuegen:

```python
    perplexity_api_key: str | None = None
    perplexity_model: str = "sonar-pro"
    perplexity_base_url: str = "https://api.perplexity.ai"
```

- [ ] **Step 4: Run — expect PASS**
- [ ] **Step 5:** `.env.example` um `PERPLEXITY_API_KEY=` ergaenzen.
- [ ] **Step 6: Commit** `git add -A && git commit -m "feat(config): perplexity api settings"`

---

## Task 2: Metric-Definitionen + Domain-Allowlists

**Files:**
- Create: `backend/app/values/metric_definitions.py`
- Test: `backend/tests/test_metric_definitions.py`

**Kontext:** Fundamental-Keys = alle `value_definitions` mit `source_type=API` MINUS `ALWAYS_CURRENT_KEYS` (aus `app/values/always_current.py`). Die Definition pro Key ist der "Pinning"-Text, der frueher in verstreuten Prompts lag.

- [ ] **Step 1: Failing test**

```python
# backend/tests/test_metric_definitions.py
from app.values.metric_definitions import METRIC_DEFINITIONS, ADJUSTED_KEYS, PERIOD_DOMAIN_ALLOWLIST
from app.values.always_current import ALWAYS_CURRENT_KEYS

def test_definitions_cover_key_fundamentals():
    for k in ("operating_cash_flow", "capex", "net_income", "revenue",
              "ebitda", "net_debt", "sbc", "buyback_volume", "dividends",
              "eps_diluted", "fcf"):
        assert k in METRIC_DEFINITIONS and METRIC_DEFINITIONS[k].strip()

def test_no_stammdaten_in_definitions():
    assert not (set(METRIC_DEFINITIONS) & ALWAYS_CURRENT_KEYS)

def test_adjusted_keys_subset():
    assert ADJUSTED_KEYS <= set(METRIC_DEFINITIONS)
    assert {"net_income", "ebitda", "fcf"} <= ADJUSTED_KEYS

def test_period_allowlist_is_official():
    assert "sec.gov" in PERIOD_DOMAIN_ALLOWLIST
```

- [ ] **Step 2: Run — expect FAIL**
- [ ] **Step 3: Implement** `metric_definitions.py`:

```python
"""Einzige Quelle der Wahrheit fuer Metrik-Definitionen ("Pinning"),
Adjusted-Keys und Domain-Allowlists der Perplexity-Abfragen.
Definitionen landen als Feldbeschreibung im JSON-Schema.
"""

# Praezise, so wie die Kennzahl im offiziellen US-GAAP-Bericht steht.
METRIC_DEFINITIONS: dict[str, str] = {
    "revenue": "Total revenue / net sales for the fiscal year, consolidated (GAAP income statement top line), in millions.",
    "net_income": "Net income attributable to the company's shareholders (GAAP, after non-controlling interests), in millions.",
    "eps_diluted": "Diluted earnings per share (GAAP), in currency units (not millions).",
    "operating_cash_flow": "Net cash provided by operating activities (consolidated cash flow statement), in millions.",
    "capex": "Capital expenditures = purchases of property, plant and equipment (and capitalized software), absolute positive amount, in millions.",
    "fcf": "Free cash flow = operating cash flow minus capital expenditures, in millions.",
    "ebitda": "EBITDA = operating income plus depreciation, depletion and amortization (full D&A incl. amortization of intangibles), in millions. NEVER a non-GAAP operating-income figure.",
    "sbc": "Stock-based compensation expense for the fiscal year (cash flow statement add-back), in millions.",
    "buyback_volume": "Total cash used for repurchases of common stock during the fiscal year (financing activities), absolute amount, in millions.",
    "dividends": "Total cash dividends paid to common shareholders during the fiscal year, absolute amount, in millions.",
    "net_debt": "Net debt = total debt (short-term + long-term borrowings, excluding operating lease liabilities) minus cash & equivalents and short-term investments. Negative = net cash, in millions.",
    "cash_and_equivalents": "Cash and cash equivalents at fiscal year end (balance sheet), in millions.",
    "st_investments": "Short-term / marketable investments at fiscal year end (balance sheet), in millions.",
    "st_debt": "Short-term borrowings + current portion of long-term debt at fiscal year end (excluding operating leases), in millions.",
    "lt_debt": "Long-term debt at fiscal year end (excluding operating lease liabilities), in millions.",
}

# Kennzahlen, fuer die die Firma zusaetzlich einen Non-GAAP/adjusted-Wert
# berichtet. Perplexity liefert dann auch das `<key>_adjusted`-Feld.
ADJUSTED_KEYS: set[str] = {"net_income", "ebitda", "fcf", "revenue", "operating_cash_flow"}

# Reported-Abfragen: nur offizielle Filings (8-K-Exhibits/10-K liegen auf sec.gov).
PERIOD_DOMAIN_ALLOWLIST: list[str] = ["sec.gov"]
```

- [ ] **Step 4: Run — expect PASS**
- [ ] **Step 5: Commit** `git commit -am "feat(values): metric definitions + domain allowlists"`

---

## Task 3: SchemaBuilder

**Files:**
- Create: `backend/app/values/schema_builder.py`
- Test: `backend/tests/test_schema_builder.py`

- [ ] **Step 1: Failing test**

```python
# backend/tests/test_schema_builder.py
from app.values.schema_builder import build_period_schema, fundamental_keys
from app.values.always_current import ALWAYS_CURRENT_KEYS

def test_fundamental_keys_exclude_stammdaten():
    keys = fundamental_keys()
    assert not (set(keys) & ALWAYS_CURRENT_KEYS)
    assert "operating_cash_flow" in keys

def test_schema_has_value_and_adjusted_fields():
    schema = build_period_schema()["json_schema"]["schema"]
    props = schema["properties"]
    # Container: metrics -> object keyed by metric
    assert "net_income" in props
    assert props["net_income"]["description"]  # pinning text present
    # adjusted twin exists for ADJUSTED_KEYS
    assert "net_income_adjusted" in props
    # stammdaten never present
    assert "stock_price" not in props

def test_schema_no_url_fields():
    schema = build_period_schema()["json_schema"]["schema"]
    for name in schema["properties"]:
        assert "url" not in name and "source" not in name
```

- [ ] **Step 2: Run — expect FAIL**
- [ ] **Step 3: Implement** `schema_builder.py`:

```python
"""Baut das response_format json_schema fuer Perplexity-Abfragen aus den
Fundamental-Keys (Katalog-API-Keys minus Stammdaten). Jede Kennzahl ist ein
nullable number; Definitionen kommen aus metric_definitions. Keine URL-Felder
(Quellen kommen aus citations).
"""

from app.values.always_current import ALWAYS_CURRENT_KEYS
from app.values.metric_definitions import ADJUSTED_KEYS, METRIC_DEFINITIONS


def fundamental_keys() -> list[str]:
    return [k for k in METRIC_DEFINITIONS if k not in ALWAYS_CURRENT_KEYS]


def _num_prop(desc: str) -> dict:
    return {"type": ["number", "null"], "description": desc}


def _properties() -> dict:
    props: dict[str, dict] = {}
    for k in fundamental_keys():
        props[k] = _num_prop(METRIC_DEFINITIONS[k])
        if k in ADJUSTED_KEYS:
            props[f"{k}_adjusted"] = _num_prop(
                f"Company-reported adjusted / non-GAAP variant of: {METRIC_DEFINITIONS[k]} "
                "Only if the company explicitly reports an adjusted figure; else null."
            )
    return props


def build_period_schema(name: str = "fundamentals") -> dict:
    props = _properties()
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "schema": {
                "type": "object",
                "properties": props,
                "required": [],
                "additionalProperties": False,
            },
        },
    }


def build_consensus_schema(keys: list[str]) -> dict:
    props = {k: _num_prop(METRIC_DEFINITIONS[k]) for k in keys if k in METRIC_DEFINITIONS}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "consensus",
            "schema": {"type": "object", "properties": props,
                       "required": [], "additionalProperties": False},
        },
    }
```

- [ ] **Step 4: Run — expect PASS**
- [ ] **Step 5: Commit** `git commit -am "feat(values): perplexity json schema builder"`

---

## Task 4: PerplexityClient

**Files:**
- Create: `backend/app/llm/perplexity.py`
- Test: `backend/tests/test_perplexity_client.py`

**Kontext:** Reuse `app/llm/rate_limiter.py` (429-Backoff) und `app/llm/json_utils.py` (tolerantes JSON-Parsen) wenn vorhanden; sonst `json.loads`. httpx synchron (wie andere Provider). Citations werden an ALLE Werte des Calls gehaengt (ein Call = eine Firma+Periode).

- [ ] **Step 1: Failing test** (respx-gemockt)

```python
# backend/tests/test_perplexity_client.py
import respx, httpx, json
from app.llm.perplexity import PerplexityClient, PerplexityValue

def _resp(payload: dict, citations):
    return {"output_text": json.dumps(payload), "citations": citations, "search_results": []}

@respx.mock
def test_fetch_period_parses_values_and_citation():
    route = respx.post("https://api.perplexity.ai/v1/agent").mock(return_value=httpx.Response(
        200, json=_resp({"net_income": 1234.0, "net_income_adjusted": 1300.0, "capex": None},
                        ["https://www.sec.gov/Archives/edgar/x.htm"])))
    c = PerplexityClient(api_key="pk", model="sonar-pro")
    out = c.fetch_period(company_name="Acme Inc", ticker="ACME", fiscal_year=2024,
                         missing_keys=["net_income", "capex"], currency="USD")
    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "sonar-pro"
    assert body["response_format"]["type"] == "json_schema"
    assert body["tools"][0]["type"] == "web_search"
    assert body["tools"][0]["filters"]["search_domain_filter"] == ["sec.gov"]
    assert out["net_income"] == PerplexityValue(value=1234.0, adjusted=1300.0,
                                                source_url="https://www.sec.gov/Archives/edgar/x.htm",
                                                source_title=None)
    assert "capex" not in out  # null -> weggelassen

@respx.mock
def test_fetch_consensus_has_no_domain_filter():
    respx.post("https://api.perplexity.ai/v1/agent").mock(return_value=httpx.Response(
        200, json=_resp({"revenue": 5000.0}, ["https://finance.example.com/x"])))
    c = PerplexityClient(api_key="pk", model="sonar-pro")
    out = c.fetch_consensus(company_name="Acme", ticker="ACME", forward_year=2026,
                            keys=["revenue"], currency="USD")
    assert out["revenue"].value == 5000.0

@respx.mock
def test_api_error_raises_clean():
    respx.post("https://api.perplexity.ai/v1/agent").mock(return_value=httpx.Response(500))
    c = PerplexityClient(api_key="pk", model="sonar-pro")
    import pytest
    with pytest.raises(Exception):
        c.fetch_period(company_name="A", ticker="A", fiscal_year=2024,
                       missing_keys=["revenue"], currency="USD")
```

- [ ] **Step 2: Run — expect FAIL**
- [ ] **Step 3: Implement** `perplexity.py`:

```python
"""Duenner Client fuer die Perplexity Agent-API (POST /v1/agent).
Strukturierte Ausgabe via response_format json_schema; Quellen aus citations.
Keine Gates, kein Retry-Zoo — 429/5xx werden vom Aufrufer/rate_limiter behandelt.
"""

import json
import logging
from dataclasses import dataclass

import httpx

from app.values.metric_definitions import PERIOD_DOMAIN_ALLOWLIST
from app.values.schema_builder import build_consensus_schema, build_period_schema

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PerplexityValue:
    value: float
    adjusted: float | None
    source_url: str | None
    source_title: str | None


class PerplexityClient:
    def __init__(self, api_key: str, model: str,
                 base_url: str = "https://api.perplexity.ai", timeout: float = 120.0):
        self._key = api_key
        self._model = model
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def _post(self, input_text: str, response_format: dict,
              domain_filter: list[str] | None) -> tuple[dict, str | None, str | None]:
        web = {"type": "web_search", "search_context_size": "high"}
        if domain_filter:
            web["filters"] = {"search_domain_filter": domain_filter}
        body = {
            "model": self._model,
            "input": input_text,
            "response_format": response_format,
            "tools": [web],
            "stream": False,
        }
        with httpx.Client(timeout=self._timeout) as client:
            r = client.post(f"{self._base}/v1/agent", json=body,
                            headers={"Authorization": f"Bearer {self._key}"})
            r.raise_for_status()
            data = r.json()
        payload = self._parse_output(data.get("output_text"))
        url, title = self._first_citation(data)
        return payload, url, title

    @staticmethod
    def _parse_output(output_text) -> dict:
        if not output_text:
            return {}
        try:
            return json.loads(output_text)
        except (json.JSONDecodeError, TypeError):
            logger.warning("perplexity: output_text not valid JSON")
            return {}

    @staticmethod
    def _first_citation(data: dict) -> tuple[str | None, str | None]:
        cits = data.get("citations") or []
        if cits:
            first = cits[0]
            if isinstance(first, str):
                return first, None
            if isinstance(first, dict):
                return first.get("url"), first.get("title")
        for sr in data.get("search_results") or []:
            if isinstance(sr, dict) and sr.get("url"):
                return sr["url"], sr.get("title")
        return None, None

    def _to_values(self, payload: dict, keys: list[str], url, title) -> dict[str, PerplexityValue]:
        out: dict[str, PerplexityValue] = {}
        for k in keys:
            v = payload.get(k)
            if v is None:
                continue
            adj = payload.get(f"{k}_adjusted")
            out[k] = PerplexityValue(value=float(v),
                                     adjusted=float(adj) if adj is not None else None,
                                     source_url=url, source_title=title)
        return out

    def fetch_period(self, *, company_name: str, ticker: str, fiscal_year: int,
                     missing_keys: list[str], currency: str) -> dict[str, PerplexityValue]:
        prompt = (
            f"Report the exact as-reported fundamental financial figures for "
            f"{company_name} (ticker {ticker}), fiscal year {fiscal_year}, from its "
            f"official US-GAAP filings (10-K/10-Q/8-K). Currency {currency}, amounts in "
            f"millions unless the field says otherwise. Use null for any figure you cannot "
            f"find in an official filing. Do not estimate. Only these metrics: "
            f"{', '.join(missing_keys)}."
        )
        payload, url, title = self._post(prompt, build_period_schema(), PERIOD_DOMAIN_ALLOWLIST)
        return self._to_values(payload, missing_keys, url, title)

    def fetch_consensus(self, *, company_name: str, ticker: str, forward_year: int,
                        keys: list[str], currency: str) -> dict[str, PerplexityValue]:
        prompt = (
            f"Report the current Wall-Street analyst consensus estimates for "
            f"{company_name} (ticker {ticker}) for fiscal year {forward_year}. "
            f"Currency {currency}, amounts in millions unless the field says otherwise. "
            f"Use null where no consensus is available. Only these metrics: {', '.join(keys)}."
        )
        payload, url, title = self._post(prompt, build_consensus_schema(keys), None)
        return self._to_values(payload, keys, url, title)
```

- [ ] **Step 4: Run — expect PASS**
- [ ] **Step 5: Commit** `git commit -am "feat(llm): perplexity agent-api client"`

---

## Task 5: ValueOrchestrator — Skelett, Stammdaten, EDGAR, Persistenz-Prioritaet

**Files:**
- Create: `backend/app/values/orchestrator.py`
- Test: `backend/tests/test_orchestrator_core.py`

**Kontext / Interfaces:** Der Orchestrator bekommt injizierbare Kollaborateure (Fakes im Test):
- `stammdaten_fetch(company) -> dict[key, (value, currency)]` (Yahoo-Adapter)
- `edgar_fetch(company, years) -> dict[(key, year), AnchorValue]`
- `perplexity` (Task 4/6)
Persistenz ueber bestehende `persistence.normalize_sign` / `currency_conflict` und das `CompanyValue`-Model. Prioritaet: bestehende `manually_overridden`-Zeile NIE ueberschreiben; EDGAR (primary_method="provider") schlaegt Perplexity — Perplexity schreibt nur, wo keine provider/manual-Zeile existiert.

- [ ] **Step 1: Failing test** (nur Stammdaten + EDGAR + Prioritaet, Perplexity=Fake mit leerem Ergebnis)

```python
# backend/tests/test_orchestrator_core.py — nutzt conftest DB-Fixtures
# (siehe bestehende tests/conftest.py fuer db + company Factory)
from decimal import Decimal
from app.values.orchestrator import ValueOrchestrator, AnchorValue
from app.values.models import CompanyValue

class FakePerplexity:
    def fetch_period(self, **k): return {}
    def fetch_consensus(self, **k): return {}

def _rows(db, cid, key, year):
    return db.query(CompanyValue).filter_by(company_id=cid, value_key=key, period_year=year).all()

def test_edgar_values_persisted_1to1(db, us_company):
    orch = ValueOrchestrator(
        db=db,
        stammdaten_fetch=lambda c: {"market_cap": (Decimal("1000"), "USD"),
                                     "stock_price": (Decimal("10"), "USD"),
                                     "shares_outstanding": (Decimal("100"), "USD")},
        edgar_fetch=lambda c, years: {("net_income", 2024): AnchorValue(Decimal("500"),
                                       "SEC EDGAR", "https://sec.gov/x", "USD")},
        perplexity=FakePerplexity(),
        history_years=1,
    )
    orch.run(us_company)
    r = _rows(db, us_company.id, "net_income", 2024)
    assert len(r) == 1 and r[0].numeric_value == Decimal("500")
    assert r[0].source_name == "SEC EDGAR" and r[0].primary_method == "provider"

def test_manual_override_never_touched(db, us_company):
    db.add(CompanyValue(company_id=us_company.id, value_key="net_income", period_type="FY",
                        period_year=2024, numeric_value=Decimal("999"),
                        source_name="Manual Override", manually_overridden=True,
                        primary_method="manual"))
    db.flush()
    orch = ValueOrchestrator(
        db=db, stammdaten_fetch=lambda c: {},
        edgar_fetch=lambda c, years: {("net_income", 2024): AnchorValue(Decimal("500"),
                                       "SEC EDGAR", "https://sec.gov/x", "USD")},
        perplexity=FakePerplexity(), history_years=1)
    orch.run(us_company)
    r = _rows(db, us_company.id, "net_income", 2024)
    assert len(r) == 1 and r[0].numeric_value == Decimal("999")  # unveraendert
```

> Falls `conftest.py` noch keine `us_company`-Fixture hat: eine minimale hinzufuegen (US-ISIN, ticker, fiscal_year_end 12/31). Bestehende Company-Factory-Muster in `tests/conftest.py` folgen.

- [ ] **Step 2: Run — expect FAIL**
- [ ] **Step 3: Implement** `orchestrator.py` (Kern; Perplexity-Fuellung folgt in Task 6):

```python
"""Neuer, schlanker Refresh-Kern (ersetzt den routes.py-Orchestrator).
Reihenfolge pro Firma: Stammdaten (Feed) -> EDGAR-Anker -> [Task6: Perplexity
Luecken + Konsens] -> engine.py Ableitung. Keine Gates.
Prioritaet pro Zelle: Manual > EDGAR(provider) > Perplexity > leer.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.values.always_current import ALWAYS_CURRENT_KEYS
from app.values.models import CompanyValue
from app.values.persistence import currency_conflict, normalize_sign
from app.values.provider_anchor import running_fy_year

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnchorValue:
    value: Decimal
    source_name: str
    source_link: str | None
    currency: str | None


class ValueOrchestrator:
    def __init__(self, *, db, stammdaten_fetch, edgar_fetch, perplexity,
                 history_years: int = 5):
        self.db = db
        self.stammdaten_fetch = stammdaten_fetch
        self.edgar_fetch = edgar_fetch
        self.perplexity = perplexity
        self.history_years = history_years

    # ---- helpers ---------------------------------------------------------
    def _existing(self, company_id, key, year, period_type="FY"):
        return (self.db.query(CompanyValue)
                .filter_by(company_id=company_id, value_key=key,
                           period_year=year, period_type=period_type)
                .one_or_none())

    def _writable(self, row) -> bool:
        """True wenn eine (nicht vorhandene / nicht manuelle / nicht provider) Zelle
        beschrieben werden darf."""
        if row is None:
            return True
        if row.manually_overridden or row.primary_method in ("manual", "provider"):
            return False
        return True

    def _upsert(self, company_id, key, year, *, value, source_name, source_link,
                currency, primary_method, is_forecast=False, adjusted=None,
                period_type="FY"):
        row = self._existing(company_id, key, year, period_type)
        if not self._writable(row):
            return
        value = normalize_sign(key, value)
        now = datetime.now(timezone.utc)
        if row is None:
            row = CompanyValue(id=uuid4(), company_id=company_id, value_key=key,
                               period_type=period_type, period_year=year)
            self.db.add(row)
        if currency_conflict(key, getattr(row, "currency", None), currency):
            logger.info("currency conflict %s FY%s: %s->%s (overwrite)",
                        key, year, row.currency, currency)
        row.numeric_value = value
        row.numeric_value_adjusted = Decimal(str(adjusted)) if adjusted is not None else None
        row.source_name = source_name
        row.source_link = source_link
        row.currency = currency
        row.primary_method = primary_method
        row.is_forecast = is_forecast
        row.manually_overridden = False
        row.fetched_at = now
        row.last_refresh_attempt = now

    # ---- flow ------------------------------------------------------------
    def target_years(self, company) -> list[int]:
        run = running_fy_year(company)
        return list(range(run - (self.history_years - 1), run + 1))

    def _apply_stammdaten(self, company):
        for key, (val, cur) in (self.stammdaten_fetch(company) or {}).items():
            if key not in ALWAYS_CURRENT_KEYS:
                continue
            self._upsert(company.id, key, None, value=val, source_name="Market Data Feed",
                         source_link=None, currency=cur, primary_method="market_feed")

    def _apply_edgar(self, company, years):
        for (key, year), av in (self.edgar_fetch(company, years) or {}).items():
            self._upsert(company.id, key, year, value=av.value, source_name=av.source_name,
                         source_link=av.source_link, currency=av.currency,
                         primary_method="provider")

    def run(self, company):
        years = self.target_years(company)
        self._apply_stammdaten(company)
        self._apply_edgar(company, years)
        self._apply_perplexity(company, years)   # Task 6
        self.db.flush()
        self._derive_calculations(company, years)  # Task 6 (engine.py)
        self.db.flush()

    # In Task 5 noch No-ops, in Task 6 implementiert:
    def _apply_perplexity(self, company, years):
        return

    def _derive_calculations(self, company, years):
        return
```

- [ ] **Step 4: Run — expect PASS**
- [ ] **Step 5: Commit** `git commit -am "feat(values): value orchestrator core (stammdaten, edgar, priority)"`

---

## Task 6: Orchestrator — Perplexity-Luecken, Konsens, engine.py-Ableitung

**Files:**
- Modify: `backend/app/values/orchestrator.py`
- Test: `backend/tests/test_orchestrator_perplexity.py`

**Kontext:** `_apply_perplexity` fuellt pro Historien-FY nur die noch leeren Fundamental-Keys (kein provider/manual-Row, kein Wert) mit EINEM `fetch_period`-Call; fuers laufende FY (letztes Zieljahr, sofern FY nicht abgeschlossen → Forecast) EINEN `fetch_consensus`-Call, Rows `is_forecast=True`, `primary_method="perplexity_consensus"`. `_derive_calculations` ruft die bestehende `engine.calculate_stammdaten`/`calculate_fy` und persistiert Calculated wie der Alt-Pfad in `persistence._run_and_persist_calculations` (diese Funktion wiederverwenden statt neu bauen).

- [ ] **Step 1: Failing test**

```python
# backend/tests/test_orchestrator_perplexity.py
from decimal import Decimal
from app.values.orchestrator import ValueOrchestrator
from app.llm.perplexity import PerplexityValue
from app.values.models import CompanyValue

class FakePplx:
    def __init__(self): self.period_calls = []; self.consensus_calls = []
    def fetch_period(self, *, company_name, ticker, fiscal_year, missing_keys, currency):
        self.period_calls.append((fiscal_year, tuple(sorted(missing_keys))))
        if "revenue" in missing_keys:
            return {"revenue": PerplexityValue(Decimal("900"), Decimal("950"),
                                               "https://sec.gov/r", None)}
        return {}
    def fetch_consensus(self, *, company_name, ticker, forward_year, keys, currency):
        self.consensus_calls.append(forward_year)
        return {"revenue": PerplexityValue(Decimal("1100"), None, "https://x/c", None)}

def test_perplexity_fills_only_missing(db, us_company):
    pplx = FakePplx()
    orch = ValueOrchestrator(
        db=db, stammdaten_fetch=lambda c: {},
        edgar_fetch=lambda c, years: {},   # nichts vom Anker -> alles offen
        perplexity=pplx, history_years=2)
    orch.run(us_company)
    rev = db.query(CompanyValue).filter_by(company_id=us_company.id, value_key="revenue").all()
    assert any(r.source_name == "Perplexity" and r.numeric_value == Decimal("900") for r in rev)
    # adjusted mitgeschrieben
    assert any(r.numeric_value_adjusted == Decimal("950") for r in rev)
    # Konsens fuers Forwardjahr als Forecast
    assert any(r.is_forecast and r.primary_method == "perplexity_consensus" for r in rev)

def test_perplexity_skips_keys_already_anchored(db, us_company):
    from app.values.orchestrator import AnchorValue
    pplx = FakePplx()
    orch = ValueOrchestrator(
        db=db, stammdaten_fetch=lambda c: {},
        edgar_fetch=lambda c, years: {("revenue", years[0]): AnchorValue(
            Decimal("777"), "SEC EDGAR", "https://sec.gov/e", "USD")},
        perplexity=pplx, history_years=2)
    orch.run(us_company)
    # der geankerte FY darf revenue NICHT bei Perplexity anfragen
    anchored_year = orch.target_years(us_company)[0]
    assert all("revenue" not in keys for (fy, keys) in pplx.period_calls if fy == anchored_year)
```

- [ ] **Step 2: Run — expect FAIL**
- [ ] **Step 3: Implement** — `_apply_perplexity` + `_derive_calculations`:

```python
    def _missing_fundamental_keys(self, company_id, year):
        from app.values.schema_builder import fundamental_keys
        missing = []
        for k in fundamental_keys():
            row = self._existing(company_id, k, year)
            if row is None or (row.numeric_value is None
                               and row.primary_method not in ("manual", "provider")):
                missing.append(k)
        return missing

    def _apply_perplexity(self, company, years):
        from app.values.provider_anchor import _fy_is_closed
        currency = getattr(company, "currency", None) or "USD"
        run = years[-1]
        for year in years:
            forward = (year == run) and not _fy_is_closed(company, year)
            keys = self._missing_fundamental_keys(company.id, year)
            if not keys:
                continue
            if forward:
                vals = self.perplexity.fetch_consensus(
                    company_name=company.name, ticker=company.ticker,
                    forward_year=year, keys=keys, currency=currency)
                method, fc, src = "perplexity_consensus", True, "Perplexity"
            else:
                vals = self.perplexity.fetch_period(
                    company_name=company.name, ticker=company.ticker,
                    fiscal_year=year, missing_keys=keys, currency=currency)
                method, fc, src = "perplexity", False, "Perplexity"
            for key, pv in vals.items():
                self._upsert(company.id, key, year, value=Decimal(str(pv.value)),
                             source_name=src, source_link=pv.source_url, currency=currency,
                             primary_method=method, is_forecast=fc,
                             adjusted=pv.adjusted)

    def _derive_calculations(self, company, years):
        from app.values.persistence import run_and_persist_calculations_for_years
        run_and_persist_calculations_for_years(self.db, company, years)
```

> **Step 3b:** In `persistence.py` eine schlanke, oeffentliche `run_and_persist_calculations_for_years(db, company, years)` bereitstellen, die die bestehende `_run_and_persist_calculations`-Logik pro Jahr kapselt (Stammdaten-Map + FY-Map laden, `calculate_stammdaten`/`calculate_fy` rufen, `_persist_calc_results`). Falls die bestehende Funktion bereits genau das tut, nur duenn wrappen — **keine** Neu-Implementierung der Formeln (die bleiben in `engine.py`).

- [ ] **Step 4: Run — expect PASS**
- [ ] **Step 5: Commit** `git commit -am "feat(values): orchestrator perplexity gap-fill + consensus + calc derive"`

---

## Task 7: Verdrahtung — routes.refresh_company_values + batch auf Orchestrator

**Files:**
- Modify: `backend/app/values/routes.py` (`refresh_company_values`, `get_refresh_status` bleibt)
- Modify: `backend/app/values/batch.py` (`_recompute_one`)
- Create: `backend/app/values/adapters.py` — duenne Adapter `yahoo_stammdaten(company)` und `edgar_anchor(company, years)`, die die bestehenden `providers/yahoo.py` bzw. `providers/edgar.py` auf die Orchestrator-Interfaces mappen.
- Test: `backend/tests/test_refresh_uses_orchestrator.py`

- [ ] **Step 1: Failing test** — `refresh_company_values` schreibt EDGAR- + Perplexity-Werte via Orchestrator (Provider/Perplexity gemockt), keine Gate-/Two-Stage-Imports mehr aufgerufen.

```python
# backend/tests/test_refresh_uses_orchestrator.py
# Monkeypatch app.values.adapters.yahoo_stammdaten / edgar_anchor + PerplexityClient,
# rufe refresh_company_values(company_id, RefreshRequest(period_type="FY",
# period_year=<running>), user, db) und pruefe persistierte Zeilen + Quellen.
```

- [ ] **Step 2: Run — expect FAIL**
- [ ] **Step 3: Implement**
  - `adapters.py`: `yahoo_stammdaten(company)` ruft den bestehenden Yahoo-Provider fuer `ALWAYS_CURRENT_KEYS` und liefert `{key: (Decimal, currency)}`. `edgar_anchor(company, years)` ruft den bestehenden EDGAR-Provider/`edgar.py` und liefert `{(key, year): AnchorValue}` fuer alle Perioden, die EDGAR hergibt (FY + Quartale falls vorhanden).
  - `refresh_company_values`: Body ersetzen durch:
    ```python
    from app.config import settings
    from app.llm.perplexity import PerplexityClient
    from app.values.adapters import edgar_anchor, yahoo_stammdaten
    from app.values.orchestrator import ValueOrchestrator
    client = PerplexityClient(api_key=settings.perplexity_api_key,
                              model=settings.perplexity_model,
                              base_url=settings.perplexity_base_url)
    ValueOrchestrator(db=db, stammdaten_fetch=yahoo_stammdaten,
                      edgar_fetch=edgar_anchor, perplexity=client).run(company)
    ```
    Den `RefreshRequest.period_year`/`keys` weiterhin annehmen (API-Kompatibilitaet), aber intern bestimmt der Orchestrator die Jahre via `running_fy_year`. Progress-Phasen (`set_phase`) an den drei Schritten setzen.
  - `batch._recompute_one`: bleibt (ruft `refresh_company_values`), da dieses jetzt delegiert. `select_stale_companies`: `primary_method`-Filter auf `("perplexity", "perplexity_consensus", "provider")` aktualisieren (statt `two_stage%/statement_research`).
- [ ] **Step 4: Run — expect PASS**; zusaetzlich `uv run pytest tests/test_batch_recompute.py -v` gruen halten (ggf. Fixtures anpassen).
- [ ] **Step 5: Commit** `git commit -am "feat(values): route refresh + batch through new orchestrator"`

---

## Task 8: Alt-Module + Tests entfernen, provider_anchor trimmen, Suite gruen

**Files:**
- Delete: `statement_research.py`, `guidance_estimates.py`, `consistency.py`, `gaap_bridge.py`, `adjusted_enrichment.py`, `providers/esef.py`
- Modify: `providers/registry.py` (ESEF-Eintrag raus), `provider_anchor.py` (auf `running_fy_year`, `_fy_is_closed` + genutzte Helfer reduzieren), `routes.py` (tote Helfer `_process_one_key`, `_n2_fy_anchor_missing`, `_prev_year_needs_backfill`, `_ensure_previous_year_inputs`, `_anchor_*` entfernen)
- Delete tests: siehe File-Structure-Liste

- [ ] **Step 1:** Grep nach Importen der zu loeschenden Module: `cd backend && grep -rl "statement_research\|guidance_estimates\|import consistency\|from app.values.consistency\|gaap_bridge\|adjusted_enrichment\|providers.esef\|import esef" app | sort -u`
- [ ] **Step 2:** Pro Treffer den Import/Aufruf entfernen (die Funktionalitaet ist im Orchestrator ersetzt). `_fy_is_closed` und `running_fy_year` in `provider_anchor.py` behalten.
- [ ] **Step 3:** Module + zugehoerige Testdateien loeschen. `git rm <dateien>`
- [ ] **Step 4:** `cd backend && uv run ruff check app && uv run python -c "import app.main"` — muss ohne ImportError durchlaufen (Compile-Check; **Services nicht starten** — der User startet selbst).
- [ ] **Step 5:** `cd backend && uv run pytest -q` — komplette Suite gruen (verbliebene Tests). Rot markierte Alt-Tests, die geloeschte Features pruefen, mit-loeschen (nicht anpassen).
- [ ] **Step 6: Commit** `git commit -am "refactor(values): remove legacy acquisition pipeline (research/gates/estimates/esef)"`

---

## Abschluss

- [ ] **Frontend-Check (nur Kompilierung):** `cd frontend && npm run build` — das Dashboard nutzt dieselben `company_values`-Felder; keine FE-Aenderung geplant. Falls das FE auf entfernte `primary_method`-Strings prueft (z.B. Badges), Mapping auf `perplexity`/`perplexity_consensus`/`provider`/`market_feed`/`manual`/`not_found` anpassen.
- [ ] **Offen (spaeter, nicht in diesem Plan):** exakte Perplexity-Modell-ID am echten Key verifizieren; Domain-Allowlist um IR-Domains erweitern; Kostenbudget pro Full-Recompute beobachten; Nicht-US-Wiederaufnahme als eigene Spec/Plan.

**Referenz-Skills:** @superpowers:test-driven-development, @superpowers:subagent-driven-development, @superpowers:verification-before-completion.
