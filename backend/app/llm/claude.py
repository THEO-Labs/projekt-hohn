"""Claude-API-Client-Helpers.

Der fruehere research_value-Monolith (Web-Recherche pro Zelle) ist
entfernt — Recherche laeuft ausschliesslich ueber die neuen Module
(statement_research, gaap_bridge, guidance_estimates,
adjusted_enrichment). Hier lebt nur noch die geteilte Client-Basis.
"""

import logging

import anthropic

from app.config import settings
from app.llm.rate_limiter import claude_limiter  # noqa: F401 — Re-Export fuer Caller

logger = logging.getLogger(__name__)


def get_client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise ValueError("Anthropic API key is not configured")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 5,
}


def _collect_text(response) -> str:
    parts = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()
