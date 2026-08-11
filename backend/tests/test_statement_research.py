"""Tests fuer die Statement-Recherche (statement_research.py): EIN
Claude-Call pro Firma+Jahr+Statement-Gruppe ersetzt die Two-Stage-
Recherche fuer Nicht-US-Filer. Hermetisch — der Claude-Call ist via
_call_claude gemockt, das Refresh-Wiring via monkeypatch auf routes."""
from datetime import date
from decimal import Decimal

import pytest

import app.values.statement_research as sr
from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.values.models import CompanyValue

YEAR = date.today().year - 2  # abgeschlossenes Jahr
PREV = YEAR - 1


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="stmt@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.flush()
    portfolio = Portfolio(name="P", owner_user_id=user.id)
    db.add(portfolio)
    db.flush()
    comp = Company(
        portfolio_id=portfolio.id, name="Deutsche TestCo", ticker="DTC",
        currency="EUR", isin="DE0001234567",
        fiscal_year_end_month=12, fiscal_year_end_day=31,
    )
    db.add(comp)
    db.commit()
    return comp


def _entry(value, quote="Tabellenzeile aus dem Bericht", url="https://ir.example.com/q.pdf",
           derived_from=None):
    return {"value": value, "quote": quote, "url": url, "derived_from": derived_from}


def _income_payload(**overrides):
    base = {
        "revenue": {
            "FY": _entry(40_000_000_000),
            "Q1": _entry(9_000_000_000),
            "Q2": _entry(10_000_000_000, derived_from="H1-Q1"),
            "Q3": _entry(10_500_000_000, derived_from="9M-H1"),
            "Q4": _entry(10_500_000_000, derived_from="FY-9M"),
        },
        "net_income": {
            "FY": _entry(3_000_000_000),
        },
        "eps_diluted": {
            "FY": _entry("3.05"),
        },
        "net_income_adjusted": {
            "FY": _entry(3_500_000_000, quote="Bereinigtes Konzernergebnis 3,5 Mrd"),
        },
    }
    base.update(overrides)
    return base


def _mock_claude(monkeypatch, payloads: dict):
    """payloads: {group: dict} — nicht enthaltene Gruppen liefern {}."""
    calls: list[tuple] = []

    def fake(company, year, group, cost_tracker=None):
        calls.append((company.ticker, year, group))
        return payloads.get(group, {})

    monkeypatch.setattr(sr, "_call_claude", fake)
    return calls


def _rows(db, comp, key, pt, year=YEAR):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == pt,
            CompanyValue.period_year == year,
        )
        .all()
    )


def _seed(db, comp, key, pt, value, year=YEAR, **kw):
    row = CompanyValue(
        company_id=comp.id, value_key=key, period_type=pt, period_year=year,
        numeric_value=Decimal(str(value)) if value is not None else None,
        currency=kw.pop("currency", "EUR"), **kw,
    )
    db.add(row)
    db.commit()
    return row


# --- Parsing + Persistenz --------------------------------------------------


def test_three_calls_write_fy_and_quarters(db, company, monkeypatch):
    """Ein Lauf = drei Gruppen-Calls; FY und Quartale werden als Actuals
    (is_forecast=False, primary_method=statement_research) geschrieben,
    source_name ist quote-first."""
    calls = _mock_claude(monkeypatch, {"income": _income_payload()})

    written = sr.fetch_statement_research(db, company, YEAR)
    db.commit()

    assert [c[2] for c in calls] == ["income", "cashflow", "balance"]
    assert written == 7  # revenue FY+Q1-Q4, net_income FY, eps FY

    fy = _rows(db, company, "revenue", "FY")
    assert len(fy) == 1
    row = fy[0]
    assert row.numeric_value == Decimal("40000000000")
    assert row.is_forecast is False
    assert row.primary_method == "statement_research"
    assert row.currency == "EUR"
    assert row.source_name.startswith("Tabellenzeile")
    assert "https://ir.example.com/q.pdf" in row.source_name
    assert row.source_link == "https://ir.example.com/q.pdf"

    q1 = _rows(db, company, "revenue", "Q1")[0]
    assert q1.numeric_value == Decimal("9000000000")
    assert q1.is_forecast is False


