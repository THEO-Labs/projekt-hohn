"""Median-of-N fuer kritische FY-Werte: Ausreisser einzelner Extraktionen
(Web-Search-Nichtdeterminismus) werden durch Mehrfach-Sampling eliminiert.
Gewaehlt wird die KOMPLETTE Extraktion mit dem Median-FY (keine Perioden-
Mischung ueber Samples)."""

from decimal import Decimal

from scripts.two_stage_research import (
    CRITICAL_MEDIAN_KEYS,
    ExtractResult,
    QuarterValue,
    _pick_median_extract,
)


def _er(fy_value):
    fy = None
    if fy_value is not None:
        fy = QuarterValue(value=Decimal(str(fy_value)), source_quote="q", source_url=None)
    return ExtractResult(
        ticker="TST", value_key="net_income", year=2026, currency="EUR",
        q1=None, q2=None, q3=None, q4=None, fy=fy,
        quarter_only=None, is_adjusted_note=None,
    )


def test_median_of_three_picks_middle():
    a, b, c = _er(900), _er(100), _er(200)
    assert _pick_median_extract([a, b, c]).fy.value == Decimal("200")


def test_single_candidate_returned():
    a = _er(100)
    assert _pick_median_extract([a]) is a


def test_null_fy_candidates_ignored():
    a, b, c = _er(None), _er(300), _er(100)
    assert _pick_median_extract([a, b, c]).fy.value == Decimal("300")


def test_all_null_returns_first():
    a, b = _er(None), _er(None)
    assert _pick_median_extract([a, b]) is a


def test_critical_keys():
    assert {"net_income", "revenue", "fcf"} <= CRITICAL_MEDIAN_KEYS
