"""Temporal-Logik im Verifier-Prompt: Fuer ein laufendes Geschaeftsjahr ist
der FY-Wert ein Forward-Estimate. Der Adidas-Fall: Verifier fand eine als
'reported FY 2026' gelabelte Website-Zahl (die es im Juli 2026 nicht geben
kann), ersetzte den Konsens-FY und riss die H-Return von +16.8% auf -42%."""

from datetime import date
from decimal import Decimal

from scripts.two_stage_research import (
    ExtractResult,
    QuarterValue,
    _build_verifier_prompt,
)


def _extract(year):
    return ExtractResult(
        ticker="TST", value_key="net_income", year=year, currency="EUR",
        q1=QuarterValue(value=Decimal("484"), source_quote="q1", source_url=None,
                        is_estimate=False),
        q2=None, q3=None, q4=None,
        fy=QuarterValue(value=Decimal("1663"), source_quote="consensus",
                        source_url=None, is_estimate=True),
        quarter_only=None, is_adjusted_note=None,
    )


def test_running_fy_gets_temporal_rule():
    prompt = _build_verifier_prompt(_extract(date.today().year))
    assert "IN-PROGRESS FISCAL YEAR" in prompt
    assert "cannot exist" in prompt


def test_past_fy_has_no_temporal_rule():
    prompt = _build_verifier_prompt(_extract(date.today().year - 2))
    assert "IN-PROGRESS FISCAL YEAR" not in prompt
