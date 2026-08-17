"""Duenner Client fuer die Perplexity Sonar API (POST /chat/completions).
Strukturierte Ausgabe via response_format json_schema; Quellen aus citations;
Domain-Filter via search_domain_filter; Suchtiefe via web_search_options.
Keine Gates, kein Retry-Zoo — 429/5xx werden vom Aufrufer/rate_limiter behandelt.

Hinweis: Die neue /v1/agent-API akzeptiert die sonar-Modelle NICHT
("model sonar-pro is not supported"). Die klassische /chat/completions-API
liefert mit sonar-pro strukturiertes JSON + citations und ist bis 27.09.2026
unterstuetzt — die nutzen wir.
"""

import logging
from dataclasses import dataclass

import httpx

from app.llm.json_utils import extract_json
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
        body = {
            "model": self._model,
            "messages": [{"role": "user", "content": input_text}],
            "response_format": response_format,
            "web_search_options": {"search_context_size": "high"},
            "stream": False,
        }
        if domain_filter:
            body["search_domain_filter"] = domain_filter
        with httpx.Client(timeout=self._timeout) as client:
            r = client.post(f"{self._base}/chat/completions", json=body,
                            headers={"Authorization": f"Bearer {self._key}"})
            r.raise_for_status()
            data = r.json()
        choices = data.get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else None
        payload = self._parse_output(content)
        url, title = self._first_citation(data)
        return payload, url, title

    @staticmethod
    def _parse_output(output_text) -> dict:
        if not output_text:
            return {}
        try:
            return extract_json(output_text)
        except (ValueError, TypeError):
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

    @staticmethod
    def _to_values(payload: dict, keys: list[str], url, title) -> dict[str, PerplexityValue]:
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
            f"official US-GAAP filings (10-K/10-Q/8-K). Use the EXACT figures from the "
            f"consolidated financial-statement TABLES, NOT rounded numbers from the narrative "
            f"(e.g. 11633000000, never 11600000000). Report EVERY monetary figure as the "
            f"FULL EXACT amount in {currency} with all digits — e.g. 391035000000 for 391 "
            f"billion. Do NOT scale to thousands, millions or billions. eps_diluted stays "
            f"per-share (e.g. 6.08). Use null for any figure you cannot find in an official "
            f"filing. Do not estimate. Only these metrics: {', '.join(missing_keys)}."
        )
        payload, url, title = self._post(prompt, build_period_schema(), PERIOD_DOMAIN_ALLOWLIST)
        return self._to_values(payload, missing_keys, url, title)

    def fetch_consensus(self, *, company_name: str, ticker: str, forward_year: int,
                        keys: list[str], currency: str) -> dict[str, PerplexityValue]:
        prompt = (
            f"Provide your best estimate of {company_name}'s (ticker {ticker}) fiscal year "
            f"{forward_year} figures. PRIORITIZE the company's OWN most recent official "
            f"guidance / outlook (from its latest earnings release, 10-Q/10-K outlook or "
            f"earnings call); combine it with current Wall-Street analyst consensus and the "
            f"recent quarterly run-rate. Report EVERY monetary figure as the "
            f"FULL amount in {currency} with all digits — e.g. 391035000000 for 391 billion. "
            f"Do NOT scale to thousands, millions or billions. eps_diluted stays per-share "
            f"(e.g. 6.08). Provide a numeric estimate for every metric you can reasonably "
            f"project; use null only if you truly have no basis. Only these metrics: "
            f"{', '.join(keys)}."
        )
        payload, url, title = self._post(prompt, build_consensus_schema(keys), None)
        return self._to_values(payload, keys, url, title)

    def fetch_adjusted(self, *, company_name: str, ticker: str, fiscal_year: int,
                       keys: list[str], currency: str) -> dict[str, PerplexityValue]:
        """Firmen-definierte ADJUSTED / Non-GAAP-Werte (adjusted NI/EBITDA/FCF …)
        aus der Non-GAAP-Reconciliation des Earnings-Release."""
        prompt = (
            f"Report {company_name}'s (ticker {ticker}) company-defined ADJUSTED / NON-GAAP "
            f"figures for fiscal year {fiscal_year}, exactly as the company presents them in "
            f"its earnings release / non-GAAP reconciliation (e.g. adjusted net income, "
            f"adjusted EBITDA, adjusted free cash flow). Report the FULL amount in {currency} "
            f"with all digits — e.g. 8500000000 for 8.5 billion. Do NOT scale. Use null if the "
            f"company does NOT report an adjusted/non-GAAP figure for a metric (do not just "
            f"repeat the GAAP number). Only these metrics: {', '.join(keys)}."
        )
        payload, url, title = self._post(prompt, build_consensus_schema(keys), PERIOD_DOMAIN_ALLOWLIST)
        return self._to_values(payload, keys, url, title)

    def fetch_quarter_reported(self, *, company_name: str, ticker: str, fiscal_year: int,
                               quarter: str, keys: list[str], currency: str) -> dict[str, PerplexityValue]:
        """Berichtete (nicht geschaetzte) Quartals-Actuals aus dem Earnings-
        Release / 8-K — fuer Quartale, deren 10-Q-XBRL noch nicht bei EDGAR ist
        (Filing-Lag). Domain-gefiltert auf offizielle Quellen."""
        prompt = (
            f"Report {company_name}'s (ticker {ticker}) ACTUAL reported {quarter} results "
            f"for fiscal year {fiscal_year}, as published in its most recent earnings "
            f"release / press release / 8-K (the 10-Q XBRL may not be filed yet). These are "
            f"REPORTED actuals, NOT estimates. CRITICAL: take the EXACT figures from the "
            f"condensed consolidated financial-statement TABLES (in the 8-K earnings-release "
            f"exhibit or the 10-Q), NOT the rounded numbers in the press-release "
            f"narrative/headline — e.g. report 11633000000, never the rounded 11600000000. "
            f"Report EVERY monetary figure as the FULL EXACT amount in {currency} with all "
            f"digits. Do NOT scale to thousands, millions or billions. eps_diluted stays "
            f"per-share (exact, e.g. 2.98). Use null for any figure not in the tables. "
            f"Only these metrics: {', '.join(keys)}."
        )
        payload, url, title = self._post(prompt, build_consensus_schema(keys), PERIOD_DOMAIN_ALLOWLIST)
        return self._to_values(payload, keys, url, title)

    def fetch_quarter_estimate(self, *, company_name: str, ticker: str, fiscal_year: int,
                               quarter: str, keys: list[str], currency: str) -> dict[str, PerplexityValue]:
        prompt = (
            f"Estimate {company_name}'s (ticker {ticker}) {quarter} results for fiscal year "
            f"{fiscal_year} (only this single fiscal quarter). The earlier quarters of this "
            f"fiscal year are already reported — estimate ONLY {quarter}, using the company's "
            f"own official guidance for the quarter/full year, current analyst consensus and "
            f"the recent quarterly run-rate. Report EVERY monetary figure as the FULL amount "
            f"in {currency} with all digits — e.g. 95000000000 for 95 billion. Do NOT scale "
            f"to thousands, millions or billions. eps_diluted stays per-share (e.g. 1.60). "
            f"Provide a numeric estimate for every metric you can reasonably project; use null "
            f"only if you truly have no basis. Only these metrics: {', '.join(keys)}."
        )
        payload, url, title = self._post(prompt, build_consensus_schema(keys), None)
        return self._to_values(payload, keys, url, title)