def test_derived_quarters_are_marked(db, company, monkeypatch):
    """H1-abgeleitete Quartale (derived_from) tragen den Rechenweg im
    source_name — quote-first, beginnt nie mit https."""
    _mock_claude(monkeypatch, {"income": _income_payload()})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    q2 = _rows(db, company, "revenue", "Q2")[0]
    assert q2.source_name.startswith("Abgeleitet (H1-Q1):")
    assert not q2.source_name.startswith("https")


def test_unreported_periods_get_not_found_placeholder(db, company, monkeypatch):
    """Kein Beleg -> null -> not_found-Platzhalter (rote Zelle), Regel 3."""
    _mock_claude(monkeypatch, {"income": _income_payload()})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    q1_ni = _rows(db, company, "net_income", "Q1")
    assert len(q1_ni) == 1
    assert q1_ni[0].numeric_value is None
    assert q1_ni[0].primary_method == "not_found"


def test_groups_parameter_limits_calls(db, company, monkeypatch):
    calls = _mock_claude(monkeypatch, {})

    sr.fetch_statement_research(db, company, YEAR, groups=["cashflow"])

    assert [c[2] for c in calls] == ["cashflow"]


def test_us_company_no_call(db, company, monkeypatch):
    """US-Filer laufen ueber EDGAR/8-K-Bruecke — kein Statement-Call."""
    company.isin = "US0001234567"
    db.commit()
    calls = _mock_claude(monkeypatch, {"income": _income_payload()})

    assert sr.fetch_statement_research(db, company, YEAR) == 0
    assert calls == []


# --- Schreibrechte ---------------------------------------------------------


def test_manual_pdf_provider_rows_stay(db, company, monkeypatch):
    """Manual-/PDF-/Provider-Zeilen mit Wert sind authoritative."""
    manual = _seed(db, company, "revenue", "FY", 1, manually_overridden=True)
    pdf = _seed(db, company, "net_income", "FY", 2, from_ir_pdf=True)
    provider = _seed(db, company, "eps_diluted", "FY", 3, primary_method="provider")
    _mock_claude(monkeypatch, {"income": _income_payload()})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()
    db.refresh(manual)
    db.refresh(pdf)
    db.refresh(provider)

    assert manual.numeric_value == Decimal("1")
    assert pdf.numeric_value == Decimal("2")
    assert provider.numeric_value == Decimal("3")
    assert provider.primary_method == "provider"
    # Refresh-Versuch ist trotzdem dokumentiert.
    assert provider.last_refresh_attempt is not None


def test_two_stage_and_not_found_rows_replaced(db, company, monkeypatch):
    """Alte LLM-Zeilen (two_stage_*) und not_found-Platzhalter sind
    ersetzbar — der Statement-Wert uebernimmt den Slot."""
    old = _seed(db, company, "revenue", "FY", 999,
                primary_method="two_stage_verified")
    placeholder = _seed(db, company, "net_income", "FY", None,
                        primary_method="not_found")
    _mock_claude(monkeypatch, {"income": _income_payload()})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()
    db.refresh(old)
    db.refresh(placeholder)

    assert old.numeric_value == Decimal("40000000000")
    assert old.primary_method == "statement_research"
    assert placeholder.numeric_value == Decimal("3000000000")
    assert placeholder.primary_method == "statement_research"


def test_own_rows_replaceable_on_second_run(db, company, monkeypatch):
    """Idempotenz: ein zweiter Lauf aktualisiert die eigene Zeile."""
    _mock_claude(monkeypatch, {"income": _income_payload()})
    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    updated = _income_payload()
    updated["revenue"]["FY"] = _entry(41_000_000_000)
    _mock_claude(monkeypatch, {"income": updated})
    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    fy = _rows(db, company, "revenue", "FY")
    assert len(fy) == 1
    assert fy[0].numeric_value == Decimal("41000000000")


def test_currency_conflict_blocks_write(db, company, monkeypatch):
    """Abweichendes Currency-Label der bestehenden Zeile blockt den Write."""
    old = _seed(db, company, "revenue", "FY", 999, currency="USD",
                primary_method="two_stage_verified")
    _mock_claude(monkeypatch, {"income": _income_payload()})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()
    db.refresh(old)

    assert old.numeric_value == Decimal("999")
    assert old.currency == "USD"


# --- Gates -----------------------------------------------------------------


