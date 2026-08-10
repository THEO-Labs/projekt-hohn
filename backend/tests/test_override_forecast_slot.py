"""Slot-Wahl des manuellen Overrides (override_company_value).

Ein Override auf eine noch NICHT berichtete Periode ist eine Schaetzung
und gehoert in den FORECAST-Slot — nicht als manueller ACTUAL auf die
not_found-Platzhalterzeile (falsche Farbe, Hard-Lock gegen Anker/Bruecke).
Berichtet-Kriterium wie im Rest der Pipeline: Karenzfrist abgelaufen ODER
(US-Filer) Item-2.02-8-K nach Periodenende. Submissions sind gemockt.
"""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.companies.models import Company
from app.providers.base import ProviderResult
from app.values.models import CompanyValue
from tests.test_values import _login_with_company, _seed_catalog

CURRENT_YEAR = date.today().year


def _slot_rows(db, cid, key="net_income", year=CURRENT_YEAR):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == UUID(cid),
            CompanyValue.value_key == key,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == year,
        )
        .order_by(CompanyValue.is_forecast.asc())
        .all()
    )


def _seed_row(db, cid, value, is_forecast=False, year=CURRENT_YEAR, **kw):
    row = CompanyValue(
        company_id=UUID(cid), value_key="net_income", period_type="FY",
        period_year=year, numeric_value=value, is_forecast=is_forecast,
        currency="USD", **kw,
    )
    db.add(row)
    db.commit()
    return row


def _set_fy_end(db, cid, fy_end):
    """FY-Ende der Company direkt setzen (detect_fiscal_year_end ist in
    Tests gemockt und ueberschreibt das nicht)."""
    comp = db.get(Company, UUID(cid))
    comp.fiscal_year_end_month = fy_end.month
    comp.fiscal_year_end_day = fy_end.day
    db.commit()
    return comp


def _override(client, cid, value, year=CURRENT_YEAR, variant=None):
    body = {"numeric_value": value}
    if variant:
        body["variant"] = variant
    return client.post(
        f"/api/companies/{cid}/values/net_income/override"
        f"?period_type=FY&period_year={year}",
        json=body,
    )


def test_override_unreported_period_creates_forecast_row(client, db):
    """Kein Slot vorhanden, Periode laeuft noch (kein 8-K-Check noetig,
    Nicht-US): Override legt eine FORECAST-Zeile an."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)

    response = _override(client, cid, "100")
    assert response.status_code == 200
    data = response.json()
    assert data["is_forecast"] is True
    assert data["manually_overridden"] is True

    rows = _slot_rows(db, cid)
    assert len(rows) == 1
    assert rows[0].is_forecast is True
    assert rows[0].manually_overridden is True
    assert rows[0].primary_method == "manual"
    assert rows[0].numeric_value == Decimal("100")


def test_override_unreported_flips_not_found_placeholder(client, db):
    """Nur die leere Actual-Platzhalterzeile (not_found) existiert: sie wird
    auf is_forecast=True geflippt statt als manueller Actual gesperrt."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)
    placeholder = _seed_row(
        db, cid, None, source_name="Two-Stage not_found",
        primary_method="two_stage_verified",
    )

    response = _override(client, cid, "100")
    assert response.status_code == 200

    rows = _slot_rows(db, cid)
    assert len(rows) == 1
    assert rows[0].id == placeholder.id
    assert rows[0].is_forecast is True
    assert rows[0].manually_overridden is True
    assert rows[0].primary_method == "manual"
    assert rows[0].numeric_value == Decimal("100")


def test_override_unreported_prefers_forecast_and_deletes_placeholder(client, db):
    """Forecast-Zeile UND leere Actual-Platzhalterzeile existieren: Override
    schreibt in den Forecast-Slot, der Platzhalter wird geloescht
    (Unique-Index erlaubt kein zweites Forecast-Flip)."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)
    forecast = _seed_row(
        db, cid, Decimal("90"), is_forecast=True, primary_method="web_guidance",
    )
    _seed_row(db, cid, None, source_name="not_found")

    response = _override(client, cid, "100")
    assert response.status_code == 200

    rows = _slot_rows(db, cid)
    assert len(rows) == 1
    assert rows[0].id == forecast.id
    assert rows[0].is_forecast is True
    assert rows[0].manually_overridden is True
    assert rows[0].numeric_value == Decimal("100")


def test_override_reported_via_8k_targets_actual_slot(client, db):
    """US-Filer, Periode beendet, Karenz laeuft noch, aber ein Item-2.02-8-K
    existiert: Verhalten wie bisher — Actual-Slot (amber)."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)
    fy_end = date.today() - timedelta(days=10)
    comp = _set_fy_end(db, cid, fy_end)
    comp.isin = "US0378331005"
    db.commit()

    with patch(
        "app.values.gaap_bridge.has_reported_8k", return_value=True
    ) as mock_8k:
        response = _override(client, cid, "100", year=fy_end.year)
    assert response.status_code == 200
    mock_8k.assert_called_once()

    rows = _slot_rows(db, cid, year=fy_end.year)
    assert len(rows) == 1
    assert rows[0].is_forecast is False
    assert rows[0].manually_overridden is True
    assert rows[0].primary_method == "manual"


