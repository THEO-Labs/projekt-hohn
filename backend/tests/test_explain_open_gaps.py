"""derive_explain_open_gaps: bewusst leere Schaetzzellen offener Quartale
bekommen einen not_estimated-Platzhalter mit ehrlicher Begruendung im
source_name (statt generischem "noch nicht berichtet" im UI). Nur wenn ein
FY-Forecast mit Wert existiert und die Zelle wirklich leer ist; berichtete
Perioden bleiben aussen vor (dort gilt not_found/rot). Echte Writer
ersetzen die Platzhalter regulaer."""
from decimal import Decimal

import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.values import consistency
from app.values.consistency import (
    _NOT_ESTIMATED_GENERIC,
    _NOT_ESTIMATED_TEXTS,
    _derivation_replaceable,
    derive_explain_open_gaps,
    derive_open_quarter_from_fy_estimate,
)
from app.values.models import CompanyValue
from app.values.persistence import NOT_FOUND_SOURCE
from app.values.statement_research import _row_replaceable

YEAR = 2026


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="gaps@example.com", password_hash=hash_password("pw1234"))
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


@pytest.fixture
def q4_open(monkeypatch):
    """Berichtet-Kriterium deterministisch: Q1-Q3 berichtet, Q4 offen —
    hermetisch, kein Netz und keine Abhaengigkeit vom heutigen Datum."""
    monkeypatch.setattr(
        consistency, "_quarter_reported",
        lambda company, year, q, cache: q in ("Q1", "Q2", "Q3"),
    )


def _seed(db, comp, key, ptype, value, is_forecast=False, year=YEAR, **kw):
    adjusted = kw.pop("adjusted", None)
    row = CompanyValue(
        company_id=comp.id, value_key=key, period_type=ptype, period_year=year,
        numeric_value=Decimal(str(value)) if value is not None else None,
        numeric_value_adjusted=(
            Decimal(str(adjusted)) if adjusted is not None else None
        ),
        is_forecast=is_forecast, source_name=kw.pop("source_name", "seed"),
        primary_method=kw.pop("primary_method", "provider"),
        currency=kw.pop("currency", "USD"), **kw,
    )
    db.add(row)
    db.commit()
    return row


def _rows(db, comp, key, ptype):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == ptype,
            CompanyValue.period_year == YEAR,
        )
        .order_by(CompanyValue.is_forecast.asc())
        .all()
    )


def test_placeholder_for_open_quarter_with_fy_forecast(db, company, q4_open):
    """FY-Forecast mit Wert + leeres offenes Q4: not_estimated-Platzhalter
    mit fcf-spezifischem Text; berichtete Quartale bleiben leer."""
    _seed(db, company, "fcf", "FY", 400, is_forecast=True,
          primary_method="web_guidance")

    written = derive_explain_open_gaps(db, company.id, YEAR)
    db.commit()

    assert written == 1
    (q4,) = _rows(db, company, "fcf", "Q4")
    assert q4.numeric_value is None
    assert q4.is_forecast is True
    assert q4.primary_method == "not_estimated"
    assert q4.source_name == _NOT_ESTIMATED_TEXTS["fcf"]
    # Berichtete Quartale (Q1-Q3) bekommen KEINE solchen Platzhalter.
    for q in ("Q1", "Q2", "Q3"):
        assert _rows(db, company, "fcf", q) == []


def test_key_specific_and_generic_texts(db, company, q4_open):
    """operating_cash_flow bekommt den OCF-Text, revenue den generischen."""
    _seed(db, company, "operating_cash_flow", "FY", 500, is_forecast=True,
          primary_method="web_guidance")
    _seed(db, company, "revenue", "FY", 1000, is_forecast=True,
          primary_method="web_guidance")

    derive_explain_open_gaps(db, company.id, YEAR)
    db.commit()

    (ocf,) = _rows(db, company, "operating_cash_flow", "Q4")
    assert ocf.source_name == _NOT_ESTIMATED_TEXTS["operating_cash_flow"]
    (rev,) = _rows(db, company, "revenue", "Q4")
    assert rev.source_name == _NOT_ESTIMATED_GENERIC


def test_eps_adjusted_only_fy_anchor_counts(db, company, q4_open):
    """FY-Forecast nur mit adjusted-Wert (GAAP leer) zaehlt als Anker —
    das leere Q4 bekommt den eps-Text."""
    _seed(db, company, "eps_diluted", "FY", None, is_forecast=True,
          primary_method="web_guidance", adjusted="5.20")

    written = derive_explain_open_gaps(db, company.id, YEAR)
    db.commit()

    assert written == 1
    (q4,) = _rows(db, company, "eps_diluted", "Q4")
    assert q4.primary_method == "not_estimated"
    assert q4.source_name == _NOT_ESTIMATED_TEXTS["eps_diluted"]


