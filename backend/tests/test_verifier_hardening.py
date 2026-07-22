"""Verifier-Haertung: unabhaengige Evidenz statt Zirkel-Pruefung.

1. Der Verifier bekommt is_estimate/adjusted-Felder — sonst kann er
   Estimate-Regeln und Adjusted-vs-Reported gar nicht beurteilen.
2. Korrekturen greifen NUR bei verdict='correct' — ein 'confirm' oder
   'insufficient_evidence' mit versehentlich befuellten corrections darf
   den Extractor-Wert nicht stillschweigend ueberschreiben.
3. Der Verifier hat Web-Search (unabhaengige Quelle) und genug
   max_tokens, damit Korrektur-JSON nicht abgeschnitten wird.
"""

from decimal import Decimal

from scripts.two_stage_research import (
    VERIFIER_MAX_TOKENS,
    VERIFIER_SYSTEM,
    VERIFIER_WEB_SEARCH_MAX_USES,
    ExtractResult,
    QuarterValue,
    TwoStageResult,
    VerifierVerdict,
)


def _extract(q1_value=Decimal("100")):
    return ExtractResult(
        ticker="TST", value_key="revenue", year=2025, currency="EUR",
        q1=QuarterValue(value=q1_value, source_quote="Q1 revenue was 100",
                        source_url="https://example.com/q1", is_estimate=True,
                        adjusted_value=Decimal("110"), adjustments_note="ex one-offs"),
        q2=None, q3=None, q4=None, fy=None,
        quarter_only=None, is_adjusted_note=None,
    )


def test_to_verifier_json_includes_estimate_and_adjusted_fields():
    j = _extract().to_verifier_json()
    assert j["q1"]["is_estimate"] is True
    assert j["q1"]["adjusted_value"] == "110"
    assert j["q1"]["adjustments_note"] == "ex one-offs"


def test_corrections_ignored_unless_verdict_correct():
    verdict = VerifierVerdict(
        verdict="confirm",
        corrections={"Q1": Decimal("999")},
        reason="", confidence=0.9, flags=[],
    )
    result = TwoStageResult(extract=_extract(), verdict=verdict)
    assert result.final_values["Q1"] == Decimal("100")


def test_corrections_ignored_on_insufficient_evidence():
    verdict = VerifierVerdict(
        verdict="insufficient_evidence",
        corrections={"Q1": Decimal("999")},
        reason="no quote", confidence=0.2, flags=[],
    )
    result = TwoStageResult(extract=_extract(), verdict=verdict)
    assert result.final_values["Q1"] == Decimal("100")


def test_corrections_applied_on_correct_verdict():
    verdict = VerifierVerdict(
        verdict="correct",
        corrections={"Q1": Decimal("250")},
        reason="quote proves per-share confusion", confidence=0.8, flags=[],
    )
    result = TwoStageResult(extract=_extract(), verdict=verdict)
    assert result.final_values["Q1"] == Decimal("250")


def test_verifier_has_web_search_and_room_for_corrections():
    assert VERIFIER_WEB_SEARCH_MAX_USES >= 2
    assert VERIFIER_MAX_TOKENS >= 2048
    assert "web_search" in VERIFIER_SYSTEM