def test_unit_gate_discards_sub_million_values(db, company, monkeypatch):
    """Absolutwerte unter 1 Mio (BAYN-Muster: '2510' statt 2,51 Mrd)
    werden verworfen; Per-Share-Keys sind ausgenommen."""
    payload = _income_payload()
    payload["revenue"] = {"FY": _entry(2510)}
    _mock_claude(monkeypatch, {"income": payload})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    fy = _rows(db, company, "revenue", "FY")
    assert len(fy) == 1 and fy[0].primary_method == "not_found"
    # eps (Per-Share) wurde trotz Wert < 1 Mio geschrieben.
    assert _rows(db, company, "eps_diluted", "FY")[0].numeric_value == Decimal("3.05")


def test_prev_year_band_gate(db, company, monkeypatch):
    """FY-Wert ausserhalb 40-160% des Vorjahres-Ist wird verworfen;
    innerhalb des Bandes wird geschrieben."""
    _seed(db, company, "revenue", "FY", 10_000_000_000, year=PREV,
          primary_method="provider")
    _seed(db, company, "net_income", "FY", 2_900_000_000, year=PREV,
          primary_method="provider")
    payload = {
        "revenue": {"FY": _entry(30_000_000_000)},   # 3x Vorjahr -> raus
        "net_income": {"FY": _entry(3_000_000_000)},  # +3.4% -> ok
    }
    _mock_claude(monkeypatch, {"income": payload})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    assert _rows(db, company, "revenue", "FY")[0].primary_method == "not_found"
    assert _rows(db, company, "net_income", "FY")[0].numeric_value == Decimal("3000000000")


def test_prev_year_band_allows_sign_flip(db, company, monkeypatch):
    """Turnaround (Vorzeichenwechsel) ist vom Band ausgenommen."""
    _seed(db, company, "net_income", "FY", -1_000_000_000, year=PREV,
          primary_method="provider")
    payload = {"net_income": {"FY": _entry(3_000_000_000)}}
    _mock_claude(monkeypatch, {"income": payload})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    assert _rows(db, company, "net_income", "FY")[0].numeric_value == Decimal("3000000000")


def test_reported_above_adjusted_discards_pair(db, company, monkeypatch):
    """reported > adjusted + 1% ist ein klarer Verstoss — beide Werte der
    Periode werden verworfen."""
    payload = {
        "net_income": {"FY": _entry(4_000_000_000)},
        "net_income_adjusted": {"FY": _entry(3_000_000_000)},
    }
    _mock_claude(monkeypatch, {"income": payload})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    fy = _rows(db, company, "net_income", "FY")
    assert len(fy) == 1
    assert fy[0].numeric_value is None
    assert fy[0].numeric_value_adjusted is None


def test_qsum_enforcement_discards_quarters_keeps_fy(db, company, monkeypatch):
    """FY + alle 4 Quartale geliefert, Summe > 1% daneben: Quartale
    verwerfen, FY behalten."""
    payload = {
        "revenue": {
            "FY": _entry(40_000_000_000),
            "Q1": _entry(9_000_000_000),
            "Q2": _entry(9_000_000_000),
            "Q3": _entry(9_000_000_000),
            "Q4": _entry(9_000_000_000),  # Summe 36 Mrd vs FY 40 Mrd
        },
    }
    _mock_claude(monkeypatch, {"income": payload})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    assert _rows(db, company, "revenue", "FY")[0].numeric_value == Decimal("40000000000")
    for q in ("Q1", "Q2", "Q3", "Q4"):
        rows = _rows(db, company, "revenue", q)
        assert len(rows) == 1 and rows[0].primary_method == "not_found"


def test_qsum_within_tolerance_writes_quarters(db, company, monkeypatch):
    _mock_claude(monkeypatch, {"income": _income_payload()})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    for q in ("Q1", "Q2", "Q3", "Q4"):
        assert _rows(db, company, "revenue", q)[0].numeric_value is not None


# --- Adjusted-Sidecars -----------------------------------------------------


def test_adjusted_sidecar_written_to_base_row(db, company, monkeypatch):
    _mock_claude(monkeypatch, {"income": _income_payload()})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    ni = _rows(db, company, "net_income", "FY")[0]
    assert ni.numeric_value == Decimal("3000000000")
    assert ni.numeric_value_adjusted == Decimal("3500000000")
    assert ni.adjustments_source.startswith("Bereinigtes")
    assert not ni.adjustments_source.startswith("https://")