def test_no_placeholder_without_fy_forecast(db, company, q4_open):
    """Ohne FY-Forecast mit Wert entsteht nichts."""
    assert derive_explain_open_gaps(db, company.id, YEAR) == 0
    assert _rows(db, company, "fcf", "Q4") == []


def test_cell_with_value_untouched(db, company, q4_open):
    """Q4 hat bereits eine Schaetzung mit Wert: keine Erklaerung noetig,
    die Zeile bleibt unveraendert."""
    _seed(db, company, "net_income", "FY", 460, is_forecast=True,
          primary_method="web_guidance")
    est = _seed(db, company, "net_income", "Q4", 120, is_forecast=True,
                primary_method="web_guidance")

    assert derive_explain_open_gaps(db, company.id, YEAR) == 0
    db.refresh(est)
    assert est.numeric_value == Decimal("120")
    assert est.primary_method == "web_guidance"
    assert est.source_name == "seed"


def test_idempotent(db, company, q4_open):
    _seed(db, company, "fcf", "FY", 400, is_forecast=True,
          primary_method="web_guidance")

    assert derive_explain_open_gaps(db, company.id, YEAR) == 1
    db.commit()
    assert derive_explain_open_gaps(db, company.id, YEAR) == 0
    db.commit()
    (q4,) = _rows(db, company, "fcf", "Q4")
    assert q4.primary_method == "not_estimated"
    assert q4.source_name == _NOT_ESTIMATED_TEXTS["fcf"]


def test_existing_empty_row_updated_in_place(db, company, q4_open):
    """Komplett leere, nicht-authoritative Zeile (z.B. geraeumtes
    Residuum): nur source_name/primary_method werden gesetzt, keine
    zweite Zeile."""
    _seed(db, company, "sbc", "FY", 400, is_forecast=True,
          primary_method="web_guidance")
    _seed(db, company, "sbc", "Q4", None, is_forecast=True,
          primary_method="calculated",
          source_name="Geraeumt: FY-Anker der Ableitung entfallen")

    written = derive_explain_open_gaps(db, company.id, YEAR)
    db.commit()

    assert written == 1
    (q4,) = _rows(db, company, "sbc", "Q4")
    assert q4.primary_method == "not_estimated"
    assert q4.source_name == _NOT_ESTIMATED_GENERIC
    assert q4.numeric_value is None


def test_manual_and_web_guidance_empty_rows_untouched(db, company, q4_open):
    """Leere manual-/web_guidance-Zeilen behalten ihre Methode — am
    'web_guidance'-Tag haengt der Direkt-Schaetzungs-Vorrang der
    Residual-Ableitung."""
    _seed(db, company, "net_income", "FY", 460, is_forecast=True,
          primary_method="web_guidance")
    wg = _seed(db, company, "net_income", "Q4", None, is_forecast=True,
               primary_method="web_guidance", adjusted="130")

    assert derive_explain_open_gaps(db, company.id, YEAR) == 0
    db.refresh(wg)
    assert wg.primary_method == "web_guidance"
    assert wg.source_name == "seed"
    assert wg.numeric_value_adjusted == Decimal("130")


def test_reported_period_placeholder_becomes_not_found(db, company, q4_open):
    """Hygiene: ein not_estimated-Platzhalter eines inzwischen
    BERICHTETEN Quartals wird auf not_found (rot) umgestuft."""
    stale = _seed(db, company, "fcf", "Q2", None, is_forecast=True,
                  primary_method="not_estimated",
                  source_name=_NOT_ESTIMATED_TEXTS["fcf"])

    derive_explain_open_gaps(db, company.id, YEAR)
    db.commit()

    db.refresh(stale)
    assert stale.primary_method == "not_found"
    assert stale.source_name == NOT_FOUND_SOURCE


def test_real_writer_overwrites_placeholder(db, company, q4_open):
    """Ein spaeterer echter Writer (hier: Residual-Ableitung aus dem
    FY-Estimate) ersetzt den not_estimated-Platzhalter regulaer."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "net_income", q, v)
    _seed(db, company, "net_income", "FY", 460, is_forecast=True,
          primary_method="web_guidance")

    assert derive_explain_open_gaps(db, company.id, YEAR) == 1
    db.commit()
    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 1
    q4 = next(r for r in _rows(db, company, "net_income", "Q4") if r.is_forecast)
    assert q4.numeric_value == Decimal("130")
    assert q4.primary_method == "calculated"


def test_not_estimated_is_replaceable_in_writer_contracts(db, company):
    """not_estimated steht in den Replaceable-Kontrakten — auch wenn die
    Zeile (theoretisch) einen Wert truege."""
    row = CompanyValue(
        company_id=company.id, value_key="fcf", period_type="Q4",
        period_year=YEAR, is_forecast=True,
        numeric_value=Decimal("1"), primary_method="not_estimated",
    )
    assert _derivation_replaceable(row) is True
    assert _row_replaceable(row) is True
