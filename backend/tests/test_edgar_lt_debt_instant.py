"""EDGAR lt_debt als Instant-Bilanz-Key: STRIKT LongTermDebtNoncurrent,
keine Fallback-Kaskade (LongTermDebt = Total inkl. Current Maturities —
der historische lt_debt-Fehler). Datums-Encoding wie bei cash: Stichtag
bzw. Stichtag+1 (Mitternachts-Encoding) via ±7d-Toleranz der
Instant-Suche. Hermetisch gegen Fixture-Facts."""

from decimal import Decimal
from unittest.mock import patch

import pytest

from app.providers.edgar import BALANCE_KEYS, CONCEPT_MAP, EdgarProvider


@pytest.fixture
def provider():
    p = EdgarProvider()
    p._ticker_to_cik = {"MSFT": "0000789019"}
    return p


def _facts_with(concepts: dict[str, list[dict]]) -> dict:
    return {
        "facts": {
            "us-gaap": {
                name: {"units": {"USD": entries}} for name, entries in concepts.items()
            }
        }
    }


def _instant(end: str, val: float, form: str = "10-Q",
             filed: str = "2025-01-01",
             accn: str = "0000789019-25-000002") -> dict:
    return {"end": end, "val": val, "form": form, "filed": filed, "accn": accn}


def _fetch_q(provider, facts, key, quarter, year=2025):
    with patch.object(provider, "_get_facts", return_value=facts):
        return provider.fetch_quarterly(
            "MSFT", key, year, quarter, fy_end_month=12, fy_end_day=31
        )


def test_lt_debt_is_strict_single_concept_balance_key():
    assert "lt_debt" in BALANCE_KEYS
    assert CONCEPT_MAP["lt_debt"] == ["LongTermDebtNoncurrent"]


def test_lt_debt_q2_from_instant(provider):
    facts = _facts_with({
        "LongTermDebtNoncurrent": [
            _instant("2025-06-30", 20e9, filed="2025-07-25"),
        ]
    })
    res = _fetch_q(provider, facts, "lt_debt", "Q2")
    assert res is not None
    assert res.value == Decimal("20e9")
    assert "Bilanz-Stichtag" in res.source_name


def test_lt_debt_q4_from_10k_instant(provider):
    facts = _facts_with({
        "LongTermDebtNoncurrent": [
            _instant("2025-12-31", 21e9, form="10-K", filed="2026-02-10"),
        ]
    })
    res = _fetch_q(provider, facts, "lt_debt", "Q4")
    assert res is not None
    assert res.value == Decimal("21e9")
    assert "10-K" in res.source_name


def test_lt_debt_instant_accepts_midnight_plus_one_encoding(provider):
    """Manche Filer encodieren den Instant als Stichtag+1 (Mitternacht) —
    die ±7d-Toleranz der Instant-Suche (wie bei cash) deckt das ab."""
    facts = _facts_with({
        "LongTermDebtNoncurrent": [
            _instant("2025-07-01", 20e9, filed="2025-07-25"),
        ]
    })
    res = _fetch_q(provider, facts, "lt_debt", "Q2")
    assert res is not None
    assert res.value == Decimal("20e9")


def test_lt_debt_no_fallback_to_total_debt_concept(provider):
    """Nur LongTermDebt (Total Carrying Value) getaggt: lieber leer als
    falsch — weder Quartals- noch FY-Pfad liefern einen Wert."""
    facts = _facts_with({
        "LongTermDebt": [
            _instant("2025-06-30", 25e9, filed="2025-07-25"),
            _instant("2025-12-31", 26e9, form="10-K", filed="2026-02-10"),
        ]
    })
    assert _fetch_q(provider, facts, "lt_debt", "Q2") is None
    with patch.object(provider, "_get_facts", return_value=facts):
        assert provider.fetch(
            "MSFT", "lt_debt", period_type="FY", period_year=2025,
            fy_end_month=12, fy_end_day=31,
        ) is None


def test_lt_debt_fy_fetch_uses_strict_concept(provider):
    """FY-Pfad (fetch): der 10-K-Instant von LongTermDebtNoncurrent
    liefert den Jahresendwert."""
    facts = _facts_with({
        "LongTermDebtNoncurrent": [
            _instant("2025-12-31", 21e9, form="10-K", filed="2026-02-10"),
        ]
    })
    with patch.object(provider, "_get_facts", return_value=facts):
        res = provider.fetch(
            "MSFT", "lt_debt", period_type="FY", period_year=2025,
            fy_end_month=12, fy_end_day=31,
        )
    assert res is not None
    assert res.value == Decimal("21e9")