def test_protected_adjusted_not_overwritten(db, company, monkeypatch):
    """adjusted_is_protected ('Manual' oder reine URL) bleibt stehen."""
    row = _seed(db, company, "net_income", "FY", 2_900_000_000,
                primary_method="two_stage_verified")
    row.numeric_value_adjusted = Decimal("9999")
    row.adjustments_source = "Manual"
    db.commit()
    _mock_claude(monkeypatch, {"income": _income_payload()})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()
    db.refresh(row)

    # GAAP-Teil wurde aktualisiert, der geschuetzte Adjusted-Wert nicht.
    assert row.numeric_value == Decimal("3000000000")
    assert row.numeric_value_adjusted == Decimal("9999")
    assert row.adjustments_source == "Manual"


# --- Refresh-Wiring (Backfill-Umleitung) -----------------------------------


def _login_refresh(client, db, company):
    client.post("/api/auth/login", json={"email": "stmt@example.com", "password": "pw1234"})
    return company.id


def test_prev_year_inputs_use_statement_research_not_web(db, company, monkeypatch):
    """_ensure_previous_year_inputs (Nicht-US): Statement-Recherche statt
    Two-Stage/Web-Fallback — der alte research_value_dual-Pfad wird nicht
    mehr betreten."""
    import app.values.routes as routes

    def boom(*a, **kw):
        raise AssertionError("Alt-Pfad _try_web_guidance darf nicht laufen")

    monkeypatch.setattr(routes, "_try_web_guidance", boom)

    statement_calls: list[tuple] = []

    def fake_statement(db_, comp, year, cost_tracker=None, groups=None):
        statement_calls.append((comp.ticker, year, tuple(groups or ())))
        return 0

    monkeypatch.setattr(sr, "fetch_statement_research", fake_statement)

    routes._ensure_previous_year_inputs(db, company.ticker, company, company.id, YEAR)

    assert len(statement_calls) == 1
    ticker, year, groups = statement_calls[0]
    assert (ticker, year) == ("DTC", PREV)
    # fcf -> cashflow, net_debt -> balance, net_income -> income
    assert set(groups) == {"income", "cashflow", "balance"}


def test_prev_year_inputs_skip_when_values_exist(db, company, monkeypatch):
    import app.values.routes as routes

    for key in ("net_income", "fcf", "net_debt", "sbc", "buyback_volume", "dividends"):
        _seed(db, company, key, "FY", 5_000_000_000, year=PREV,
              primary_method="provider")

    def boom(*a, **kw):
        raise AssertionError("kein Statement-Call noetig")

    monkeypatch.setattr(sr, "fetch_statement_research", boom)

    routes._ensure_previous_year_inputs(db, company.ticker, company, company.id, YEAR)


def test_statement_research_row_counts_as_fresh_backfill(db, company):
    """_prev_year_needs_backfill: statement_research-Zeilen sind frisch —
    der zweite Refresh-Klick bleibt billig."""
    from app.values.routes import _prev_year_needs_backfill

    _seed(db, company, "revenue", "FY", 1_000_000_000, year=PREV,
          primary_method="statement_research")
    assert _prev_year_needs_backfill(db, company.id, "revenue", PREV) is False
    assert _prev_year_needs_backfill(db, company.id, "net_income", PREV) is True


# --- Validator-Diet --------------------------------------------------------


def test_validator_diet_keeps_core_checks_and_clears_legacy_flags(db, company):
    """full_checks=False (Nicht-US-Diet): eps_ni bleibt aktiv, die
    Alt-Checks (unit_scale/prior_year_copy/...) setzen keine Flags mehr
    und raeumen stale Flags."""
    from app.values.consistency import validate_cross_metrics

    # eps_ni-Mismatch: eps x shares weit weg von net_income.
    _seed(db, company, "eps_diluted", "FY", "9.34", year=YEAR)
    _seed(db, company, "shares_outstanding", "SNAPSHOT", 178_000_000, year=None)
    ni = _seed(db, company, "net_income", "FY", 764_000_000, year=YEAR)
    # unit_scale-Verdacht (< 1 Mio) + stale Alt-Flag: Diet raeumt beides.
    capex = _seed(db, company, "capex", "FY", 2510, year=YEAR,
                  consistency_flags="unit_scale_suspect,prior_year_copy")

    active = validate_cross_metrics(db, company.id, YEAR, is_us=False,
                                    full_checks=False)
    db.commit()
    db.refresh(ni)
    db.refresh(capex)

    assert "eps_ni_mismatch" in (ni.consistency_flags or "")
    assert capex.consistency_flags is None
    assert all(not f.startswith("unit_scale") for f in active)
