"""FY-only-Backfill fuer das Jahr N-2 im FY-Refresh: die H-Rendite des
Vorjahres (N-1) braucht die FY-N-2-Anker (net_income/eps/revenue +
Bilanzkomponenten). Kriterium: net_income FY N-2 leer/ersetzbar.
Nicht-US: fetch_statement_research(periods=('FY',)); US: EDGAR-FY-Anker
fuer die Kern-Keys. Danach derive_net_debt_from_components(N-2) und ein
gezielter Kennzahlen-Lauf fuer N-1. Hermetisch — alle Recherche-/Anker-
Pfade sind gemockt."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from app.auth.models import User
from app.auth.security import hash_password
from app.values.models import CompanyValue
from app.values.routes import _N2_FY_ANCHOR_KEYS, _n2_fy_anchor_missing

YEAR = date.today().year
N2 = YEAR - 2


def _setup(client, db, email="n2backfill@example.com", isin=None):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]
    # Firma direkt per ORM (API-Neuanlage gesperrt: ISIN-Pflicht +
    # Nicht-US-Block). Default ohne ISIN -> Nicht-US/Statement-Pfad;
    # mit isin=US... -> US-EDGAR-Anker-Pfad.
    from app.companies.models import Company
    company = Company(portfolio_id=UUID(pid), name="TestCo", ticker="TST",
                      currency="EUR", isin=isin)
    db.add(company)
    db.commit()
    return company.id


def _fy_row(cid, key, year, pm, value=Decimal("1000000000"), **kw):
    return CompanyValue(
        company_id=cid, value_key=key, period_type="FY", period_year=year,
        numeric_value=value, primary_method=pm,
        is_forecast=kw.pop("is_forecast", False), **kw,
    )


# --- Kriterium ---------------------------------------------------------------


def test_criterion_no_row_missing(client, db):
    cid = _setup(client, db)
    assert _n2_fy_anchor_missing(db, cid, N2) is True


def test_criterion_statement_and_provider_rows_are_fresh(client, db):
    cid = _setup(client, db)
    db.add(_fy_row(cid, "net_income", N2, "statement_research"))
    db.commit()
    assert _n2_fy_anchor_missing(db, cid, N2) is False


def test_criterion_replaceable_rows_missing(client, db):
    """calculated/web_*-Reste und wertlose Zeilen zaehlen als fehlend."""
    cid = _setup(client, db)
    db.add(_fy_row(cid, "net_income", N2, "calculated"))
    db.commit()
    assert _n2_fy_anchor_missing(db, cid, N2) is True


def test_criterion_manual_row_is_authoritative(client, db):
    cid = _setup(client, db)
    db.add(_fy_row(cid, "net_income", N2, None, manually_overridden=True))
    db.commit()
    assert _n2_fy_anchor_missing(db, cid, N2) is False


# --- Refresh-Wiring ----------------------------------------------------------


def _patch_refresh_env(monkeypatch, stmt_return_for_n2=1):
    import app.values.consistency as cons
    import app.values.guidance_estimates as ge
    import app.values.routes as routes
    import app.values.statement_research as sr

    monkeypatch.setattr(routes, "_prev_year_needs_backfill",
                        lambda db_, cid_, k, y: False)
    monkeypatch.setattr(routes, "_ensure_previous_year_inputs",
                        lambda *a, **kw: None)
    monkeypatch.setattr(ge, "fetch_guidance_estimates",
                        lambda *a, **kw: 0)

    calc_calls: list[tuple] = []

    def fake_calc(db_, cid_, period_type, period_year):
        calc_calls.append((period_type, period_year))
        return []

    monkeypatch.setattr(routes, "_run_and_persist_calculations", fake_calc)

    stmt_calls: list[tuple] = []

    def fake_statement(db_, company, year, cost_tracker=None, groups=None,
                       periods=None):
        stmt_calls.append((year, periods, tuple(groups or ())))
        return stmt_return_for_n2 if periods == ("FY",) else 0

    monkeypatch.setattr(sr, "fetch_statement_research", fake_statement)

    nd_calls: list[int] = []
    monkeypatch.setattr(cons, "derive_net_debt_from_components",
                        lambda db_, cid_, y: nd_calls.append(y))
    return stmt_calls, nd_calls, calc_calls


def test_refresh_runs_fy_only_backfill_for_empty_n2(client, db, monkeypatch):
    """N-2 leer -> einmaliger FY-only-Lauf (periods=('FY',)), danach
    net_debt-Ableitung fuer N-2 und Kennzahlen-Lauf fuer N-1."""
    cid = _setup(client, db)
    stmt_calls, nd_calls, calc_calls = _patch_refresh_env(monkeypatch)

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={"keys": ["net_income"], "period_type": "FY", "period_year": YEAR},
    )

    assert r.status_code == 200
    assert (N2, ("FY",), ()) in stmt_calls
    # Der normale Statement-Lauf des Zieljahres bleibt (ohne periods).
    assert any(y == YEAR and p is None for y, p, _ in stmt_calls)
    assert N2 in nd_calls
    assert ("FY", YEAR - 1) in calc_calls
    assert ("FY", YEAR) in calc_calls


def test_refresh_skips_backfill_when_n2_fresh(client, db, monkeypatch):
    """N-2 vorhanden (statement_research-Zeile mit Wert) -> kein Call,
    kein gezielter N-1-Kennzahlen-Lauf."""
    cid = _setup(client, db)
    db.add(_fy_row(cid, "net_income", N2, "statement_research"))
    db.commit()
    stmt_calls, nd_calls, calc_calls = _patch_refresh_env(monkeypatch)

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={"keys": ["net_income"], "period_type": "FY", "period_year": YEAR},
    )

    assert r.status_code == 200
    assert not any(p == ("FY",) for _, p, _ in stmt_calls)
    assert N2 not in nd_calls
    assert ("FY", YEAR - 1) not in calc_calls


def test_refresh_no_n1_recompute_when_backfill_writes_nothing(client, db, monkeypatch):
    """FY-only-Lauf ohne geschriebene Zeilen (Recherche fand nichts):
    keine Folge-Ableitungen, kein N-1-Kennzahlen-Lauf."""
    cid = _setup(client, db)
    stmt_calls, nd_calls, calc_calls = _patch_refresh_env(
        monkeypatch, stmt_return_for_n2=0,
    )

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={"keys": ["net_income"], "period_type": "FY", "period_year": YEAR},
    )

    assert r.status_code == 200
    assert (N2, ("FY",), ()) in stmt_calls  # Versuch lief
    assert N2 not in nd_calls
    assert ("FY", YEAR - 1) not in calc_calls


def test_us_refresh_anchors_n2_fy_via_edgar(client, db, monkeypatch):
    """US-Filer: N-2-FY kommt ueber den EDGAR-FY-Anker (kein LLM-Call,
    kein Statement-Lauf), danach net_debt(N-2) + Kennzahlen N-1."""
    import app.values.provider_anchor as anchor_mod

    cid = _setup(client, db, isin="US0001234567")
    stmt_calls, nd_calls, calc_calls = _patch_refresh_env(monkeypatch)

    anchored: list[tuple] = []

    def fake_anchor(db_, company, key, year):
        anchored.append((key, year))
        return True

    monkeypatch.setattr(anchor_mod, "anchor_fy_with_provider", fake_anchor)

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={"keys": ["net_income"], "period_type": "FY", "period_year": YEAR},
    )

    assert r.status_code == 200
    assert anchored == [(k, N2) for k in _N2_FY_ANCHOR_KEYS]
    assert not any(p == ("FY",) for _, p, _ in stmt_calls)
    assert N2 in nd_calls
    assert ("FY", YEAR - 1) in calc_calls
