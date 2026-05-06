"""Pure-math tests for the FY estimate engine. Uses fakes for the DB-touching
helpers so we cover sign-flip, near-zero, YTD, balance-snapshot und FY-Fallback
ohne live Postgres."""
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.calculations import estimates
from app.calculations.estimates import (
    _NEAR_ZERO_FRACTION,
    BALANCE_KEYS,
    FLOW_KEYS,
    compute_estimate,
)


def _stub_value_at(values: dict[tuple[str, str, int], Decimal]):
    """values keyed by (key, period_type, period_year)."""
    def _impl(_db, _company_id, key, period_type, period_year):
        return values.get((key, period_type, period_year))
    return _impl


def _stub_period_basis(bases: dict[tuple[str, str, int], str]):
    def _impl(_db, _company_id, key, quarter, period_year):
        return bases.get((key, quarter, period_year))
    return _impl


@pytest.fixture
def cid():
    return uuid4()


def test_balance_snapshot_picks_latest_quarter(cid):
    values = {
        ("net_debt", "Q1", 2026): Decimal("10"),
        ("net_debt", "Q3", 2026): Decimal("30"),  # latest available
    }
    with patch.object(estimates, "_value_at", _stub_value_at(values)):
        r = compute_estimate(None, cid, "net_debt", 2026)
    assert r is not None
    assert r.method == "balance_snapshot"
    assert r.value == Decimal("30")
    assert r.quarters_used == ["Q3"]


def test_balance_no_quarter_falls_back_to_fy(cid):
    """Kein Q-Snapshot → estimate = FY[N-1]-Wert (no-growth-Annahme)."""
    values = {
        ("net_debt", "FY", 2025): Decimal("500"),
    }
    with patch.object(estimates, "_value_at", _stub_value_at(values)):
        r = compute_estimate(None, cid, "net_debt", 2026)
    assert r is not None
    assert r.method == "fy_fallback"
    assert r.value == Decimal("500")


def test_balance_no_data_at_all_returns_none(cid):
    with patch.object(estimates, "_value_at", _stub_value_at({})):
        r = compute_estimate(None, cid, "net_debt", 2026)
    assert r is None


def test_flow_factor_basic_yoy(cid):
    values = {
        ("net_income", "FY", 2025): Decimal("1000"),
        ("net_income", "Q1", 2025): Decimal("200"),
        ("net_income", "Q1", 2026): Decimal("220"),  # +10%
    }
    with patch.object(estimates, "_value_at", _stub_value_at(values)):
        with patch.object(estimates, "_period_basis", _stub_period_basis({})):
            r = compute_estimate(None, cid, "net_income", 2026)
    assert r is not None
    assert r.method == "flow_factor"
    assert r.factor == Decimal("1.1")
    assert r.value == Decimal("1100.0")


def test_flow_sign_flip_falls_back_to_fy(cid):
    """VW-FCF-Case: Q swing von negativ zu positiv → Faktor unzuverlaessig
    → Fallback FY[N-1]."""
    values = {
        ("fcf", "FY", 2025): Decimal("-10000"),
        ("fcf", "Q1", 2025): Decimal("-2600"),
        ("fcf", "Q1", 2026): Decimal("600"),
    }
    with patch.object(estimates, "_value_at", _stub_value_at(values)):
        with patch.object(estimates, "_period_basis", _stub_period_basis({})):
            r = compute_estimate(None, cid, "fcf", 2026)
    assert r is not None
    assert r.method == "fy_fallback"
    assert r.value == Decimal("-10000")


def test_flow_near_zero_denominator_falls_back_to_fy(cid):
    """Winziger prev-Q-Wert → Faktor instabil → FY-Fallback."""
    values = {
        ("net_income", "FY", 2025): Decimal("10000"),
        ("net_income", "Q1", 2025): Decimal("50"),  # 0.5 % der FY-Basis
        ("net_income", "Q1", 2026): Decimal("3000"),
    }
    with patch.object(estimates, "_value_at", _stub_value_at(values)):
        with patch.object(estimates, "_period_basis", _stub_period_basis({})):
            r = compute_estimate(None, cid, "net_income", 2026)
    assert r is not None
    assert r.method == "fy_fallback"


