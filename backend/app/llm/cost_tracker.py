"""Kosten-Tracking fuer Claude-Batch-Laeufe: kumulierte API-Kosten + Budget-Cap.

Genutzt von statement_research, gaap_bridge, adjusted_enrichment und
guidance_estimates (Kosten-Logging pro Refresh-Phase).
"""

from dataclasses import dataclass

# Kostenschaetzung pro 1M Tokens (USD, Stand 2026-07)
_COST_TABLE = {
    "claude-sonnet-4-6":  {"in": 3.00,  "out": 15.00},
    "claude-opus-4-7":    {"in": 15.00, "out": 75.00},
    "claude-haiku-4-5":   {"in": 1.00, "out": 5.00},
    "claude-haiku-4-5-20251001": {"in": 1.00, "out": 5.00},
}
_WEB_SEARCH_COST_PER_CALL = 0.01  # $10 pro 1000 Web-Searches


@dataclass
class CostTracker:
    """Kumulierte API-Kosten eines Laufs + Budget-Cap-Durchsetzung."""
    max_usd: float | None = None
    spent_usd: float = 0.0
    calls: int = 0

    def add_response(self, response, model: str, web_search_calls: int = 0) -> float:
        usage = getattr(response, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        rates = _COST_TABLE.get(model.replace("[1m]", ""), {"in": 3.0, "out": 15.0})
        cost = (in_tok * rates["in"] + out_tok * rates["out"]) / 1_000_000
        cost += web_search_calls * _WEB_SEARCH_COST_PER_CALL
        self.spent_usd += cost
        self.calls += 1
        return cost

    def check_budget(self) -> None:
        if self.max_usd is not None and self.spent_usd >= self.max_usd:
            raise RuntimeError(
                f"Budget cap reached: spent ${self.spent_usd:.2f} >= max ${self.max_usd:.2f} "
                f"after {self.calls} calls"
            )