def test_override_us_within_grace_no_8k_targets_forecast(client, db):
    """US-Filer, Periode beendet, Karenz laeuft, kein 8-K (CIK-Aufloesung
    liefert in Tests None -> has_reported_8k False, kein Netz): Override
    landet im Forecast-Slot."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)
    fy_end = date.today() - timedelta(days=10)
    comp = _set_fy_end(db, cid, fy_end)
    comp.isin = "US0378331005"
    db.commit()

    response = _override(client, cid, "100", year=fy_end.year)
    assert response.status_code == 200

    rows = _slot_rows(db, cid, year=fy_end.year)
    assert len(rows) == 1
    assert rows[0].is_forecast is True
    assert rows[0].manually_overridden is True


def test_override_grace_expired_targets_actual_slot(client, db):
    """Abgelaufene Karenz: Actual-Slot wie heute, der 8-K-Check laeuft gar
    nicht erst (kein Submissions-Fetch, wenn er nichts entscheidet)."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)

    with patch("app.values.gaap_bridge.has_reported_8k", MagicMock()) as mock_8k:
        response = _override(client, cid, "100", year=2024)
    assert response.status_code == 200
    mock_8k.assert_not_called()

    rows = _slot_rows(db, cid, year=2024)
    assert len(rows) == 1
    assert rows[0].is_forecast is False
    assert rows[0].manually_overridden is True
    assert rows[0].primary_method == "manual"


def test_anchor_replaces_manual_forecast_from_override(client, db):
    """Zusammenspiel mit dem Lock-Kontrakt: der Override auf die unberichtete
    Periode erzeugt eine manuelle FORECAST-Zeile — der XBRL-Anker darf sie
    spaeter durch berichtete Zahlen ersetzen (Flip auf Actual, Lock weg)."""
    from app.values.provider_anchor import anchor_fy_with_provider

    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)
    fy_end = date.today() - timedelta(days=10)
    comp = _set_fy_end(db, cid, fy_end)

    response = _override(client, cid, "100", year=fy_end.year)
    assert response.status_code == 200
    rows = _slot_rows(db, cid, year=fy_end.year)
    assert len(rows) == 1 and rows[0].is_forecast is True
    override_row_id = rows[0].id

    result = ProviderResult(
        value=Decimal("120"), source_name="EDGAR 10-K FY",
        source_link="https://sec.gov/x", currency="USD",
    )
    provider = MagicMock()
    provider.fetch.return_value = result
    provider.name = "MockProvider"
    with patch(
        "app.values.provider_anchor.get_providers", return_value=[provider]
    ):
        wrote = anchor_fy_with_provider(db, comp, "net_income", fy_end.year)
    db.commit()

    assert wrote is True
    rows = _slot_rows(db, cid, year=fy_end.year)
    assert len(rows) == 1
    assert rows[0].id == override_row_id
    assert rows[0].is_forecast is False
    assert rows[0].manually_overridden is False
    assert rows[0].primary_method == "provider"
    assert rows[0].numeric_value == Decimal("120")


def test_adjusted_override_unreported_lands_on_forecast_row(client, db):
    """variant=adjusted folgt derselben Slot-Wahl: auf der unberichteten
    Periode landet der Adjusted-Wert auf der Forecast-Zeile, der leere
    Actual-Platzhalter wird abgeraeumt."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)
    forecast = _seed_row(
        db, cid, Decimal("90"), is_forecast=True, primary_method="web_guidance",
    )
    _seed_row(db, cid, None, source_name="not_found")

    response = _override(client, cid, "95", variant="adjusted")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(forecast.id)
    assert data["is_forecast"] is True

    rows = _slot_rows(db, cid)
    assert len(rows) == 1
    assert rows[0].id == forecast.id
    assert rows[0].is_forecast is True
    assert rows[0].numeric_value == Decimal("90")
    assert rows[0].numeric_value_adjusted == Decimal("95")
    assert rows[0].adjustments_source == "Manual"
    # GAAP-Felder unangetastet: kein Manual-Lock durch den Adjusted-Pfad.
    assert rows[0].manually_overridden is False