def test_flow_ytd_used_directly_no_summing(cid):
    """Wenn Q3 YTD-period_basis hat, enthaelt es schon Q1+Q2+Q3 — darf
    NICHT mit Q1+Q2 standalone summiert werden."""
    values = {
        ("net_income", "FY", 2025): Decimal("1000"),
        ("net_income", "Q1", 2025): Decimal("200"),
        ("net_income", "Q2", 2025): Decimal("210"),
        ("net_income", "Q3", 2025): Decimal("650"),  # YTD
        ("net_income", "Q1", 2026): Decimal("220"),
        ("net_income", "Q2", 2026): Decimal("230"),
        ("net_income", "Q3", 2026): Decimal("700"),  # YTD
    }
    bases = {
        ("net_income", "Q1", 2025): "Q1_standalone",
        ("net_income", "Q2", 2025): "Q2_standalone",
        ("net_income", "Q3", 2025): "Q3_YTD",
        ("net_income", "Q1", 2026): "Q1_standalone",
        ("net_income", "Q2", 2026): "Q2_standalone",
        ("net_income", "Q3", 2026): "Q3_YTD",
    }
    with patch.object(estimates, "_value_at", _stub_value_at(values)):
        with patch.object(estimates, "_period_basis", _stub_period_basis(bases)):
            r = compute_estimate(None, cid, "net_income", 2026)
    assert r is not None
    assert r.method == "flow_factor"
    # factor = 700/650, NICHT (220+230+700)/(200+210+650)
    assert r.factor == Decimal("700") / Decimal("650")


def test_flow_missing_prev_year_q_falls_back_to_fy(cid):
    """Q vorhanden fuer target_fy aber nicht fuer prev_fy → FY-Fallback."""
    values = {
        ("net_income", "FY", 2025): Decimal("1000"),
        ("net_income", "Q1", 2026): Decimal("220"),
    }
    with patch.object(estimates, "_value_at", _stub_value_at(values)):
        with patch.object(estimates, "_period_basis", _stub_period_basis({})):
            r = compute_estimate(None, cid, "net_income", 2026)
    assert r is not None
    assert r.method == "fy_fallback"
    assert r.value == Decimal("1000")


def test_flow_no_q_no_fy_returns_none(cid):
    """Kein Q UND keine FY-Vorjahres-Basis → kein Estimate moeglich."""
    values = {
        ("net_income", "Q1", 2025): Decimal("200"),
        ("net_income", "Q1", 2026): Decimal("220"),
        # FY 2025 fehlt → kein Skalierungsanker
    }
    with patch.object(estimates, "_value_at", _stub_value_at(values)):
        with patch.object(estimates, "_period_basis", _stub_period_basis({})):
            r = compute_estimate(None, cid, "net_income", 2026)
    assert r is None


def test_flow_no_quarterlies_falls_back_to_fy(cid):
    """User-Wunsch: wenn KEINE Q-Berichte hochgeladen → estimate = FY[N-1]."""
    values = {
        ("net_income", "FY", 2025): Decimal("1000"),
    }
    with patch.object(estimates, "_value_at", _stub_value_at(values)):
        with patch.object(estimates, "_period_basis", _stub_period_basis({})):
            r = compute_estimate(None, cid, "net_income", 2026)
    assert r is not None
    assert r.method == "fy_fallback"
    assert r.value == Decimal("1000")


def test_unknown_key_returns_none(cid):
    with patch.object(estimates, "_value_at", _stub_value_at({})):
        r = compute_estimate(None, cid, "some_random_key", 2026)
    assert r is None


def test_estimable_keys_partition():
    """Sanity: FLOW und BALANCE sind disjoint, Union ist ESTIMABLE."""
    assert FLOW_KEYS.isdisjoint(BALANCE_KEYS)
    assert FLOW_KEYS | BALANCE_KEYS == set(estimates.ESTIMABLE_KEYS)
    assert _NEAR_ZERO_FRACTION > 0


def test_q4_not_in_quarters():
    """Q4-Daten sind im Annual Report enthalten, kein eigener Slot."""
    assert "Q4" not in estimates.QUARTERS
    assert estimates.QUARTERS == ("Q1", "Q2", "Q3")
