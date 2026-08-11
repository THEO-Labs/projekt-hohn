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


def test_adjusted_below_reported_is_allowed(db, company, monkeypatch):
    """Non-IFRS darf UNTER reported liegen (schliesst auch Einmal-Gewinne
    aus, SAP-Muster) — beide Spuren werden geschrieben."""
    payload = {
        "net_income": {"FY": _entry(4_000_000_000)},
        "net_income_adjusted": {"FY": _entry(3_000_000_000)},
    }
    _mock_claude(monkeypatch, {"income": payload})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    fy = _rows(db, company, "net_income", "FY")
    assert len(fy) == 1
    assert fy[0].numeric_value == Decimal("4000000000")
    assert fy[0].numeric_value_adjusted == Decimal("3000000000")


def test_track_mixup_beyond_band_discards_pair(db, company, monkeypatch):
    """Mehr als 60% Abstand zwischen reported und adjusted ist eine
    Spur-Verwechslung — beide Werte der Periode werden verworfen."""
    payload = {
        "net_income": {"FY": _entry(4_000_000_000)},
        "net_income_adjusted": {"FY": _entry(1_000_000_000)},
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


# --- Karenz-Gate: unberichtete Perioden werden nicht angefasst -------------


def _running_year(db, company):
    """FY-Ende ~120 Tage in der Zukunft: Q1/Q2 berichtet (Karenz um),
    Q3/Q4/FY unberichtet — unabhaengig vom Ausfuehrungsdatum."""
    from datetime import timedelta

    t = date.today() + timedelta(days=120)
    company.fiscal_year_end_month = t.month
    company.fiscal_year_end_day = t.day
    db.commit()
    return t.year


def test_running_year_unreported_periods_untouched(db, company, monkeypatch):
    """Laufendes Jahr: nur berichtete Perioden (Karenz abgelaufen) werden
    geschrieben bzw. not_found-gestempelt. Unberichtete Perioden bleiben
    komplett ohne Zeile — auch wenn das Modell Werte liefert."""
    year = _running_year(db, company)
    payload = {
        "revenue": {
            "FY": _entry(40_000_000_000),
            "Q1": _entry(9_000_000_000),
            "Q2": _entry(10_000_000_000),
            "Q3": _entry(10_500_000_000),
            "Q4": _entry(10_500_000_000),
        },
        "net_income": {"FY": _entry(3_000_000_000)},
    }
    _mock_claude(monkeypatch, {"income": payload})

    sr.fetch_statement_research(db, company, year, groups=["income"])
    db.commit()

    # Berichtete Quartale geschrieben.
    assert _rows(db, company, "revenue", "Q1", year=year)[0].numeric_value == Decimal("9000000000")
    assert _rows(db, company, "revenue", "Q2", year=year)[0].numeric_value == Decimal("10000000000")
    # Unberichtete Perioden: kein Write trotz gelieferter Werte.
    for pt in ("Q3", "Q4", "FY"):
        assert _rows(db, company, "revenue", pt, year=year) == []
    # net_income: FY geliefert, aber unberichtet -> nichts; berichtete
    # Quartale ohne Beleg -> not_found (rote Zelle).
    assert _rows(db, company, "net_income", "FY", year=year) == []
    for pt in ("Q1", "Q2"):
        (row,) = _rows(db, company, "net_income", pt, year=year)
        assert row.numeric_value is None
        assert row.primary_method == "not_found"
    for pt in ("Q3", "Q4"):
        assert _rows(db, company, "net_income", pt, year=year) == []


def test_running_year_forecast_slot_not_shadowed(db, company, monkeypatch):
    """SAP-Regression: die Guidance-Forecast-Zeile einer unberichteten
    Periode bekommt keinen not_found-Actual daneben (der die Forecast-
    Zelle in der Detail-Seite verschatten wuerde)."""
    year = _running_year(db, company)
    fc = _seed(db, company, "revenue", "FY", 42_000_000_000, year=year,
               is_forecast=True, primary_method="web_guidance")
    _mock_claude(monkeypatch, {"income": {"revenue": {"Q1": _entry(9_000_000_000)}}})

    sr.fetch_statement_research(db, company, year, groups=["income"])
    db.commit()

    fy_rows = _rows(db, company, "revenue", "FY", year=year)
    assert len(fy_rows) == 1
    db.refresh(fc)
    assert fc.is_forecast is True
    assert fc.numeric_value == Decimal("42000000000")
    assert fc.primary_method == "web_guidance"
    # Berichtetes Q2 ohne Beleg bekommt weiterhin den not_found-Stempel.
    assert _rows(db, company, "revenue", "Q2", year=year)[0].primary_method == "not_found"


def test_year_without_reported_periods_skips_calls(db, company, monkeypatch):
    """Noch keine Periode berichtet (Jahr gerade gestartet): kein
    Claude-Call, keine Writes."""
    from datetime import timedelta

    t = date.today() + timedelta(days=330)
    company.fiscal_year_end_month = t.month
    company.fiscal_year_end_day = t.day
    db.commit()
    calls = _mock_claude(monkeypatch, {"income": _income_payload()})

    assert sr.fetch_statement_research(db, company, t.year) == 0
    assert calls == []


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


# --- Bedarfspruefung (Quartals-Anker deckt Gruppen) -------------------------


def _cover_group(db, company, group, skip=(), source_name=None):
    """Alle Basis-Keys der Gruppe fuer FY+Q1-Q4 mit XBRL-provider-Zeilen
    fuellen (wie nach dem ESEF-Anker). Markt-Provider-Zeilen (Bloomberg-
    Label) decken seit dem Kundenentscheid NICHT mehr — siehe
    test_market_covered_group_still_triggers_call."""
    keys = [k for k, _ in sr.STATEMENT_GROUPS[group]
            if k not in sr._ADJUSTED_SIDECARS]
    for key in keys:
        for pt in ("FY", "Q1", "Q2", "Q3", "Q4"):
            if (key, pt) in skip:
                continue
            _seed(db, company, key, pt, 5_000_000_000,
                  primary_method="provider",
                  source_name=source_name or f"ESEF FY{YEAR}")


def test_fully_covered_group_skips_claude_call(db, company, monkeypatch):
    """Gruppe ohne ersetzbare/leere berichtete Zelle (Provider-Anker hat
    alles gedeckt) -> kein Claude-Call fuer diese Gruppe."""
    _cover_group(db, company, "income")
    calls = _mock_claude(monkeypatch, {})

    sr.fetch_statement_research(db, company, YEAR)

    assert [c[2] for c in calls] == ["cashflow", "balance"]


def test_all_groups_covered_zero_claude_calls(db, company, monkeypatch):
    """Steady State (SAP-artig): Provider deckt alle Gruppen -> 0 Calls."""
    for group in ("income", "cashflow", "balance"):
        _cover_group(db, company, group)
    calls = _mock_claude(monkeypatch, {})

    assert sr.fetch_statement_research(db, company, YEAR) == 0
    assert calls == []


def test_single_uncovered_cell_triggers_group_call(db, company, monkeypatch):
    """Eine leere berichtete Zelle (z.B. ebitda Q2 fehlt bei Yahoo)
    genuegt, damit der Gruppen-Call laeuft."""
    _cover_group(db, company, "income", skip={("ebitda", "Q2")})
    _cover_group(db, company, "cashflow")
    _cover_group(db, company, "balance")
    calls = _mock_claude(monkeypatch, {})

    sr.fetch_statement_research(db, company, YEAR)

    assert [c[2] for c in calls] == ["income"]


def test_replaceable_row_still_triggers_group_call(db, company, monkeypatch):
    """statement_research-Zeilen sind ersetzbar — eine gedeckte Gruppe
    aus reinen Recherche-Zeilen wird weiterhin aktualisiert."""
    _cover_group(db, company, "balance")
    row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company.id,
            CompanyValue.value_key == "lt_debt",
            CompanyValue.period_type == "Q1",
        )
        .one()
    )
    row.primary_method = "statement_research"
    db.commit()
    calls = _mock_claude(monkeypatch, {})

    sr.fetch_statement_research(db, company, YEAR, groups=["balance"])

    assert [c[2] for c in calls] == ["balance"]


