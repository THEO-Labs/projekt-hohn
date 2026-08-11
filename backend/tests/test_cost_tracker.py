"""CostTracker-Unit-Tests: Kosten-Akkumulation und Budget-Cap."""

import pytest
from types import SimpleNamespace

from app.llm.cost_tracker import CostTracker


def _resp(in_tok, out_tok):
    return SimpleNamespace(usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok))


def test_add_response_accumulates_cost_and_calls():
    t = CostTracker()
    c1 = t.add_response(_resp(1_000_000, 0), "claude-sonnet-4-6")
    assert c1 == pytest.approx(3.0)
    c2 = t.add_response(_resp(0, 1_000_000), "claude-sonnet-4-6")
    assert c2 == pytest.approx(15.0)
    assert t.spent_usd == pytest.approx(18.0)
    assert t.calls == 2


def test_web_search_calls_add_flat_cost():
    t = CostTracker()
    t.add_response(_resp(0, 0), "claude-sonnet-4-6", web_search_calls=3)
    assert t.spent_usd == pytest.approx(0.03)


def test_unknown_model_uses_default_rates():
    t = CostTracker()
    t.add_response(_resp(1_000_000, 0), "unbekanntes-modell")
    assert t.spent_usd == pytest.approx(3.0)


def test_check_budget_raises_at_cap():
    t = CostTracker(max_usd=1.0)
    t.check_budget()  # unter Cap: kein Fehler
    t.spent_usd = 1.0
    with pytest.raises(RuntimeError):
        t.check_budget()


def test_no_cap_never_raises():
    t = CostTracker()
    t.spent_usd = 10_000.0
    t.check_budget()
