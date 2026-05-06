"""Pure-math tests for the FY estimate engine. Uses fakes for the DB-touching
helpers so we cover the sign-flip, near-zero, YTD, and balance-snapshot
branches without needing a live Postgres."""
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


def _stub_q_value(values: dict[tuple[str, str, int], Decimal]):
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
    with patch.object(estimates, "_q_value", _stub_q_value(values)):
        r = compute_estimate(None, cid, "net_debt", 2026)
    assert r is not None
    assert r.method == "balance_snapshot"
    assert r.value == Decimal("30")
    assert r.quarters_used == ["Q3"]


def test_flow_factor_basic_yoy(cid):
    values = {
        ("net_income", "FY", 2025): Decimal("1000"),
        ("net_income", "Q1", 2025): Decimal("200"),
        ("net_income", "Q1", 2026): Decimal("220"),  # +10%
    }
    with patch.object(estimates, "_q_value", _stub_q_value(values)):
        with patch.object(estimates, "_period_basis", _stub_period_basis({})):
            r = compute_estimate(None, cid, "net_income", 2026)
    assert r is not None
    assert r.method == "flow_factor"
    assert r.factor == Decimal("1.1")
    assert r.value == Decimal("1100.0")


def test_flow_sign_flip_returns_none(cid):
    """VW FCF case: Q1 2025 negative, Q1 2026 positive -> bogus factor."""
    values = {
        ("fcf", "FY", 2025): Decimal("-10000"),
        ("fcf", "Q1", 2025): Decimal("-2600"),
        ("fcf", "Q1", 2026): Decimal("600"),
    }
    with patch.object(estimates, "_q_value", _stub_q_value(values)):
        with patch.object(estimates, "_period_basis", _stub_period_basis({})):
            r = compute_estimate(None, cid, "fcf", 2026)
    assert r is None  # caller must fall back to Claude-research


def test_flow_near_zero_denominator_returns_none(cid):
    """Tiny prev-period value blows up the factor — refuse to extrapolate."""
    values = {
        ("net_income", "FY", 2025): Decimal("10000"),
        ("net_income", "Q1", 2025): Decimal("50"),  # 0.5% of FY base, well below 1% threshold
        ("net_income", "Q1", 2026): Decimal("3000"),
    }
    with patch.object(estimates, "_q_value", _stub_q_value(values)):
        with patch.object(estimates, "_period_basis", _stub_period_basis({})):
            r = compute_estimate(None, cid, "net_income", 2026)
    assert r is None


def test_flow_ytd_used_directly_no_summing(cid):
    """If Q3 carries period_basis YTD, it already contains Q1+Q2+Q3 — must
    NOT be summed with the standalone Q1/Q2 PDFs."""
    values = {
        ("net_income", "FY", 2025): Decimal("1000"),
        ("net_income", "Q1", 2025): Decimal("200"),
        ("net_income", "Q2", 2025): Decimal("210"),
        ("net_income", "Q3", 2025): Decimal("650"),  # YTD = 200 + 210 + 240
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
    with patch.object(estimates, "_q_value", _stub_q_value(values)):
        with patch.object(estimates, "_period_basis", _stub_period_basis(bases)):
            r = compute_estimate(None, cid, "net_income", 2026)
    assert r is not None
    # factor should be 700/650, NOT (220+230+700)/(200+210+650)
    assert r.factor == Decimal("700") / Decimal("650")
    assert r.components["used_ytd"] is True


def test_flow_missing_prev_year_q_returns_none(cid):
    """Need both target and prev quarter to compute factor."""
    values = {
        ("net_income", "FY", 2025): Decimal("1000"),
        # prev Q1 missing
        ("net_income", "Q1", 2026): Decimal("220"),
    }
    with patch.object(estimates, "_q_value", _stub_q_value(values)):
        with patch.object(estimates, "_period_basis", _stub_period_basis({})):
            r = compute_estimate(None, cid, "net_income", 2026)
    assert r is None


def test_flow_missing_fy_prev_returns_none(cid):
    values = {
        # FY 2025 missing
        ("net_income", "Q1", 2025): Decimal("200"),
        ("net_income", "Q1", 2026): Decimal("220"),
    }
    with patch.object(estimates, "_q_value", _stub_q_value(values)):
        with patch.object(estimates, "_period_basis", _stub_period_basis({})):
            r = compute_estimate(None, cid, "net_income", 2026)
    assert r is None


def test_unknown_key_returns_none(cid):
    with patch.object(estimates, "_q_value", _stub_q_value({})):
        r = compute_estimate(None, cid, "some_random_key", 2026)
    assert r is None


def test_estimable_keys_partition():
    """Sanity: FLOW and BALANCE are disjoint, union is ESTIMABLE."""
    assert FLOW_KEYS.isdisjoint(BALANCE_KEYS)
    assert FLOW_KEYS | BALANCE_KEYS == set(estimates.ESTIMABLE_KEYS)
    assert _NEAR_ZERO_FRACTION > 0
