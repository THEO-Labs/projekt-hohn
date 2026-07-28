"""Gap-Fill: fill_portfolio_gaps-Statistik, not_found-Platzhalter,
Vollstaendigkeits-Report — Research komplett gemockt."""

from decimal import Decimal
from uuid import UUID

from app.auth.models import User
from app.auth.security import hash_password
from app.values.models import CompanyValue


def _setup(client, db, tickers=("ADS.DE",)):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="gap@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": "gap@example.com", "password": "pw1234"})
    pid = client.post("/api/portfolios", json={"name": "DAX"}).json()["id"]
    cids = {}
    for t in tickers:
        r = client.post(
            f"/api/portfolios/{pid}/companies",
            json={"name": t, "ticker": t, "currency": "EUR"},
        )
        cids[t] = UUID(r.json()["id"])
    return UUID(pid), cids


def _api_keys(db):
    import scripts.fill_gaps as fg
    return fg._expected_api_keys(db)


def _no_recalc(monkeypatch):
    # Recalc-Engine ist nicht Gegenstand dieser Tests.
    import app.values.routes as routes
    monkeypatch.setattr(routes, "_run_and_persist_calculations",
                        lambda db, cid, pt, year: None)


def test_fill_portfolio_gaps_fills_and_counts(client, db, monkeypatch):
    import scripts.fill_gaps as fg

    pid, cids = _setup(client, db)
    cid = cids["ADS.DE"]
    keys = _api_keys(db)
    empty_key = keys[1]
    fail_key = keys[2]

    # Erster Key ist bereits vollstaendig -> darf nicht recherchiert werden.
    for p in fg.PERIODS:
        db.add(CompanyValue(company_id=cid, value_key=keys[0], period_year=2026,
                            period_type=p, numeric_value=Decimal("1")))
    db.commit()

    researched = []

    def fake_research(ticker, company_name, value_key, year, currency, mode,
                      quarter=None, prev_year_fy_hint=None, **kw):
        researched.append((value_key, year))
        if value_key == fail_key:
            raise RuntimeError("research kaputt")
        return {"value_key": value_key}

    def fake_apply(db_, company_id, value_key, year, result, currency=None):
        if value_key == empty_key:
            return []
        row = CompanyValue(company_id=company_id, value_key=value_key,
                           period_year=year, period_type="FY",
                           numeric_value=Decimal("42"),
                           primary_method="two_stage_verified")
        db_.add(row)
        return [row]

    monkeypatch.setattr(fg, "research_two_stage", fake_research)
    monkeypatch.setattr(fg, "apply_to_db", fake_apply)
    _no_recalc(monkeypatch)

    lines = []
    stats = fg.fill_portfolio_gaps(db, pid, [2026], progress_cb=lines.append)

    assert stats == {
        "combos": len(keys) - 1,
        "filled": len(keys) - 3,
        "empty": 1,
        "failed": 1,
    }
    # Vollstaendiger Key wurde uebersprungen, alle anderen genau einmal.
    assert (keys[0], 2026) not in researched
    assert len(researched) == len(keys) - 1
    # Recherchierte Keys haben jetzt eine FY-Zeile.
    filled_key = keys[3]
    row = db.query(CompanyValue).filter(
        CompanyValue.company_id == cid,
        CompanyValue.value_key == filled_key,
        CompanyValue.period_type == "FY",
        CompanyValue.period_year == 2026,
    ).one()
    assert row.numeric_value == Decimal("42")
    # Log-Zeilen kamen ueber progress_cb.
    assert any(line.startswith("gap-fill:") for line in lines)
    assert any("FAILED" in line for line in lines)
    assert any(line.startswith("gap-fill done:") for line in lines)


