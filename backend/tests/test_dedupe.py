"""dedupe_company_values: Duplikat-Zeilen pro Slot (company_id, value_key,
period_type, period_year, is_forecast) — beste Zeile ueberlebt."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.values.dedupe import dedupe_company_values
from app.values.models import CompanyValue
from tests.test_values import _seed_catalog


def _allow_duplicates(db) -> None:
    """Simuliert den Vor-Index-Zustand: die Migration dedupet BEVOR der
    Unique-Index angelegt wird. Mit Index liessen sich die Duplikat-
    Fixtures gar nicht erst einfuegen. Schema wird pro Test neu erstellt,
    der Drop leakt also nicht in andere Tests."""
    db.execute(text("DROP INDEX IF EXISTS uq_company_values_slot"))
    db.commit()


def _seed_company(db) -> Company:
    _seed_catalog(db)
    user = User(email="dedupe@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.flush()
    portfolio = Portfolio(name="Test", owner_user_id=user.id)
    db.add(portfolio)
    db.flush()
    company = Company(
        portfolio_id=portfolio.id, name="Apple", ticker="AAPL", currency="USD",
    )
    db.add(company)
    db.flush()
    return company


def _row(company, key="net_income", period_type="FY", period_year=2025,
         is_forecast=False, value=None, primary_method=None,
         fetched_at=None, **kw) -> CompanyValue:
    return CompanyValue(
        id=uuid4(),
        company_id=company.id,
        value_key=key,
        period_type=period_type,
        period_year=period_year,
        is_forecast=is_forecast,
        numeric_value=value,
        primary_method=primary_method,
        fetched_at=fetched_at or datetime.now(timezone.utc),
        **kw,
    )


def test_provider_row_survives_over_two_stage(db):
    company = _seed_company(db)
    _allow_duplicates(db)
    now = datetime.now(timezone.utc)
    provider_row = _row(company, value=Decimal("100"), primary_method="provider",
                        fetched_at=now - timedelta(days=5))
    two_stage_row = _row(company, value=Decimal("99"),
                         primary_method="two_stage_confirmed", fetched_at=now)
    db.add_all([provider_row, two_stage_row])
    db.commit()

    deleted = dedupe_company_values(db)
    db.commit()

    assert deleted == 1
    rows = db.query(CompanyValue).filter(
        CompanyValue.company_id == company.id,
        CompanyValue.value_key == "net_income",
    ).all()
    assert len(rows) == 1
    assert rows[0].id == provider_row.id
    assert rows[0].primary_method == "provider"


def test_value_row_survives_over_not_found_placeholder(db):
    company = _seed_company(db)
    _allow_duplicates(db)
    now = datetime.now(timezone.utc)
    placeholder = _row(company, key="revenue", value=None,
                       primary_method="not_found", fetched_at=now)
    value_row = _row(company, key="revenue", value=Decimal("500"),
                     primary_method="web_guidance",
                     fetched_at=now - timedelta(days=30))
    db.add_all([placeholder, value_row])
    db.commit()

    deleted = dedupe_company_values(db)
    db.commit()

    assert deleted == 1
    rows = db.query(CompanyValue).filter(
        CompanyValue.company_id == company.id,
        CompanyValue.value_key == "revenue",
    ).all()
    assert len(rows) == 1
    assert rows[0].id == value_row.id
    assert rows[0].numeric_value == Decimal("500")


def test_manual_row_survives_over_pdf(db):
    # Manual-Override rankt VOR from_ir_pdf — konsistent mit den
    # Schreibpfad-Guards, die Manual zuerst pruefen.
    company = _seed_company(db)
    _allow_duplicates(db)
    now = datetime.now(timezone.utc)
    pdf_row = _row(company, value=Decimal("100"), primary_method="pdf",
                   from_ir_pdf=True, fetched_at=now)
    manual_row = _row(company, value=Decimal("101"), primary_method="manual",
                      manually_overridden=True,
                      fetched_at=now - timedelta(days=10))
    db.add_all([pdf_row, manual_row])
    db.commit()

    deleted = dedupe_company_values(db)
    db.commit()

    assert deleted == 1
    rows = db.query(CompanyValue).filter(
        CompanyValue.company_id == company.id,
        CompanyValue.value_key == "net_income",
    ).all()
    assert len(rows) == 1
    assert rows[0].id == manual_row.id
    assert rows[0].manually_overridden is True


def test_distinct_slots_untouched(db):
    company = _seed_company(db)
    # Gleiche Zelle, aber Actual + Forecast = zwei legitime Slots.
    actual = _row(company, value=Decimal("100"), is_forecast=False,
                  primary_method="provider")
    forecast = _row(company, value=Decimal("110"), is_forecast=True,
                    primary_method="web_guidance")
    # SNAPSHOT-Slot mit period_year=None — NULL-Gruppe darf nicht crashen.
    snapshot = _row(company, key="revenue", period_type="SNAPSHOT",
                    period_year=None, value=Decimal("1"))
    db.add_all([actual, forecast, snapshot])
    db.commit()

    deleted = dedupe_company_values(db)
    db.commit()

    assert deleted == 0
    assert db.query(CompanyValue).filter(
        CompanyValue.company_id == company.id
    ).count() == 3


def test_null_period_year_duplicates_deduped(db):
    company = _seed_company(db)
    _allow_duplicates(db)
    now = datetime.now(timezone.utc)
    older = _row(company, key="revenue", period_type="SNAPSHOT",
                 period_year=None, value=Decimal("1"),
                 primary_method="provider", fetched_at=now - timedelta(days=2))
    newer = _row(company, key="revenue", period_type="SNAPSHOT",
                 period_year=None, value=Decimal("2"),
                 primary_method="provider", fetched_at=now)
    db.add_all([older, newer])
    db.commit()

    deleted = dedupe_company_values(db)
    db.commit()

    assert deleted == 1
    rows = db.query(CompanyValue).filter(
        CompanyValue.company_id == company.id,
        CompanyValue.period_type == "SNAPSHOT",
    ).all()
    assert len(rows) == 1
    assert rows[0].id == newer.id
