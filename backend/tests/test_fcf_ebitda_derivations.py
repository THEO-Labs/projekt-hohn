"""derive_missing_fcf (fcf = OCF - |Capex| als berechneter Wert) und
derive_ebitda_q4_from_fy (Q4 = FY - Q1 - Q2 - Q3 Restwert).

Lock-Konvention: not_found-Platzhalter und alte LLM-Werte (two_stage_*/
web_*) werden ersetzt, manual/PDF/provider-Zeilen mit Wert bleiben.
"""
from decimal import Decimal

import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.values.consistency import derive_ebitda_q4_from_fy, derive_missing_fcf
from app.values.models import CompanyValue

YEAR = 2026


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="fcfderive@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.flush()
    portfolio = Portfolio(name="P", owner_user_id=user.id)
    db.add(portfolio)
    db.flush()
    comp = Company(
        portfolio_id=portfolio.id, name="TestCo", ticker="TST",
        currency="USD", isin="US0001234567",
    )
    db.add(comp)
    db.commit()
    return comp


def _seed(db, comp, key, ptype, value, is_forecast=False, year=YEAR, **kw):
    row = CompanyValue(
        company_id=comp.id, value_key=key, period_type=ptype, period_year=year,
        numeric_value=Decimal(str(value)) if value is not None else None,
        is_forecast=is_forecast, source_name=kw.pop("source_name", "seed"),
        primary_method=kw.pop("primary_method", "provider"),
        currency=kw.pop("currency", "USD"), **kw,
    )
    db.add(row)
    db.commit()
    return row


def _rows(db, comp, key, ptype, year=YEAR):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == ptype,
            CompanyValue.period_year == year,
        )
        .order_by(CompanyValue.is_forecast.asc())
        .all()
    )


# --- derive_missing_fcf ------------------------------------------------------


def test_fcf_derived_for_fy_and_quarters(db, company):
    """GAAP-Fall: fcf = ocf - |capex| fuer jede Periode mit beiden Inputs;
    capex positiv gespeichert, Betrag wird trotzdem via abs() genommen."""
    _seed(db, company, "operating_cash_flow", "FY", 2700e6)
    _seed(db, company, "capex", "FY", 500e6)
    _seed(db, company, "operating_cash_flow", "Q1", 700e6)
    _seed(db, company, "capex", "Q1", 120e6)
    _seed(db, company, "operating_cash_flow", "Q2", 650e6)
    # Q2 ohne capex -> keine Ableitung.

    written = derive_missing_fcf(db, company.id, [YEAR])
    db.commit()

    assert written == 2
    (fy,) = _rows(db, company, "fcf", "FY")
    assert fy.numeric_value == Decimal("2200000000")
    assert fy.primary_method == "calculated"
    assert fy.is_forecast is False
    assert fy.source_name.startswith("Berechnet: OCF - Capex")
    assert fy.currency == "USD"
    (q1,) = _rows(db, company, "fcf", "Q1")
    assert q1.numeric_value == Decimal("580000000")
    assert _rows(db, company, "fcf", "Q2") == []


def test_fcf_negative_capex_input_abs(db, company):
    """Legacy-Zeilen mit negativem capex: die Ableitung rechnet mit dem
    Betrag."""
    _seed(db, company, "operating_cash_flow", "FY", 1000)
    _seed(db, company, "capex", "FY", -300)

    assert derive_missing_fcf(db, company.id, [YEAR]) == 1
    db.commit()
    (fy,) = _rows(db, company, "fcf", "FY")
    assert fy.numeric_value == Decimal("700")


def test_fcf_replaces_not_found_placeholder(db, company):
    """not_found-Platzhalter (rote Zelle) wird durch den berechneten Wert
    ersetzt — gleiche Zeile (Slot-Unique-Index), kein Duplikat."""
    nf = _seed(db, company, "fcf", "FY", None, primary_method="not_found",
               source_name="No source found (research attempted)")
    _seed(db, company, "operating_cash_flow", "FY", 1000)
    _seed(db, company, "capex", "FY", 300)

    written = derive_missing_fcf(db, company.id, [YEAR])
    db.commit()

    assert written == 1
    rows = _rows(db, company, "fcf", "FY")
    assert len(rows) == 1
    db.refresh(nf)
    assert nf.numeric_value == Decimal("700")
    assert nf.primary_method == "calculated"


def test_fcf_replaces_stale_two_stage_value(db, company):
    """Alte two_stage/web-Recherchewerte weichen der Berechnung."""
    old = _seed(db, company, "fcf", "FY", 999,
                primary_method="two_stage_confirmed")
    _seed(db, company, "operating_cash_flow", "FY", 1000)
    _seed(db, company, "capex", "FY", 300)

    assert derive_missing_fcf(db, company.id, [YEAR]) == 1
    db.commit()
    db.refresh(old)
    assert old.numeric_value == Decimal("700")
    assert old.primary_method == "calculated"


def test_fcf_manual_pdf_provider_rows_untouched(db, company):
    """manual (FY), PDF (Q1) und provider (Q2) fcf-Zeilen sind
    authoritative — keine Ableitung in diesen Perioden."""
    manual = _seed(db, company, "fcf", "FY", 111, primary_method="manual",
                   manually_overridden=True)
    pdf = _seed(db, company, "fcf", "Q1", 222, primary_method="pdf",
                from_ir_pdf=True)
    prov = _seed(db, company, "fcf", "Q2", 333, primary_method="provider")
    for pt in ("FY", "Q1", "Q2"):
        _seed(db, company, "operating_cash_flow", pt, 1000)
        _seed(db, company, "capex", pt, 300)

    assert derive_missing_fcf(db, company.id, [YEAR]) == 0
    db.commit()
    for row, expected in ((manual, "111"), (pdf, "222"), (prov, "333")):
        db.refresh(row)
        assert row.numeric_value == Decimal(expected)
        assert row.primary_method != "calculated"


