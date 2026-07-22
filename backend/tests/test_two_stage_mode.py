"""Mode selection for the two-stage pipeline.

FY-level runs (quarter=None) must always use "historic" mode: the historic
extractor prompt contains the in-progress-fiscal-year protocol (reported
quarters as actuals, missing quarters as estimates, FY via guidance/consensus).
"current" mode is a single-standalone-quarter extraction and therefore only
valid when a specific quarter is requested. Previously choose_mode_for_year
returned "current" for the running year even without a quarter, which made
run_extractor raise ValueError for every current-year FY batch run.
"""

from datetime import date

from scripts.two_stage_research import choose_mode_for_year

TODAY = date(2026, 7, 22)


def test_past_year_is_historic():
    assert choose_mode_for_year(2025, today=TODAY) == "historic"


def test_current_year_without_quarter_is_historic():
    assert choose_mode_for_year(2026, today=TODAY) == "historic"


def test_future_year_without_quarter_is_historic():
    assert choose_mode_for_year(2027, today=TODAY) == "historic"


def test_current_year_with_quarter_is_current():
    assert choose_mode_for_year(2026, today=TODAY, quarter="Q2") == "current"


def test_past_year_with_quarter_stays_historic():
    assert choose_mode_for_year(2025, today=TODAY, quarter="Q2") == "historic"
