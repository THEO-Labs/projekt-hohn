"""Geschaeftsjahr-bewusstes laufendes FY (Dynatrace/Intuit-Bug Aug 2026)."""
from datetime import date
from types import SimpleNamespace

from app.values.provider_anchor import running_fy_year


def _co(m, d):
    return SimpleNamespace(fiscal_year_end_month=m, fiscal_year_end_day=d, ticker="X")


def test_calendar_year_company_unchanged():
    # Dez-Ende: FY2026 endet 31.12.2026 -> am 16.08.2026 laeuft FY2026.
    assert running_fy_year(_co(12, 31), date(2026, 8, 16)) == 2026


def test_march_fiscal_year_rolls_to_next():
    # Dynatrace Maerz: FY2026 endete 31.03.2026 -> laufend ist FY2027.
    assert running_fy_year(_co(3, 31), date(2026, 8, 16)) == 2027


def test_july_fiscal_year_rolls_after_end():
    # Intuit Juli: am 16.08.2026 ist FY2026 (31.07.) vorbei -> FY2027.
    assert running_fy_year(_co(7, 31), date(2026, 8, 16)) == 2027


def test_july_fiscal_year_before_end_stays():
    # Am 15.07.2026 laeuft FY2026 noch.
    assert running_fy_year(_co(7, 31), date(2026, 7, 15)) == 2026


def test_september_fiscal_year_running():
    # Apple Sep: am 16.08.2026 laeuft FY2026 (endet 30.09.2026).
    assert running_fy_year(_co(9, 30), date(2026, 8, 16)) == 2026


def test_no_fiscal_year_end_falls_back_to_calendar():
    assert running_fy_year(_co(None, None), date(2026, 8, 16)) == 2026
