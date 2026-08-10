"""Tests fuer den XBRL-Provider-Anker (anchor_fy_with_provider).

Provider werden gemockt — kein Netz. Der Anker darf nur abgeschlossene
Geschaeftsjahre anfassen, respektiert Manual-Override/PDF-Zeilen und
haelt dieselben Schreibpfad-Invarianten ein wie der FY-Refresh
(Sign-Normalisierung, Currency-Konflikt-Block).
"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.providers.base import ProviderResult
from app.values.models import CompanyValue
from app.values.provider_anchor import anchor_fy_with_provider

CLOSED_YEAR = date.today().year - 1
RUNNING_YEAR = date.today().year


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="anchor@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.flush()
    portfolio = Portfolio(name="P", owner_user_id=user.id)
    db.add(portfolio)
    db.flush()
    comp = Company(
        portfolio_id=portfolio.id, name="TestCo", ticker="TST",
        currency="EUR", isin="DE0001234567",
    )
    db.add(comp)
    db.commit()
    return comp


def _seed_fy_row(db, comp, key, year, value, **kw):
    row = CompanyValue(
        company_id=comp.id, value_key=key, period_type="FY", period_year=year,
        numeric_value=value, source_name="Two-Stage two_stage_verified",
        primary_method="two_stage_verified", currency=kw.pop("currency", "EUR"), **kw,
    )
    db.add(row)
    db.commit()
    return row


def _mock_provider(result):
    p = MagicMock()
    p.fetch.return_value = result
    p.name = "MockProvider"
    return p


def _fy_row(db, comp, key, year):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == year,
        )
        .one()
    )


def test_provider_value_overwrites_fy_row(db, company):
    """(a) Provider liefert Decimal -> FY-Zeile wird mit primary_method
    'provider' ueberschrieben, LLM-Wert weicht dem XBRL-Anker."""
    _seed_fy_row(db, company, "net_income", CLOSED_YEAR, Decimal("100"))
    result = ProviderResult(
        value=Decimal("120"), source_name="EDGAR 10-K FY", source_link="https://sec.gov/x",
        currency="EUR",
    )
    with patch("app.values.provider_anchor.get_providers", return_value=[_mock_provider(result)]):
        wrote = anchor_fy_with_provider(db, company, "net_income", CLOSED_YEAR)
    db.commit()

    assert wrote is True
    row = _fy_row(db, company, "net_income", CLOSED_YEAR)
    assert row.numeric_value == Decimal("120")
    assert row.primary_method == "provider"
    assert row.source_name == "EDGAR 10-K FY"
    assert row.source_link == "https://sec.gov/x"
    assert row.is_forecast is False
    assert row.manually_overridden is False
    assert row.fetched_at is not None
    assert row.last_refresh_attempt is not None


def test_running_year_no_provider_call(db, company):
    """(b) Laufendes Jahr: kein Provider-Aufruf, kein Write."""
    row = _seed_fy_row(db, company, "net_income", RUNNING_YEAR, Decimal("100"))
    with patch("app.values.provider_anchor.get_providers") as gp:
        wrote = anchor_fy_with_provider(db, company, "net_income", RUNNING_YEAR)
    db.commit()

    assert wrote is False
    gp.assert_not_called()
    db.refresh(row)
    assert row.numeric_value == Decimal("100")
    assert row.primary_method == "two_stage_verified"


def test_fy_end_in_past_makes_current_calendar_year_closed(db, company):
    """FY-Ende aus den Stammdaten schlaegt die Kalenderjahr-Heuristik:
    liegt das FY-Ende bereits hinter heute, ist das Jahr anker-faehig."""
    past = date.today() - timedelta(days=40)
    company.fiscal_year_end_month = past.month
    company.fiscal_year_end_day = past.day
    db.commit()
    result = ProviderResult(value=Decimal("7"), source_name="EDGAR", currency="EUR")
    with patch("app.values.provider_anchor.get_providers", return_value=[_mock_provider(result)]):
        wrote = anchor_fy_with_provider(db, company, "net_income", past.year)
    assert wrote is True


def test_fy_end_in_future_blocks_anchor(db, company):
    future = date.today() + timedelta(days=40)
    company.fiscal_year_end_month = future.month
    company.fiscal_year_end_day = future.day
    db.commit()
    with patch("app.values.provider_anchor.get_providers") as gp:
        wrote = anchor_fy_with_provider(db, company, "net_income", future.year)
    assert wrote is False
    gp.assert_not_called()


def test_manually_overridden_row_untouched(db, company):
    """(c) Manual-Override ist Hard-Lock — Provider wird gar nicht erst gerufen."""
    _seed_fy_row(db, company, "net_income", CLOSED_YEAR, Decimal("100"),
                 manually_overridden=True)
    with patch("app.values.provider_anchor.get_providers") as gp:
        wrote = anchor_fy_with_provider(db, company, "net_income", CLOSED_YEAR)
    db.commit()

    assert wrote is False
    gp.assert_not_called()
    row = _fy_row(db, company, "net_income", CLOSED_YEAR)
    assert row.numeric_value == Decimal("100")
    assert row.manually_overridden is True


def test_anchor_replaces_manual_forecast_row(db, company):
    """Lock-Kontrakt: eine manuelle FORECAST-Zeile (Schaetz-Override) weicht
    berichteten XBRL-Zahlen — Flip auf Actual, Lock-Flag weg, Manual-
    Adjusted (Schaetz-Ableger) geleert."""
    seeded = _seed_fy_row(
        db, company, "net_income", CLOSED_YEAR, Decimal("100"),
        is_forecast=True, manually_overridden=True,
        numeric_value_adjusted=Decimal("110"),
        adjustments_note="Manuell geschaetzt",
        adjustments_source="Manual",
    )
    result = ProviderResult(
        value=Decimal("120"), source_name="EDGAR 10-K FY",
        source_link="https://sec.gov/x", currency="EUR",
    )
    with patch("app.values.provider_anchor.get_providers",
               return_value=[_mock_provider(result)]):
        wrote = anchor_fy_with_provider(db, company, "net_income", CLOSED_YEAR)
    db.commit()

    assert wrote is True
    row = _fy_row(db, company, "net_income", CLOSED_YEAR)
    assert row.id == seeded.id
    assert row.numeric_value == Decimal("120")
    assert row.primary_method == "provider"
    assert row.is_forecast is False
    assert row.manually_overridden is False
    assert row.numeric_value_adjusted is None
    assert row.adjustments_note is None
    assert row.adjustments_source is None


def test_manual_actual_row_with_manual_adjusted_stays_locked(db, company):
    """Gegenprobe: manuelle ACTUAL-Zeile bleibt komplett unangetastet —
    inkl. Manual-Adjusted, kein Provider-Call."""
    _seed_fy_row(
        db, company, "net_income", CLOSED_YEAR, Decimal("100"),
        manually_overridden=True,
        numeric_value_adjusted=Decimal("110"),
        adjustments_note="Manuell ueberschrieben",
        adjustments_source="Manual",
    )
    with patch("app.values.provider_anchor.get_providers") as gp:
        wrote = anchor_fy_with_provider(db, company, "net_income", CLOSED_YEAR)
    db.commit()

    assert wrote is False
    gp.assert_not_called()
    row = _fy_row(db, company, "net_income", CLOSED_YEAR)
    assert row.numeric_value == Decimal("100")
    assert row.manually_overridden is True
    assert row.is_forecast is False
    assert row.primary_method == "two_stage_verified"
    assert row.numeric_value_adjusted == Decimal("110")
    assert row.adjustments_note == "Manuell ueberschrieben"
    assert row.adjustments_source == "Manual"


def test_from_ir_pdf_row_untouched(db, company):
    _seed_fy_row(db, company, "net_income", CLOSED_YEAR, Decimal("100"), from_ir_pdf=True)
    with patch("app.values.provider_anchor.get_providers") as gp:
        wrote = anchor_fy_with_provider(db, company, "net_income", CLOSED_YEAR)
    assert wrote is False
    gp.assert_not_called()
    row = _fy_row(db, company, "net_income", CLOSED_YEAR)
    assert row.numeric_value == Decimal("100")


def test_provider_returns_none_nothing_happens(db, company):
    """(d) Kette liefert nichts -> Zeile bleibt unveraendert."""
    row = _seed_fy_row(db, company, "net_income", CLOSED_YEAR, Decimal("100"))
    with patch("app.values.provider_anchor.get_providers", return_value=[_mock_provider(None)]):
        wrote = anchor_fy_with_provider(db, company, "net_income", CLOSED_YEAR)
    db.commit()

    assert wrote is False
    db.refresh(row)
    assert row.numeric_value == Decimal("100")
    assert row.primary_method == "two_stage_verified"


def test_sign_normalization_applies(db, company):
    """(e) normalize_sign greift im Anker-Schreibpfad: negativer Wert eines
    ALWAYS_POSITIVE_KEYS (sbc) wird positiv gespeichert."""
    _seed_fy_row(db, company, "sbc", CLOSED_YEAR, Decimal("10"))
    result = ProviderResult(value=Decimal("-50"), source_name="EDGAR", currency="EUR")
    with patch("app.values.provider_anchor.get_providers", return_value=[_mock_provider(result)]):
        wrote = anchor_fy_with_provider(db, company, "sbc", CLOSED_YEAR)
    db.commit()

    assert wrote is True
    row = _fy_row(db, company, "sbc", CLOSED_YEAR)
    assert row.numeric_value == Decimal("50")


def test_capex_keeps_raw_sign(db, company):
    """capex ist bewusst NICHT in ALWAYS_POSITIVE_KEYS (sign_keys.py:
    'wir schreiben roh wie im Bericht') — der Anker aendert das Vorzeichen
    nicht. Die Positiv-Konvention fuer ESEF-capex liegt im Provider selbst
    (abs-Summe, siehe test_esef_provider)."""
    _seed_fy_row(db, company, "capex", CLOSED_YEAR, Decimal("10"))
    result = ProviderResult(value=Decimal("-50"), source_name="EDGAR", currency="EUR")
    with patch("app.values.provider_anchor.get_providers", return_value=[_mock_provider(result)]):
        wrote = anchor_fy_with_provider(db, company, "capex", CLOSED_YEAR)
    db.commit()

    assert wrote is True
    row = _fy_row(db, company, "capex", CLOSED_YEAR)
    assert row.numeric_value == Decimal("-50")


def test_currency_conflict_blocks_write_and_stamps_attempt(db, company):
    row = _seed_fy_row(db, company, "net_income", CLOSED_YEAR, Decimal("100"), currency="EUR")
    result = ProviderResult(value=Decimal("120"), source_name="EDGAR", currency="USD")
    with patch("app.values.provider_anchor.get_providers", return_value=[_mock_provider(result)]):
        wrote = anchor_fy_with_provider(db, company, "net_income", CLOSED_YEAR)
    db.commit()

    assert wrote is False
    db.refresh(row)
    assert row.numeric_value == Decimal("100")
    assert row.currency == "EUR"
    assert row.last_refresh_attempt is not None


def test_anchor_prefers_actual_row_over_forecast(db, company):
    """Koexistierendes Actual+Forecast-Paar: der Anker schreibt in die
    Actual-Zeile, die Alt-Forecast-Zeile bleibt unangetastet."""
    forecast = _seed_fy_row(db, company, "net_income", CLOSED_YEAR, Decimal("50"),
                            is_forecast=True)
    actual = _seed_fy_row(db, company, "net_income", CLOSED_YEAR, Decimal("100"))
    result = ProviderResult(value=Decimal("120"), source_name="ESEF FY", currency="EUR")
    with patch("app.values.provider_anchor.get_providers",
               return_value=[_mock_provider(result)]):
        wrote = anchor_fy_with_provider(db, company, "net_income", CLOSED_YEAR)
    db.commit()

    assert wrote is True
    db.refresh(actual)
    db.refresh(forecast)
    assert actual.numeric_value == Decimal("120")
    assert actual.primary_method == "provider"
    assert forecast.numeric_value == Decimal("50")
    assert forecast.primary_method == "two_stage_verified"
    assert forecast.is_forecast is True


def test_result_currency_none_keeps_existing_label(db, company):
    """Provider ohne Currency (z.B. Yahoo-Minimal): Wert wird geschrieben,
    das bestehende Currency-Label (Two-Stage-Write) bleibt erhalten."""
    _seed_fy_row(db, company, "net_income", CLOSED_YEAR, Decimal("100"), currency="EUR")
    result = ProviderResult(value=Decimal("120"), source_name="ESEF FY", currency=None)
    with patch("app.values.provider_anchor.get_providers",
               return_value=[_mock_provider(result)]):
        wrote = anchor_fy_with_provider(db, company, "net_income", CLOSED_YEAR)
    db.commit()

    assert wrote is True
    row = _fy_row(db, company, "net_income", CLOSED_YEAR)
    assert row.numeric_value == Decimal("120")
    assert row.currency == "EUR"


def test_new_row_currency_conflict_against_company_blocked(db, company):
    """Neuanlage ohne bestehende Zeile: weicht die Provider-Waehrung von der
    Company-Waehrung ab (Currency-Key), wird NICHT geschrieben."""
    result = ProviderResult(value=Decimal("120"), source_name="EDGAR", currency="USD")
    with patch("app.values.provider_anchor.get_providers",
               return_value=[_mock_provider(result)]):
        wrote = anchor_fy_with_provider(db, company, "net_income", CLOSED_YEAR)
    db.commit()

    assert wrote is False
    n = db.query(CompanyValue).filter(
        CompanyValue.company_id == company.id,
        CompanyValue.value_key == "net_income",
    ).count()
    assert n == 0


def test_creates_row_when_missing(db, company):
    """Keine FY-Zeile vorhanden (Two-Stage fand nichts): Anker legt sie an —
    genau der Fall 'fehlende Zeitreihe trotz maschinenlesbarem Filing'."""
    result = ProviderResult(value=Decimal("42"), source_name="ESEF FY", currency="EUR")
    with patch("app.values.provider_anchor.get_providers", return_value=[_mock_provider(result)]):
        wrote = anchor_fy_with_provider(db, company, "net_income", CLOSED_YEAR)
    db.commit()

    assert wrote is True
    row = _fy_row(db, company, "net_income", CLOSED_YEAR)
    assert row.numeric_value == Decimal("42")
    assert row.primary_method == "provider"


def test_kwargs_cascade_passes_isin_and_falls_back(db, company):
    """Kaskadierende fetch-Signaturen wie _try_providers: voller kwargs-Satz
    (isin) zuerst; Provider mit Minimal-Signatur wird trotzdem bedient."""
    seen_kwargs = {}

    class _FullSig:
        name = "Full"

        def fetch(self, ticker, key, period_type, period_year,
                  fy_end_month=None, fy_end_day=None, isin=None):
            seen_kwargs.update({"isin": isin, "ticker": ticker})
            return None

    class _MinimalSig:
        name = "Minimal"

        def fetch(self, ticker, key, period_type, period_year):
            return ProviderResult(value=Decimal("5"), source_name="Yahoo", currency="EUR")

    with patch("app.values.provider_anchor.get_providers",
               return_value=[_FullSig(), _MinimalSig()]):
        wrote = anchor_fy_with_provider(db, company, "net_income", CLOSED_YEAR)
    db.commit()

    assert wrote is True
    assert seen_kwargs == {"isin": "DE0001234567", "ticker": "TST"}
    row = _fy_row(db, company, "net_income", CLOSED_YEAR)
    assert row.numeric_value == Decimal("5")


def test_two_stage_caller_invokes_anchor_after_apply(db, company):
    """Integration: _process_one_key_via_two_stage ruft den Anker NACH dem
    Apply-Schritt auf — der XBRL-Wert schlaegt den LLM-FY-Wert."""
    from app.values.routes import _process_one_key_via_two_stage
    from tests.test_two_stage_apply import _result

    payload = SimpleNamespace(period_type="FY", period_year=CLOSED_YEAR)
    updated: list = []
    anchor_result = ProviderResult(
        value=Decimal("999"), source_name="EDGAR 10-K", currency="EUR",
    )
    with patch("scripts.two_stage_research.research_two_stage",
               return_value=_result("net_income", Decimal("100"))), \
         patch("app.values.provider_anchor.get_providers",
               return_value=[_mock_provider(anchor_result)]):
        wrote = _process_one_key_via_two_stage(
            db=db, key="net_income", company=company,
            company_id=company.id, payload=payload, updated=updated,
        )
    db.commit()

    assert wrote is True
    row = _fy_row(db, company, "net_income", CLOSED_YEAR)
    assert row.numeric_value == Decimal("999")
    assert row.primary_method == "provider"
    assert row in updated


def test_anchor_error_never_crashes_refresh(db, company):
    """Anker-Fehler werden geloggt, der Refresh laeuft weiter — das
    Two-Stage-Ergebnis bleibt stehen."""
    from app.values.routes import _process_one_key_via_two_stage
    from tests.test_two_stage_apply import _result

    payload = SimpleNamespace(period_type="FY", period_year=CLOSED_YEAR)
    updated: list = []
    with patch("scripts.two_stage_research.research_two_stage",
               return_value=_result("net_income", Decimal("100"))), \
         patch("app.values.provider_anchor.anchor_fy_with_provider",
               side_effect=RuntimeError("boom")):
        wrote = _process_one_key_via_two_stage(
            db=db, key="net_income", company=company,
            company_id=company.id, payload=payload, updated=updated,
        )
    db.commit()

    assert wrote is True
    row = _fy_row(db, company, "net_income", CLOSED_YEAR)
    assert row.numeric_value == Decimal("100")
