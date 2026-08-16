"""EBITDA-Ableitung des EDGAR-Providers: der D&A-Summand ist IMMER die volle
Depreciation AND Amortization inkl. Amortisation immaterieller Werte.

Belegte Fehler (gegen SEC-XBRL): Dynatrace FY2025 199 statt 228 Mio, weil die
28.9M Intangible-Amortisation fehlten (Operating Income + Depreciation-only
statt + volle D&A). Hier hermetisch gegen Fixture-Facts abgesichert:
  - volle D&A-Konzepte (DepreciationDepletionAndAmortization) direkt,
  - Depreciation-only + separate Intangible-Amort -> Summe,
  - nur Depreciation ohne Intangible-Tag -> EBITDA LEER (lieber leer als zu
    niedrig),
  - kein EBITDA ohne GAAP OperatingIncomeLoss (kein Non-GAAP-OI).
"""
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.providers.edgar import EdgarProvider


@pytest.fixture
def provider():
    p = EdgarProvider()
    p._ticker_to_cik = {"DT": "0001773383"}
    return p


def _facts_with(concepts: dict[str, dict]) -> dict:
    return {"facts": {"us-gaap": concepts, "dei": {}}}


def _fy(end: str, val: float, year: int, form: str = "10-K") -> dict:
    return {"end": end, "val": val, "fy": year, "fp": "FY", "form": form,
            "accn": "0001773383-25-000010", "filed": "2025-05-20"}


def _q(start: str, end: str, val: float, form: str = "10-Q") -> dict:
    return {"start": start, "end": end, "val": val, "form": form,
            "filed": "2025-05-01", "accn": "0001773383-25-000006"}


def _fetch_fy(provider, facts, year=2025):
    with patch.object(provider, "_get_facts", return_value=facts):
        return provider.fetch(
            "DT", "ebitda", "FY", year, fy_end_month=3, fy_end_day=31,
        )


# --- FY-Pfad ---------------------------------------------------------------


def test_fy_ebitda_uses_full_da_concept_directly(provider):
    """DepreciationDepletionAndAmortization enthaelt die Intangible-Amort
    per Definition -> direkt als D&A-Summand."""
    facts = _facts_with({
        "OperatingIncomeLoss": {"units": {"USD": [_fy("2025-03-31", 150e6, 2025)]}},
        "DepreciationDepletionAndAmortization": {
            "units": {"USD": [_fy("2025-03-31", 78e6, 2025)]}
        },
    })
    res = _fetch_fy(provider, facts)
    assert res is not None
    assert res.value == Decimal("228000000")  # 150 + 78 (DT FY2025-Ziel)
    assert "EBIT + D&A" in res.source_name


def test_fy_ebitda_depreciation_plus_separate_intangible_amort(provider):
    """Nur Depreciation-only + separat getaggte AmortizationOfIntangible-
    Assets -> beide werden addiert (sonst fehlten die Intangibles)."""
    facts = _facts_with({
        "OperatingIncomeLoss": {"units": {"USD": [_fy("2025-03-31", 150e6, 2025)]}},
        "Depreciation": {"units": {"USD": [_fy("2025-03-31", 49.1e6, 2025)]}},
        "AmortizationOfIntangibleAssets": {
            "units": {"USD": [_fy("2025-03-31", 28.9e6, 2025)]}
        },
    })
    res = _fetch_fy(provider, facts)
    assert res is not None
    assert res.value == Decimal("228000000")  # 150 + 49.1 + 28.9


def test_fy_ebitda_empty_when_only_depreciation_no_intangible(provider):
    """Nur Depreciation ohne Amortisations-Tag: der Wert waere zu niedrig
    (Intangible-Amort fehlt) -> lieber LEER als falsch."""
    facts = _facts_with({
        "OperatingIncomeLoss": {"units": {"USD": [_fy("2025-03-31", 150e6, 2025)]}},
        "Depreciation": {"units": {"USD": [_fy("2025-03-31", 49.1e6, 2025)]}},
    })
    assert _fetch_fy(provider, facts) is None


def test_fy_ebitda_empty_without_gaap_operating_income(provider):
    """Kein GAAP OperatingIncomeLoss getaggt (nur eine andere OI-Kennzahl):
    EBITDA wird NICHT aus einem Ersatz-/Non-GAAP-OI gebildet -> leer."""
    facts = _facts_with({
        "OperatingIncomeLossAdjusted": {
            "units": {"USD": [_fy("2025-03-31", 300e6, 2025)]}
        },
        "DepreciationDepletionAndAmortization": {
            "units": {"USD": [_fy("2025-03-31", 78e6, 2025)]}
        },
    })
    assert _fetch_fy(provider, facts) is None


def test_fy_ebitda_empty_when_da_missing(provider):
    """Kein D&A-Konzept in XBRL: keine EBIT-only-Approximation mehr -> leer."""
    facts = _facts_with({
        "OperatingIncomeLoss": {"units": {"USD": [_fy("2025-03-31", 150e6, 2025)]}},
    })
    assert _fetch_fy(provider, facts) is None


# --- Quartals-Pfad ---------------------------------------------------------


def _fetch_q1(provider, facts):
    # FY-Ende 31.03. -> Q1-Ende 30.06. (months_back = 9).
    with patch.object(provider, "_get_facts", return_value=facts):
        return provider.fetch_quarterly(
            "DT", "ebitda", 2025, "Q1", fy_end_month=3, fy_end_day=31,
        )


def test_q_ebitda_full_da_standalone(provider):
    facts = _facts_with({
        "OperatingIncomeLoss": {
            "units": {"USD": [_q("2024-04-01", "2024-06-30", 40e6)]}
        },
        "DepreciationDepletionAndAmortization": {
            "units": {"USD": [_q("2024-04-01", "2024-06-30", 20e6)]}
        },
    })
    res = _fetch_q1(provider, facts)
    assert res is not None
    assert res.value == Decimal("60000000")
    assert "EBIT + D&A" in res.source_name


def test_q_ebitda_depreciation_plus_intangible_standalone(provider):
    facts = _facts_with({
        "OperatingIncomeLoss": {
            "units": {"USD": [_q("2024-04-01", "2024-06-30", 40e6)]}
        },
        "Depreciation": {
            "units": {"USD": [_q("2024-04-01", "2024-06-30", 12e6)]}
        },
        "AmortizationOfIntangibleAssets": {
            "units": {"USD": [_q("2024-04-01", "2024-06-30", 8e6)]}
        },
    })
    res = _fetch_q1(provider, facts)
    assert res is not None
    assert res.value == Decimal("60000000")  # 40 + 12 + 8


def test_q_ebitda_empty_when_only_depreciation(provider):
    facts = _facts_with({
        "OperatingIncomeLoss": {
            "units": {"USD": [_q("2024-04-01", "2024-06-30", 40e6)]}
        },
        "Depreciation": {
            "units": {"USD": [_q("2024-04-01", "2024-06-30", 12e6)]}
        },
    })
    assert _fetch_q1(provider, facts) is None
