"""Tests fuer die buyback_volume-Programm-Ausnahme (guidance_estimates):
Firmen kuendigen Rueckkauf-PROGRAMME an, die das Vorjahres-Ist weit
uebersteigen (SAP-Muster). Liefert das Modell program_context und liegt
der Schaetzwert <= dem geparsten Programmvolumen, passiert die
Band-Verletzung; dazu Absurditaets-Deckel > 25% Market Cap (SNAPSHOT).
Hermetisch — _call_claude gemockt."""
from datetime import date
from decimal import Decimal

import pytest

import app.values.guidance_estimates as ge
from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.values.models import CompanyValue

RUNNING_YEAR = date.today().year
PREV_YEAR = RUNNING_YEAR - 1


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="buyback@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.flush()
    portfolio = Portfolio(name="P", owner_user_id=user.id)
    db.add(portfolio)
    db.flush()
    comp = Company(
        portfolio_id=portfolio.id, name="BuybackCo", ticker="BBC",
        currency="EUR", isin="DE0001234567",
        fiscal_year_end_month=12, fiscal_year_end_day=31,
    )
    db.add(comp)
    db.commit()
    return comp


def _payload(value, program_context=None):
    entry = {
        "value": value, "source": "consensus",
        "reasoning": "Buyback consensus based on the announced program",
        "url": "https://example.com/ir/buyback",
    }
    if program_context is not None:
        entry["program_context"] = program_context
    return {"buyback_volume": entry}


def _mock_claude(monkeypatch, payload):
    monkeypatch.setattr(
        ge, "_call_claude",
        lambda company, year, cost_tracker=None, open_quarter=None: payload,
    )


def _seed_prev_buyback(db, comp, value: str):
    db.add(CompanyValue(
        company_id=comp.id, value_key="buyback_volume", period_type="FY",
        period_year=PREV_YEAR, numeric_value=Decimal(value),
        is_forecast=False, currency="EUR", primary_method="provider",
    ))
    db.commit()


def _seed_market_cap(db, comp, value: str):
    db.add(CompanyValue(
        company_id=comp.id, value_key="market_cap",
        period_type="SNAPSHOT", period_year=None,
        numeric_value=Decimal(value),
    ))
    db.commit()


def _bb_rows(db, comp):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key == "buyback_volume",
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == RUNNING_YEAR,
        )
        .all()
    )


def test_program_context_allows_acceleration(db, company, monkeypatch):
    """SAP-Fall: Vorjahres-Ist 1.9 Mrd, Schaetzung 7 Mrd (weit ausserhalb
    des 40-160%-Bands) — passiert dank Programm-Kontext (<= 10 Mrd)."""
    _mock_claude(monkeypatch, _payload(
        7_000_000_000,
        program_context="New share repurchase program of up to EUR 10 "
                        "billion for 2026-2028 (company press release)",
    ))
    _seed_prev_buyback(db, company, "1900000000")

    written = ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert written == 1
    rows = _bb_rows(db, company)
    assert len(rows) == 1
    assert rows[0].numeric_value == Decimal("7000000000")
    assert rows[0].is_forecast is True
    assert rows[0].primary_method == "web_guidance"


def test_without_context_still_discarded(db, company, monkeypatch):
    """Ohne program_context gilt das Vorjahresband unveraendert."""
    _mock_claude(monkeypatch, _payload(7_000_000_000))
    _seed_prev_buyback(db, company, "1900000000")

    written = ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert written == 0
    assert _bb_rows(db, company) == []


def test_value_above_program_volume_discarded(db, company, monkeypatch):
    """Schaetzwert ueber dem im Kontext genannten Programmvolumen: die
    Ausnahme greift nicht, der Wert wird verworfen."""
    _mock_claude(monkeypatch, _payload(
        12_000_000_000,
        program_context="Program of up to EUR 10 billion through 2028",
    ))
    _seed_prev_buyback(db, company, "1900000000")

    written = ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert written == 0
    assert _bb_rows(db, company) == []


def test_unparseable_context_falls_back_to_band(db, company, monkeypatch):
    """Kein parsebares Volumen im Kontext (nur Jahreszahlen/Prosa) ->
    alte Regel, Band verwirft."""
    _mock_claude(monkeypatch, _payload(
        7_000_000_000,
        program_context="Significantly expanded buyback program 2026-2028",
    ))
    _seed_prev_buyback(db, company, "1900000000")

    written = ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert written == 0
    assert _bb_rows(db, company) == []


def test_mcap_cap_overrides_program_context(db, company, monkeypatch):
    """> 25% der Market Cap (SNAPSHOT) wird IMMER verworfen — auch mit
    gueltigem Programm-Kontext."""
    _mock_claude(monkeypatch, _payload(
        7_000_000_000,
        program_context="Program of up to EUR 10 billion through 2028",
    ))
    _seed_prev_buyback(db, company, "1900000000")
    _seed_market_cap(db, company, "20000000000")  # 25% = 5 Mrd < 7 Mrd

    written = ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert written == 0
    assert _bb_rows(db, company) == []


def test_mcap_cap_applies_within_band(db, company, monkeypatch):
    """Der Deckel greift auch OHNE Band-Verletzung (Vorjahres-Ist nah am
    Schaetzwert, aber > 25% der Market Cap)."""
    _mock_claude(monkeypatch, _payload(7_000_000_000))
    _seed_prev_buyback(db, company, "6000000000")  # +17%: im Band
    _seed_market_cap(db, company, "20000000000")

    written = ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert written == 0
    assert _bb_rows(db, company) == []


def test_in_band_buyback_without_context_still_written(db, company, monkeypatch):
    """Regression: Wert im Band ohne Kontext und ohne Market Cap wird
    weiterhin normal geschrieben."""
    _mock_claude(monkeypatch, _payload(7_000_000_000))
    _seed_prev_buyback(db, company, "6000000000")

    written = ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert written == 1
    assert _bb_rows(db, company)[0].numeric_value == Decimal("7000000000")


@pytest.mark.parametrize("text,expected", [
    ("up to EUR 10 billion for 2026-2028", Decimal("10000000000")),
    ("4-10 Mrd EUR ueber drei Jahre", Decimal("10000000000")),
    ("$5.8bn program announced in January", Decimal("5800000000")),
    ("1,9 Mrd Rueckkaufvolumen", Decimal("1.9E+9")),
    ("program volume 5000000000 per press release", Decimal("5000000000")),
    ("500 million tranche of the 2 billion program", Decimal("2000000000")),
    ("significantly expanded program 2026-2028", None),
    ("", None),
    (None, None),
])
def test_parse_program_volume(text, expected):
    assert ge._parse_program_volume(text) == expected


def test_prompt_mentions_program_context(company):
    prompt = ge._build_user_prompt(company, RUNNING_YEAR)
    assert "program_context" in prompt