def test_fill_portfolio_gaps_ticker_filter(client, db, monkeypatch):
    import scripts.fill_gaps as fg

    pid, _cids = _setup(client, db, tickers=("ADS.DE", "SAP.DE"))
    keys = _api_keys(db)
    seen = set()

    def fake_research(ticker, **kw):
        seen.add(ticker)
        return {}

    monkeypatch.setattr(fg, "research_two_stage", lambda ticker, **kw: fake_research(ticker, **kw))
    monkeypatch.setattr(fg, "apply_to_db", lambda db_, cid, key, year, res, currency=None: [])
    _no_recalc(monkeypatch)

    stats = fg.fill_portfolio_gaps(db, pid, [2026], ticker="SAP.DE")
    assert seen == {"SAP.DE"}
    assert stats["combos"] == len(keys)
    assert stats["empty"] == len(keys)
    assert stats["filled"] == 0


def test_collect_missing_combos_not_found_placeholder_counts_as_missing(client, db):
    """not_found-Platzhalter (numeric_value NULL) sind KEIN 'present' —
    spaetere Batches muessen die Zelle wieder nachrecherchieren duerfen."""
    import scripts.fill_gaps as fg

    pid, cids = _setup(client, db)
    cid = cids["ADS.DE"]
    keys = _api_keys(db)
    ph_key, null_key = keys[0], keys[1]

    # ph_key: Quartale mit Wert, FY ist not_found-Platzhalter -> FY fehlt.
    for p in ("Q1", "Q2", "Q3", "Q4"):
        db.add(CompanyValue(company_id=cid, value_key=ph_key, period_year=2026,
                            period_type=p, numeric_value=Decimal("1")))
    db.add(CompanyValue(company_id=cid, value_key=ph_key, period_year=2026,
                        period_type="FY", numeric_value=None,
                        primary_method="not_found",
                        source_name=fg.NOT_FOUND_SOURCE))
    # null_key: NULL-Wert-Zeile OHNE not_found-Methode zaehlt weiter als
    # vorhanden (kein Auto-Rewrite fremder Leerzeilen).
    for p in ("Q1", "Q2", "Q3", "Q4"):
        db.add(CompanyValue(company_id=cid, value_key=null_key, period_year=2026,
                            period_type=p, numeric_value=Decimal("1")))
    db.add(CompanyValue(company_id=cid, value_key=null_key, period_year=2026,
                        period_type="FY", numeric_value=None))
    db.commit()

    todo = fg.collect_missing_combos(db, pid, [2026])
    combos = {(c.ticker, k, y): m for c, k, y, m in todo}
    assert combos[("ADS.DE", ph_key, 2026)] == ["FY"]
    assert ("ADS.DE", null_key, 2026) not in combos


def test_fill_portfolio_gaps_survives_prev_year_actual_forecast_pair(client, db, monkeypatch):
    """Actual+Forecast-Paar fuer FY N-1 darf die Gap-Fill-Phase nicht
    abbrechen (frueher: MultipleResultsFound ausserhalb des per-Combo-try).
    Der prev_year_fy_hint kommt aus der Actual-Zeile."""
    import scripts.fill_gaps as fg

    pid, cids = _setup(client, db)
    cid = cids["ADS.DE"]
    keys = _api_keys(db)
    gap_key = keys[0]

    # Alle anderen Keys komplett fuellen -> genau eine Recherche.
    for k in keys[1:]:
        for p in fg.PERIODS:
            db.add(CompanyValue(company_id=cid, value_key=k, period_year=2026,
                                period_type=p, numeric_value=Decimal("1")))
    db.add(CompanyValue(company_id=cid, value_key=gap_key, period_year=2025,
                        period_type="FY", numeric_value=Decimal("200"),
                        is_forecast=True))
    db.add(CompanyValue(company_id=cid, value_key=gap_key, period_year=2025,
                        period_type="FY", numeric_value=Decimal("100"),
                        is_forecast=False))
    db.commit()

    hints = []

    def fake_research(ticker, company_name, value_key, year, currency, mode,
                      quarter=None, prev_year_fy_hint=None, **kw):
        hints.append(prev_year_fy_hint)
        return {}

    monkeypatch.setattr(fg, "research_two_stage", fake_research)
    monkeypatch.setattr(fg, "apply_to_db",
                        lambda db_, c, k, y, r, currency=None: [])
    _no_recalc(monkeypatch)

    stats = fg.fill_portfolio_gaps(db, pid, [2026])
    assert stats == {"combos": 1, "filled": 0, "empty": 1, "failed": 0}
    assert hints == [Decimal("100")]