# --- Migration: Markt-Provider-Zeilen (Yahoo-Altbestand) --------------------


def test_market_covered_group_still_triggers_call(db, company, monkeypatch):
    """Bloomberg-gedeckte Gruppe zaehlt als beduerftig — sonst liefe kein
    Call, der den Markt-Provider-Altbestand ersetzt."""
    _cover_group(db, company, "income", source_name="Bloomberg")
    _cover_group(db, company, "cashflow")
    _cover_group(db, company, "balance")
    calls = _mock_claude(monkeypatch, {})

    sr.fetch_statement_research(db, company, YEAR)

    assert [c[2] for c in calls] == ["income"]


def test_market_provider_row_replaced_by_research(db, company, monkeypatch):
    """Markt-Provider-Zeile (primary_method 'provider', Label 'Bloomberg')
    ist fuer die Recherche ersetzbar — der Berichtswert uebernimmt."""
    old = _seed(db, company, "revenue", "FY", 39_000_000_000,
                primary_method="provider", source_name="Bloomberg")
    _mock_claude(monkeypatch, {"income": _income_payload()})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()
    db.refresh(old)

    assert old.numeric_value == Decimal("40000000000")
    assert old.primary_method == "statement_research"
    assert not (old.source_name or "").startswith("Bloomberg")


