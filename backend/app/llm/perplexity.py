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
            f"official US-GAAP filings (10-K/10-Q/8-K). Report EVERY monetary figure as the "
            f"FULL amount in {currency} with all digits — e.g. 391035000000 for 391 billion. "
            f"Do NOT scale to thousands, millions or billions. eps_diluted stays per-share "
            f"(e.g. 6.08). Use null for any figure you cannot find in an official filing. "
            f"Do not estimate. Only these metrics: {', '.join(missing_keys)}."
        )
        payload, url, title = self._post(prompt, build_period_schema(), PERIOD_DOMAIN_ALLOWLIST)
        return self._to_values(payload, missing_keys, url, title)

    def fetch_consensus(self, *, company_name: str, ticker: str, forward_year: int,
                        keys: list[str], currency: str) -> dict[str, PerplexityValue]:
        prompt = (
            f"Provide your best estimate of {company_name}'s (ticker {ticker}) fiscal year "
            f"{forward_year} figures, based on the company's guidance, current Wall-Street "
            f"analyst consensus and recent run-rate. Report EVERY monetary figure as the "
            f"FULL amount in {currency} with all digits — e.g. 391035000000 for 391 billion. "
            f"Do NOT scale to thousands, millions or billions. eps_diluted stays per-share "
            f"(e.g. 6.08). Provide a numeric estimate for every metric you can reasonably "
            f"project; use null only if you truly have no basis. Only these metrics: "
            f"{', '.join(keys)}."
        )
        payload, url, title = self._post(prompt, build_consensus_schema(keys), None)
        return self._to_values(payload, keys, url, title)