def test_fcf_is_forecast_propagates(db, company):
    """Ein Forecast-Input macht die abgeleitete fcf-Zeile zum Forecast."""
    _seed(db, company, "operating_cash_flow", "FY", 1000)
    _seed(db, company, "capex", "FY", 300, is_forecast=True)

    assert derive_missing_fcf(db, company.id, [YEAR]) == 1
    db.commit()
    (fy,) = _rows(db, company, "fcf", "FY")
    assert fy.is_forecast is True
    assert fy.numeric_value == Decimal("700")


def test_fcf_adjusted_when_both_inputs_adjusted(db, company):
    """Adjusted-Spur: nur wenn OCF UND Capex adjusted-Werte haben."""
    _seed(db, company, "operating_cash_flow", "FY", 1000,
          numeric_value_adjusted=Decimal("1100"))
    _seed(db, company, "capex", "FY", 300,
          numeric_value_adjusted=Decimal("280"))
    # Q1: nur ein Input adjusted -> GAAP ja, adjusted nein.
    _seed(db, company, "operating_cash_flow", "Q1", 500,
          numeric_value_adjusted=Decimal("520"))
    _seed(db, company, "capex", "Q1", 100)

    assert derive_missing_fcf(db, company.id, [YEAR]) == 2
    db.commit()
    (fy,) = _rows(db, company, "fcf", "FY")
    assert fy.numeric_value == Decimal("700")
    assert fy.numeric_value_adjusted == Decimal("820")
    assert fy.adjustments_note == "Berechnet: OCF - Capex (adjusted)"
    assert fy.adjustments_source is None
    (q1,) = _rows(db, company, "fcf", "Q1")
    assert q1.numeric_value == Decimal("400")
    assert q1.numeric_value_adjusted is None


def test_fcf_protected_adjusted_not_overwritten(db, company):
    """Manuell gesetzte Adjusted-Felder einer ersetzbaren fcf-Zeile bleiben
    (adjusted_is_protected); die GAAP-Spur wird trotzdem berechnet."""
    old = _seed(db, company, "fcf", "FY", 999, primary_method="web_guidance",
                numeric_value_adjusted=Decimal("888"),
                adjustments_note="Manuell", adjustments_source="Manual")
    _seed(db, company, "operating_cash_flow", "FY", 1000,
          numeric_value_adjusted=Decimal("1100"))
    _seed(db, company, "capex", "FY", 300,
          numeric_value_adjusted=Decimal("280"))

    assert derive_missing_fcf(db, company.id, [YEAR]) == 1
    db.commit()
    db.refresh(old)
    assert old.numeric_value == Decimal("700")
    assert old.numeric_value_adjusted == Decimal("888")
    assert old.adjustments_source == "Manual"


# --- derive_ebitda_q4_from_fy ------------------------------------------------


def test_ebitda_q4_residual_happy_path(db, company):
    _seed(db, company, "ebitda", "FY", 460)
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "ebitda", q, v)

    written = derive_ebitda_q4_from_fy(db, company.id, YEAR)
    db.commit()

    assert written == 1
    (q4,) = _rows(db, company, "ebitda", "Q4")
    assert q4.numeric_value == Decimal("130")
    assert q4.primary_method == "calculated"
    assert q4.is_forecast is False
    assert q4.source_name.startswith("Berechnet: FY minus Q1-Q3")
    assert q4.currency == "USD"


def test_ebitda_q4_negative_residual_not_written(db, company):
    """FY unter der Q1-Q3-Summe: negativer Rest ist bei den Zielfirmen ein
    Datenfehler — keine Zeile, nur Warnung."""
    _seed(db, company, "ebitda", "FY", 300)
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "ebitda", q, v)

    assert derive_ebitda_q4_from_fy(db, company.id, YEAR) == 0
    assert _rows(db, company, "ebitda", "Q4") == []


def test_ebitda_q4_existing_value_stays(db, company):
    """Berichteter Q4 (provider) bleibt unangetastet."""
    _seed(db, company, "ebitda", "FY", 460)
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "ebitda", q, v)
    q4 = _seed(db, company, "ebitda", "Q4", 128)

    assert derive_ebitda_q4_from_fy(db, company.id, YEAR) == 0
    db.commit()
    db.refresh(q4)
    assert q4.numeric_value == Decimal("128")
    assert q4.primary_method == "provider"


def test_ebitda_q4_replaces_not_found_placeholder(db, company):
    _seed(db, company, "ebitda", "FY", 460)
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "ebitda", q, v)
    nf = _seed(db, company, "ebitda", "Q4", None, primary_method="not_found")

    assert derive_ebitda_q4_from_fy(db, company.id, YEAR) == 1
    db.commit()
    db.refresh(nf)
    assert nf.numeric_value == Decimal("130")
    assert nf.primary_method == "calculated"


def test_ebitda_q4_requires_actuals(db, company):
    """FY-Forecast oder fehlendes Quartal: keine Ableitung."""
    _seed(db, company, "ebitda", "FY", 460, is_forecast=True)
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "ebitda", q, v)
    assert derive_ebitda_q4_from_fy(db, company.id, YEAR) == 0

    # FY-Actual, aber Q3 fehlt.
    _seed(db, company, "ebitda", "FY", 460, year=YEAR - 1)
    for q, v in (("Q1", 100), ("Q2", 110)):
        _seed(db, company, "ebitda", q, v, year=YEAR - 1)
    assert derive_ebitda_q4_from_fy(db, company.id, YEAR - 1) == 0