def test_esef_provider_row_not_replaced(db, company, monkeypatch):
    """XBRL-Provider-Zeilen (ESEF-/EDGAR-Label) bleiben gesperrt —
    die Erkennung laeuft ueber das source_name-Label."""
    esef = _seed(db, company, "revenue", "FY", 39_000_000_000,
                 primary_method="provider", source_name=f"ESEF FY{YEAR}")
    edgar = _seed(db, company, "net_income", "FY", 2_900_000_000,
                  primary_method="provider", source_name="SEC EDGAR 10-K")
    _mock_claude(monkeypatch, {"income": _income_payload()})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()
    db.refresh(esef)
    db.refresh(edgar)

    assert esef.numeric_value == Decimal("39000000000")
    assert esef.primary_method == "provider"
    assert edgar.numeric_value == Decimal("2900000000")
    assert edgar.primary_method == "provider"


def test_market_provider_cell_is_needy_for_document_stage(db, company):
    """_needy_cells: Bloomberg-Zellen zaehlen trotz Wert als beduerftig;
    ESEF-Zellen nicht."""
    for pt in ("FY", "Q1"):
        _seed(db, company, "net_income", pt, 1_000_000_000,
              primary_method="provider", source_name="Bloomberg")
    _seed(db, company, "net_income", "Q2", 1_000_000_000,
          primary_method="provider", source_name=f"ESEF FY{YEAR}")

    needed = sr._needy_cells(db, company, YEAR, "income",
                             ("FY", "Q1", "Q2", "Q3", "Q4"))

    assert set(needed["net_income"]) == {"FY", "Q1", "Q3", "Q4"}


# --- Yahoo-Cross-Check-Gate -------------------------------------------------


def _mock_refs(monkeypatch, refs, calls=None, error=False):
    """yahoo_reference_map am Quell-Modul mocken (statement_research
    importiert sie pro Aufruf aus provider_anchor)."""
    import app.values.provider_anchor as anchor_mod

    def fake(company_, years):
        if calls is not None:
            calls.append(tuple(years))
        if error:
            raise RuntimeError("yahoo down")
        return refs

    monkeypatch.setattr(anchor_mod, "yahoo_reference_map", fake)


