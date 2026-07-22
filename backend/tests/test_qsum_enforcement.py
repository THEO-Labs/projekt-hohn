"""Code-seitige Q-Sum-Konsistenz: Prompts und Verifier fordern Q1..Q4 = FY,
erzwingen es aber nicht (Adidas-EBITDA-Fall: Summe +22% ueber FY, Verdict
trotzdem 'verified'). _enforce_qsum_consistency rechnet deterministisch das
letzte geschaetzte Quartal als FY - Summe(uebrige) neu."""

from decimal import Decimal

from scripts.two_stage_research import (
    ExtractResult,
    QuarterValue,
    TwoStageResult,
    VerifierVerdict,
    _enforce_qsum_consistency,
)


def _qv(value, is_estimate=True, quote="q"):
    return QuarterValue(value=Decimal(str(value)), source_quote=quote,
                        source_url=None, is_estimate=is_estimate)


def _result(q1, q2, q3, q4, fy, verdict="confirm", corrections=None,
            value_key="ebitda", q_estimates=(False, True, True, True)):
    extract = ExtractResult(
        ticker="TST", value_key=value_key, year=2026, currency="EUR",
        q1=_qv(q1, q_estimates[0]), q2=_qv(q2, q_estimates[1]),
        q3=_qv(q3, q_estimates[2]), q4=_qv(q4, q_estimates[3]),
        fy=_qv(fy), quarter_only=None, is_adjusted_note=None,
    )
    vv = VerifierVerdict(verdict=verdict, corrections=corrections or {},
                         reason="", confidence=0.9, flags=[])
    return TwoStageResult(extract=extract, verdict=vv)


def test_mismatch_rederives_last_estimated_quarter():
    # Adidas-EBITDA-Fall: 1005+1250+1350+600 = 4205 vs FY 3450
    r = _result(1005, 1250, 1350, 600, 3450)
    _enforce_qsum_consistency(r)
    f = r.final_values
    assert f["Q4"] == Decimal("-155")  # 3450 - 3605
    assert f["Q1"] + f["Q2"] + f["Q3"] + f["Q4"] == f["FY"]


def test_consistent_sum_untouched():
    r = _result(100, 200, 300, 400, 1000)
    _enforce_qsum_consistency(r)
    assert r.final_values["Q4"] == Decimal("400")


def test_all_reported_quarters_never_rewritten():
    # Alle Quartale sind Actuals: Arithmetik-Fehler ist dann ein
    # Extraktionsfehler, kein Schaetz-Residuum — nichts anfassen.
    r = _result(100, 200, 300, 600, 1000, q_estimates=(False, False, False, False))
    _enforce_qsum_consistency(r)
    assert r.final_values["Q4"] == Decimal("600")


def test_stock_metric_skipped():
    r = _result(100, 200, 300, 600, 1000, value_key="net_debt")
    _enforce_qsum_consistency(r)
    assert r.final_values["Q4"] == Decimal("600")


def test_verifier_correction_on_target_also_updated():
    # Verifier hatte Q4 selbst korrigiert (verdict=correct) — die Korrektur
    # wuerde final_values dominieren, also muss sie mit ueberschrieben werden.
    r = _result(1005, 1250, 1350, 600, 3450, verdict="correct",
                corrections={"Q4": Decimal("700")})
    _enforce_qsum_consistency(r)
    assert r.final_values["Q4"] == Decimal("-155")
