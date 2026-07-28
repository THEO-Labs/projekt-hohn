"""_derive_missing_quarter: das Residual-Quartal (FY - Summe der drei
vorhandenen) muss is_estimate von seinen Basen ERBEN — ein aus Forecasts
errechnetes Quartal ist selbst eine Schaetzung, kein Actual."""

from decimal import Decimal

from scripts.two_stage_research import (
    ExtractResult,
    QuarterValue,
    TwoStageResult,
    VerifierVerdict,
    _derive_missing_quarter,
)


def _qv(value, is_estimate=False):
    return QuarterValue(value=Decimal(str(value)), source_quote="q",
                        source_url=None, is_estimate=is_estimate)


def _result(q1, q2, q3, fy, fy_estimate=False, q_estimates=(False, False, False)):
    extract = ExtractResult(
        ticker="TST", value_key="revenue", year=2025, currency="EUR",
        q1=_qv(q1, q_estimates[0]), q2=_qv(q2, q_estimates[1]),
        q3=_qv(q3, q_estimates[2]), q4=None,
        fy=_qv(fy, fy_estimate), quarter_only=None, is_adjusted_note=None,
    )
    verdict = VerifierVerdict(verdict="confirm", corrections={}, reason="",
                              confidence=0.9, flags=[])
    return TwoStageResult(extract=extract, verdict=verdict)


def test_all_actual_bases_derive_actual_quarter():
    r = _result(100, 200, 300, 1000)
    _derive_missing_quarter(r)
    assert r.extract.q4 is not None
    assert r.extract.q4.value == Decimal("400")
    assert r.extract.q4.is_estimate is False


def test_estimated_base_quarter_inherits_estimate():
    r = _result(100, 200, 300, 1000, q_estimates=(False, True, False))
    _derive_missing_quarter(r)
    assert r.extract.q4.value == Decimal("400")
    assert r.extract.q4.is_estimate is True


def test_estimated_fy_inherits_estimate():
    r = _result(100, 200, 300, 1000, fy_estimate=True)
    _derive_missing_quarter(r)
    assert r.extract.q4.value == Decimal("400")
    assert r.extract.q4.is_estimate is True