def test_yahoo_gate_discards_outlier_keeps_within_band(db, company, monkeypatch):
    """>35% Abweichung von der Yahoo-Referenz -> verworfen ('Yahoo-Cross-
    Check'); innerhalb des Bandes -> geschrieben. Ohne Referenz (kein
    Map-Eintrag) kein Urteil."""
    _mock_refs(monkeypatch, {
        ("revenue", "FY", YEAR): Decimal("20000000000"),   # Recherche 2x -> raus
        ("net_income", "FY", YEAR): Decimal("2800000000"),  # +7% -> ok
    })
    payload = {
        "revenue": {"FY": _entry(40_000_000_000)},
        "net_income": {"FY": _entry(3_000_000_000)},
        "eps_diluted": {"FY": _entry("3.05")},  # keine Referenz -> ok
    }
    _mock_claude(monkeypatch, {"income": payload})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    assert _rows(db, company, "revenue", "FY")[0].primary_method == "not_found"
    assert _rows(db, company, "net_income", "FY")[0].numeric_value == Decimal("3000000000")
    assert _rows(db, company, "eps_diluted", "FY")[0].numeric_value == Decimal("3.05")


def test_yahoo_gate_per_share_key_same_band(db, company, monkeypatch):
    """Per-Share-Keys laufen mit demselben 35%-Band."""
    _mock_refs(monkeypatch, {("eps_diluted", "FY", YEAR): Decimal("3.00")})
    payload = {"eps_diluted": {"FY": _entry("5.00")}}  # +67% -> raus
    _mock_claude(monkeypatch, {"income": payload})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    assert _rows(db, company, "eps_diluted", "FY")[0].primary_method == "not_found"


def test_yahoo_gate_sign_free_magnitude_compare(db, company, monkeypatch):
    """Betragsvergleich: Yahoo liefert capex roh negativ, die Recherche
    brutto positiv — gleicher Betrag passiert das Gate."""
    _mock_refs(monkeypatch, {("capex", "FY", YEAR): Decimal("-2000000000")})
    payload = {"capex": {"FY": _entry(2_100_000_000)}}
    _mock_claude(monkeypatch, {"cashflow": payload})

    sr.fetch_statement_research(db, company, YEAR, groups=["cashflow"])
    db.commit()

    assert _rows(db, company, "capex", "FY")[0].numeric_value is not None


def test_yahoo_gate_skipped_on_reference_fetch_error(db, company, monkeypatch):
    """Fetch-Fehler der Referenz-Map -> Gate komplett uebersprungen,
    der Lauf schreibt normal weiter."""
    _mock_refs(monkeypatch, {}, error=True)
    payload = {"revenue": {"FY": _entry(40_000_000_000)}}
    _mock_claude(monkeypatch, {"income": payload})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    assert _rows(db, company, "revenue", "FY")[0].numeric_value == Decimal("40000000000")


def test_yahoo_references_fetched_once_per_run(db, company, monkeypatch):
    """Die Referenz-Map wird einmal pro fetch_statement_research-Aufruf
    geholt — nicht pro Gruppe."""
    calls: list = []
    _mock_refs(monkeypatch, {}, calls=calls)
    _mock_claude(monkeypatch, {"income": _income_payload()})

    sr.fetch_statement_research(db, company, YEAR)

    assert calls == [(YEAR,)]


def test_yahoo_gate_complements_prev_year_band(db, company, monkeypatch):
    """Das Gate ersetzt das Vorjahresband NICHT: ein Wert, der die
    Yahoo-Referenz trifft, aber aus dem Vorjahresband faellt, wird
    weiterhin verworfen."""
    _seed(db, company, "revenue", "FY", 10_000_000_000, year=PREV,
          primary_method="statement_research")
    _mock_refs(monkeypatch, {("revenue", "FY", YEAR): Decimal("30000000000")})
    payload = {"revenue": {"FY": _entry(30_000_000_000)}}  # 3x Vorjahr
    _mock_claude(monkeypatch, {"income": payload})

    sr.fetch_statement_research(db, company, YEAR, groups=["income"])
    db.commit()

    assert _rows(db, company, "revenue", "FY")[0].primary_method == "not_found"