def test_write_not_found_placeholders(client, db):
    import scripts.fill_gaps as fg

    pid, cids = _setup(client, db, tickers=("ADS.DE", "DBK.DE"))
    keys = _api_keys(db)
    assert "ebitda" in keys

    # Eine Zelle existiert schon (mit Wert) -> kein Platzhalter dort.
    db.add(CompanyValue(company_id=cids["ADS.DE"], value_key=keys[0],
                        period_year=2026, period_type="Q1",
                        numeric_value=Decimal("5")))
    db.commit()

    created = fg.write_not_found_placeholders(db, pid, [2026])

    n_periods = len(fg.PERIODS)
    expected = (
        len(keys) * n_periods - 1          # ADS.DE, eine Zelle war belegt
        + (len(keys) - 1) * n_periods      # DBK.DE ohne ebitda
    )
    assert created == expected

    # Platzhalter-Felder korrekt gesetzt.
    ph = db.query(CompanyValue).filter(
        CompanyValue.company_id == cids["ADS.DE"],
        CompanyValue.value_key == keys[0],
        CompanyValue.period_type == "FY",
        CompanyValue.period_year == 2026,
    ).one()
    assert ph.numeric_value is None
    assert ph.primary_method == "not_found"
    assert ph.source_name == fg.NOT_FOUND_SOURCE
    assert ph.currency == "EUR"  # Company-Waehrung, kein NULL-Label
    assert ph.fetched_at is not None
    assert ph.last_refresh_attempt is not None

    # Vorhandene Zeile wurde nicht dupliziert.
    n_q1 = db.query(CompanyValue).filter(
        CompanyValue.company_id == cids["ADS.DE"],
        CompanyValue.value_key == keys[0],
        CompanyValue.period_type == "Q1",
        CompanyValue.period_year == 2026,
    ).count()
    assert n_q1 == 1

    # Banken-EBITDA bleibt komplett leer.
    n_bank_ebitda = db.query(CompanyValue).filter(
        CompanyValue.company_id == cids["DBK.DE"],
        CompanyValue.value_key == "ebitda",
    ).count()
    assert n_bank_ebitda == 0

    # Idempotent: zweiter Lauf legt nichts mehr an.
    assert fg.write_not_found_placeholders(db, pid, [2026]) == 0


def test_completeness_report(client, db):
    import scripts.fill_gaps as fg

    pid, cids = _setup(client, db, tickers=("ADS.DE", "DBK.DE"))
    keys = _api_keys(db)
    key0 = keys[0]

    # ADS.DE: key0 Q1 mit Wert, key0 FY als not_found-Platzhalter.
    db.add(CompanyValue(company_id=cids["ADS.DE"], value_key=key0,
                        period_year=2026, period_type="Q1",
                        numeric_value=Decimal("5")))
    db.add(CompanyValue(company_id=cids["ADS.DE"], value_key=key0,
                        period_year=2026, period_type="FY",
                        numeric_value=None, primary_method="not_found",
                        source_name=fg.NOT_FOUND_SOURCE))
    db.commit()

    report = fg.build_completeness_report(db, pid, [2026])
    assert report["years"] == [2026]
    per_key = report["per_key"]
    assert set(per_key) == set(keys)

    n_periods = len(fg.PERIODS)
    s0 = per_key[key0]
    assert s0["expected"] == 2 * n_periods
    assert s0["with_value"] == 1
    assert s0["not_found"] == 1
    assert s0["excluded"] == 0

    se = per_key["ebitda"]
    assert se["expected"] == n_periods       # nur ADS.DE
    assert se["excluded"] == n_periods       # DBK.DE strukturell leer
    assert se["with_value"] == 0
    assert se["not_found"] == 0
