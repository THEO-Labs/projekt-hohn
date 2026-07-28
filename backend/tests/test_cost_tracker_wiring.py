"""CostTracker-Verdrahtung: add_response muss nach JEDEM API-Call laufen,
sonst ist der Budget-Cap (max_cost_usd) ein No-op — spent_usd bliebe 0 und
check_budget wuerde nie ausloesen."""

import json
from decimal import Decimal
from types import SimpleNamespace

import scripts.two_stage_research as ts
from scripts.two_stage_research import CostTracker, research_two_stage, run_extractor, run_verifier


# JSON, das sowohl den Extractor- als auch den Verifier-Parser befriedigt.
_COMBINED_PAYLOAD = {
    "q1": None, "q2": None, "q3": None, "q4": None,
    "fy": {"value": 1000, "source_quote": "FY quote", "source_url": None,
           "is_estimate": False},
    "extractor_note_adjusted_vs_reported": None,
    "verdict": "confirm", "corrections": {}, "reason": "",
    "confidence": 0.9, "flags": [],
}


def _fake_client():
    response = SimpleNamespace(
        content=[SimpleNamespace(text=json.dumps(_COMBINED_PAYLOAD))],
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
    )
    return SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kw: response),
    )


class _PassThroughLimiter:
    def call(self, fn):
        return fn()


def _install(monkeypatch):
    monkeypatch.setattr(ts, "get_client", _fake_client)
    monkeypatch.setenv("TWO_STAGE_MEDIAN_SAMPLES", "1")


def test_run_extractor_records_cost(monkeypatch):
    _install(monkeypatch)
    tracker = CostTracker()
    run_extractor(
        ticker="TST", company_name="TestCo", value_key="ebitda", year=2025,
        currency="EUR", mode="historic", limiter=_PassThroughLimiter(),
        cost_tracker=tracker,
    )
    assert tracker.calls == 1
    assert tracker.spent_usd > 0


def test_run_verifier_records_cost(monkeypatch):
    _install(monkeypatch)
    extract = ts.ExtractResult(
        ticker="TST", value_key="ebitda", year=2025, currency="EUR",
        q1=None, q2=None, q3=None, q4=None,
        fy=ts.QuarterValue(value=Decimal("1000"), source_quote="q", source_url=None),
        quarter_only=None, is_adjusted_note=None,
    )
    tracker = CostTracker()
    run_verifier(extract=extract, limiter=_PassThroughLimiter(), cost_tracker=tracker)
    assert tracker.calls == 1
    assert tracker.spent_usd > 0


def test_research_two_stage_passes_tracker_through(monkeypatch):
    """1x Extractor + 1x Verifier = 2 getrackte Calls, spent_usd > 0."""
    _install(monkeypatch)
    tracker = CostTracker(max_usd=100.0)
    research_two_stage(
        ticker="TST", company_name="TestCo", value_key="ebitda", year=2025,
        currency="EUR", mode="historic", limiter=_PassThroughLimiter(),
        cost_tracker=tracker,
    )
    assert tracker.calls == 2
    assert tracker.spent_usd > 0


def test_signatures_backwards_compatible(monkeypatch):
    """Ohne cost_tracker (Default None) laeuft alles wie zuvor."""
    _install(monkeypatch)
    result = run_extractor(
        ticker="TST", company_name="TestCo", value_key="ebitda", year=2025,
        currency="EUR", mode="historic", limiter=_PassThroughLimiter(),
    )
    assert result.fy.value == Decimal("1000")
