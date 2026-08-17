"""Tests fuer den Quartals-XBRL-Anker (anchor_quarters_with_provider).

Provider werden gemockt — kein Netz. Der Anker ueberschreibt Two-Stage-
Quartalsschaetzungen mit exakten EDGAR-Werten, respektiert Manual-
Override/PDF-Locks und haelt die Schreibpfad-Invarianten des FY-Ankers
ein (Sign-Normalisierung, Currency-Konflikt-Block, Slot-Handling).
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.providers.base import ProviderResult
from app.values.models import CompanyValue
from app.values.provider_anchor import anchor_quarters_with_provider

YEAR = date.today().year - 1


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="qanchor@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.flush()
    portfolio = Portfolio(name="P", owner_user_id=user.id)
    db.add(portfolio)
    db.flush()
    # US-ISIN: der Anker gated auf US-Filer (EDGAR-Quartalsquelle).
    comp = Company(
        portfolio_id=portfolio.id, name="TestCo", ticker="TST",
        currency="USD", isin="US0001234567",
        fiscal_year_end_month=12, fiscal_year_end_day=31,
    )
    db.add(comp)
    db.commit()
    return comp


class _QProvider:
    """Mock mit fetch_quarterly-Signatur des EdgarProviders. results mappt
    (key, year, quarter) -> ProviderResult; fehlende Zellen liefern None
    (= Quartal nicht gefilt)."""
    name = "MockEdgarQ"

    def __init__(self, results=None):
        self.results = results or {}
        self.calls: list[tuple] = []

    def fetch_quarterly(self, ticker, key, period_year, quarter,
                        fy_end_month=None, fy_end_day=None):
        self.calls.append((key, period_year, quarter))
        return self.results.get((key, period_year, quarter))


def _seed_q_row(db, comp, key, quarter, year, value, **kw):
    row = CompanyValue(
        company_id=comp.id, value_key=key, period_type=quarter, period_year=year,
        numeric_value=value, source_name="Two-Stage two_stage_verified",
        primary_method="two_stage_verified", currency=kw.pop("currency", "USD"), **kw,
    )
    db.add(row)
    db.commit()
    return row


def _q_rows(db, comp, key, quarter, year):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == quarter,
            CompanyValue.period_year == year,
        )
        .all()
    )


def _run(db, comp, results, years=None):
    provider = _QProvider(results)
    with patch("app.values.provider_anchor.get_providers", return_value=[provider]):
        written = anchor_quarters_with_provider(db, comp, years or [YEAR])
    db.commit()
    return written, provider


def test_two_stage_quarter_row_overwritten(db, company):
    """Two-Stage-Quartalszeile weicht dem exakten XBRL-Wert: Wert ersetzt,
    primary_method 'provider', consistency_flags geloescht."""
    _seed_q_row(db, company, "net_income", "Q2", YEAR, Decimal("100"),
                consistency_flags="qsum_mismatch")
    result = ProviderResult(
        value=Decimal("120"), source_name="SEC EDGAR 10-Q (Q2)",
        source_link="https://sec.gov/q2", currency="USD",
    )
    written, _ = _run(db, company, {("net_income", YEAR, "Q2"): result})

    assert written == 1
    (row,) = _q_rows(db, company, "net_income", "Q2", YEAR)
    assert row.numeric_value == Decimal("120")
    assert row.primary_method == "provider"
    assert row.source_name == "SEC EDGAR 10-Q (Q2)"
    assert row.source_link == "https://sec.gov/q2"
    assert row.is_forecast is False
    assert row.consistency_flags is None
    assert row.fetched_at is not None
    assert row.last_refresh_attempt is not None


def test_manually_overridden_row_untouched(db, company):
    """Manual-Override auf einer ACTUAL-Zeile ist Hard-Lock — die Zelle
    wird gar nicht erst gefetcht."""
    _seed_q_row(db, company, "net_income", "Q2", YEAR, Decimal("100"),
                manually_overridden=True)
    result = ProviderResult(value=Decimal("120"), source_name="EDGAR", currency="USD")
    written, provider = _run(db, company, {("net_income", YEAR, "Q2"): result})

    assert written == 0
    assert ("net_income", YEAR, "Q2") not in provider.calls
    (row,) = _q_rows(db, company, "net_income", "Q2", YEAR)
    assert row.numeric_value == Decimal("100")
    assert row.manually_overridden is True
    assert row.primary_method == "two_stage_verified"


def test_manual_forecast_quarter_replaced_by_anchor(db, company):
    """Lock-Kontrakt: manuelle FORECAST-Zeile weicht dem berichteten
    XBRL-Wert — Umzug auf Actual, Lock-Flag weg, Manual-Adjusted
    (Schaetz-Ableger) geleert."""
    seeded = _seed_q_row(
        db, company, "net_income", "Q2", YEAR, Decimal("100"),
        is_forecast=True, manually_overridden=True,
        numeric_value_adjusted=Decimal("110"),
        adjustments_note="Manuell geschaetzt",
        adjustments_source="Manual",
    )
    result = ProviderResult(
        value=Decimal("120"), source_name="SEC EDGAR 10-Q (Q2)",
        source_link="https://sec.gov/q2", currency="USD",
    )
    written, _ = _run(db, company, {("net_income", YEAR, "Q2"): result})

    assert written == 1
    rows = _q_rows(db, company, "net_income", "Q2", YEAR)
    assert len(rows) == 1
    row = rows[0]
    assert row.id == seeded.id
    assert row.numeric_value == Decimal("120")
    assert row.primary_method == "provider"
    assert row.is_forecast is False
    assert row.manually_overridden is False
    assert row.numeric_value_adjusted is None
    assert row.adjustments_note is None
    assert row.adjustments_source is None


def test_manual_forecast_untouched_when_actual_slot_exists(db, company):
    """Actual+Manual-Forecast-Paar: der Anker schreibt in den actual-Slot,
    die manuelle Forecast-Zeile bleibt stehen (Slot-Wahl bevorzugt actual)."""
    manual_fc = _seed_q_row(db, company, "net_income", "Q2", YEAR, Decimal("50"),
                            is_forecast=True, manually_overridden=True)
    actual = _seed_q_row(db, company, "net_income", "Q2", YEAR, Decimal("100"))
    result = ProviderResult(value=Decimal("120"), source_name="EDGAR", currency="USD")
    written, _ = _run(db, company, {("net_income", YEAR, "Q2"): result})

    assert written == 1
    db.refresh(actual)
    db.refresh(manual_fc)
    assert actual.numeric_value == Decimal("120")
    assert actual.primary_method == "provider"
    assert manual_fc.numeric_value == Decimal("50")
    assert manual_fc.is_forecast is True
    assert manual_fc.manually_overridden is True


def test_filled_pdf_row_untouched(db, company):
    _seed_q_row(db, company, "net_income", "Q1", YEAR, Decimal("100"), from_ir_pdf=True)
    result = ProviderResult(value=Decimal("120"), source_name="EDGAR", currency="USD")
    written, provider = _run(db, company, {("net_income", YEAR, "Q1"): result})

    assert written == 0
    assert ("net_income", YEAR, "Q1") not in provider.calls
    (row,) = _q_rows(db, company, "net_income", "Q1", YEAR)
    assert row.numeric_value == Decimal("100")


def test_provider_none_keeps_estimate(db, company):
    """fetch_quarterly -> None (Quartal nicht gefilt): Schaetzung bleibt."""
    _seed_q_row(db, company, "net_income", "Q3", YEAR, Decimal("55"),
                is_forecast=True)
    written, provider = _run(db, company, {})

    assert written == 0
    assert ("net_income", YEAR, "Q3") in provider.calls
    (row,) = _q_rows(db, company, "net_income", "Q3", YEAR)
    assert row.numeric_value == Decimal("55")
    assert row.is_forecast is True
    assert row.primary_method == "two_stage_verified"


def test_forecast_row_moved_to_actual(db, company):
    """Forecast-Zeile ohne actual-Slot wird auf is_forecast=False umgezogen —
    keine neue Zeile (Unique-Index-Slot bleibt sauber)."""
    seeded = _seed_q_row(db, company, "net_income", "Q3", YEAR, Decimal("55"),
                         is_forecast=True)
    result = ProviderResult(
        value=Decimal("60"), source_name="SEC EDGAR 10-Q (Q3)",
        source_link="https://sec.gov/q3", currency="USD",
    )
    written, _ = _run(db, company, {("net_income", YEAR, "Q3"): result})

    assert written == 1
    rows = _q_rows(db, company, "net_income", "Q3", YEAR)
    assert len(rows) == 1
    row = rows[0]
    assert row.id == seeded.id
    assert row.is_forecast is False
    assert row.numeric_value == Decimal("60")
    assert row.primary_method == "provider"


def test_actual_slot_preferred_over_forecast(db, company):
    """Actual+Forecast-Paar: der Anker schreibt in den actual-Slot, die
    Forecast-Zeile bleibt unangetastet."""
    forecast = _seed_q_row(db, company, "net_income", "Q2", YEAR, Decimal("50"),
                           is_forecast=True)
    actual = _seed_q_row(db, company, "net_income", "Q2", YEAR, Decimal("100"))
    result = ProviderResult(value=Decimal("120"), source_name="EDGAR", currency="USD")
    written, _ = _run(db, company, {("net_income", YEAR, "Q2"): result})

    assert written == 1
    db.refresh(actual)
    db.refresh(forecast)
    assert actual.numeric_value == Decimal("120")
    assert actual.primary_method == "provider"
    assert forecast.numeric_value == Decimal("50")
    assert forecast.is_forecast is True


def test_currency_conflict_skips_and_stamps_attempt(db, company):
    row = _seed_q_row(db, company, "net_income", "Q2", YEAR, Decimal("100"),
                      currency="EUR")
    result = ProviderResult(value=Decimal("120"), source_name="EDGAR", currency="USD")
    written, _ = _run(db, company, {("net_income", YEAR, "Q2"): result})

    assert written == 0
    db.refresh(row)
    assert row.numeric_value == Decimal("100")
    assert row.currency == "EUR"
    assert row.primary_method == "two_stage_verified"
    assert row.last_refresh_attempt is not None


def test_creates_row_and_normalizes_sign(db, company):
    """Fehlende Zelle wird angelegt; normalize_sign greift (sbc ist
    ALWAYS_POSITIVE — negativer XBRL-Diff wird positiv gespeichert)."""
    result = ProviderResult(
        value=Decimal("-50"), source_name="SEC EDGAR 10-Q (Q1)",
        source_link="https://sec.gov/q1", currency="USD",
    )
    written, _ = _run(db, company, {("sbc", YEAR, "Q1"): result})

    assert written == 1
    (row,) = _q_rows(db, company, "sbc", "Q1", YEAR)
    assert row.numeric_value == Decimal("50")
    assert row.primary_method == "provider"
    assert row.is_forecast is False


def test_anchor_clears_unprotected_adjusted_but_keeps_protected(db, company):
    """Stale-Schutz differenziert (adjusted_is_protected): unbelegte
    Adjusted-Werte UND Two-Stage-Sources werden abgeraeumt (Enrichment
    fuellt nach), nur Manual und 8-K-Enrichment (SEC-URL) ueberleben."""
    unsourced = _seed_q_row(db, company, "net_income", "Q1", YEAR, Decimal("100"),
                            numeric_value_adjusted=Decimal("120"),
                            adjustments_note="LLM-Altwert ohne Quelle")
    sourced = _seed_q_row(db, company, "net_income", "Q2", YEAR, Decimal("100"),
                          numeric_value_adjusted=Decimal("130"),
                          adjustments_note="Non-GAAP (Reconciliation 8-K): SBC",
                          adjustments_source="https://www.sec.gov/Archives/ex991.htm")
    two_stage = _seed_q_row(db, company, "net_income", "Q3", YEAR, Decimal("100"),
                            numeric_value_adjusted=Decimal("140"),
                            adjustments_note="Excludes SBC",
                            adjustments_source="Adjusted net income was... | https://ir.example/pr")
    manual = _seed_q_row(db, company, "net_income", "Q4", YEAR, Decimal("100"),
                         numeric_value_adjusted=Decimal("150"),
                         adjustments_note="Manuell ueberschrieben",
                         adjustments_source="Manual")
    results = {
        ("net_income", YEAR, q): ProviderResult(
            value=Decimal("110") + i, source_name="EDGAR", currency="USD")
        for i, q in enumerate(("Q1", "Q2", "Q3", "Q4"))
    }
    written, _ = _run(db, company, results)

    assert written == 4
    db.refresh(unsourced)
    db.refresh(sourced)
    db.refresh(two_stage)
    db.refresh(manual)
    assert unsourced.numeric_value_adjusted is None
    assert unsourced.adjustments_note is None
    assert unsourced.adjustments_source is None
    # 8-K-Enrichment (www.sec.gov) bleibt stehen.
    assert sourced.numeric_value_adjusted == Decimal("130")
    assert sourced.adjustments_note == "Non-GAAP (Reconciliation 8-K): SBC"
    assert sourced.adjustments_source == "https://www.sec.gov/Archives/ex991.htm"
    # Two-Stage-Source (quote | url) wird abgeraeumt — sonst klebt der
    # stale LLM-Adjusted-Wert dauerhaft neben dem frischen GAAP-Wert.
    assert two_stage.numeric_value_adjusted is None
    assert two_stage.adjustments_note is None
    assert two_stage.adjustments_source is None
    # Manual bleibt stehen.
    assert manual.numeric_value_adjusted == Decimal("150")
    assert manual.adjustments_source == "Manual"


def test_non_us_company_skips_without_provider_call(db, company):
    company.isin = "DE0001234567"
    db.commit()
    provider = _QProvider({("net_income", YEAR, "Q1"): ProviderResult(
        value=Decimal("1"), source_name="EDGAR", currency="EUR")})
    with patch("app.values.provider_anchor.get_providers", return_value=[provider]):
        written = anchor_quarters_with_provider(db, company, [YEAR])
    db.commit()

    assert written == 0
    assert provider.calls == []


def test_excluded_keys_never_fetched(db, company):
    """st_debt/shares_outstanding sind ausgenommen (quartalsweise
    Teilkomponenten bzw. kein Quartalspfad). lt_debt ist seit dem strikten
    EDGAR-Instant-Pfad (nur LongTermDebtNoncurrent) zugelassen."""
    written, provider = _run(db, company, {})
    fetched_keys = {c[0] for c in provider.calls}
    assert written == 0
    assert fetched_keys.isdisjoint({"st_debt", "shares_outstanding"})
    assert "net_income" in fetched_keys
    assert "lt_debt" in fetched_keys

