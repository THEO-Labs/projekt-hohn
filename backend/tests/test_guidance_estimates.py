"""Tests fuer die FY-Guidance-Estimates (guidance_estimates.py): EIN
Claude-Call pro US-Firma ersetzt die Two-Stage-Recherche fuers laufende
FY. Hermetisch — der Claude-Call ist via _call_claude gemockt, der
Refresh-Flow via monkeypatch auf routes/guidance_estimates."""
from datetime import date
from decimal import Decimal
from uuid import UUID

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
    user = User(email="guid@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.flush()
    portfolio = Portfolio(name="P", owner_user_id=user.id)
    db.add(portfolio)
    db.flush()
    comp = Company(
        portfolio_id=portfolio.id, name="TestCo", ticker="TST",
        currency="USD", isin="US0001234567",
        fiscal_year_end_month=12, fiscal_year_end_day=31,
    )
    db.add(comp)
    db.commit()
    return comp


def _payload(**overrides):
    base = {
        "revenue": {
            "value": 110_000_000_000, "source": "guidance",
            "quote": "FY revenue guidance of $110 billion",
            "url": "https://example.com/ir/guidance",
        },
        "net_income": {
            "value": 20_000_000_000, "source": "consensus",
            "quote": "Consensus GAAP net income of $20.0B",
            "url": "https://stockanalysis.com/tst",
        },
        "eps_diluted": {
            "value": 4.0, "source": "consensus",
            "quote": "Consensus GAAP diluted EPS of $4.00", "url": None,
        },
        "eps_diluted_non_gaap": {
            "value": 5.0, "source": "consensus",
            "quote": "Non-GAAP EPS consensus $5.00",
            "url": "https://zacks.com/tst",
        },
        "net_income_non_gaap": {
            "value": 25_000_000_000, "source": "consensus",
            "quote": "Adjusted net income consensus $25B", "url": None,
        },
        "operating_cash_flow": {
            "value": 30_000_000_000, "source": "consensus",
            "quote": "OCF consensus $30B", "url": None,
        },
    }
    base.update(overrides)
    return base


def _seed_prev_actuals(db, comp, values: dict[str, str]):
    for key, val in values.items():
        db.add(CompanyValue(
            company_id=comp.id, value_key=key, period_type="FY",
            period_year=PREV_YEAR, numeric_value=Decimal(val),
            is_forecast=False, currency="USD", primary_method="provider",
        ))
    db.commit()


def _seed_shares(db, comp, value: str):
    db.add(CompanyValue(
        company_id=comp.id, value_key="shares_outstanding",
        period_type="SNAPSHOT", period_year=None,
        numeric_value=Decimal(value),
    ))
    db.commit()


def _mock_claude(monkeypatch, payload):
    calls: list[tuple] = []

    def fake(company, year, cost_tracker=None):
        calls.append((company.ticker, year))
        return payload

    monkeypatch.setattr(ge, "_call_claude", fake)
    return calls


def _fy_rows(db, comp, key):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == RUNNING_YEAR,
        )
        .all()
    )


def test_single_call_writes_fy_forecasts_and_sidecars(db, company, monkeypatch):
    """EIN Call schreibt mehrere FY-Forecasts inkl. Non-GAAP-Sidecars in
    numeric_value_adjusted der GAAP-Zeilen."""
    calls = _mock_claude(monkeypatch, _payload())
    _seed_prev_actuals(db, company, {
        "revenue": "100000000000",
        "net_income": "18000000000",
        "operating_cash_flow": "28000000000",
    })
    _seed_shares(db, company, "5000000000")  # eps 4.0 x 5B = 20B = NI

    written = ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert len(calls) == 1
    assert written == 4  # revenue, net_income, eps_diluted, ocf

    rev = _fy_rows(db, company, "revenue")
    assert len(rev) == 1
    assert rev[0].is_forecast is True
    assert rev[0].primary_method == "web_guidance"
    assert rev[0].numeric_value == Decimal("110000000000")
    assert "guidance" in rev[0].source_name
    assert "FY revenue guidance" in rev[0].source_name
    assert rev[0].source_link == "https://example.com/ir/guidance"
    assert rev[0].currency == "USD"

    ni = _fy_rows(db, company, "net_income")[0]
    assert ni.numeric_value == Decimal("20000000000")
    assert ni.numeric_value_adjusted == Decimal("25000000000")
    assert ni.adjustments_source != "Manual"
    assert not ni.adjustments_source.startswith("https://")

    eps = _fy_rows(db, company, "eps_diluted")[0]
    assert eps.numeric_value == Decimal("4.0")
    assert eps.numeric_value_adjusted == Decimal("5.0")
    assert "zacks.com" in eps.adjustments_source


def test_manual_row_stays_untouched(db, company, monkeypatch):
    """Manual-Override-Zeile ist authoritative — der Guidance-Write
    ueberspringt den Key, andere Keys werden trotzdem geschrieben."""
    _mock_claude(monkeypatch, _payload())
    db.add(CompanyValue(
        company_id=company.id, value_key="revenue", period_type="FY",
        period_year=RUNNING_YEAR, numeric_value=Decimal("1"),
        is_forecast=True, manually_overridden=True, source_name="Manual",
    ))
    db.commit()

    written = ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    rev = _fy_rows(db, company, "revenue")
    assert len(rev) == 1
    assert rev[0].numeric_value == Decimal("1")
    assert rev[0].manually_overridden is True
    assert written == 3  # net_income, eps_diluted, ocf
    assert _fy_rows(db, company, "net_income")[0].numeric_value == Decimal("20000000000")


def test_prev_year_gate_discards_outlier_allows_sign_flip(db, company, monkeypatch):
    """>60% Abweichung vom Vorjahres-Actual verwirft den Wert;
    Vorzeichenwechsel (Turnaround) bleibt erlaubt."""
    payload = _payload(
        revenue={
            "value": 300_000_000_000, "source": "consensus",
            "quote": "outlier", "url": None,
        },
    )
    _mock_claude(monkeypatch, payload)
    _seed_prev_actuals(db, company, {
        "revenue": "100000000000",     # 300B = +200% -> Gate
        "net_income": "-5000000000",   # -5B -> +20B: sign flip erlaubt
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert _fy_rows(db, company, "revenue") == []
    ni = _fy_rows(db, company, "net_income")
    assert len(ni) == 1
    assert ni[0].numeric_value == Decimal("20000000000")


def test_eps_ni_consistency_gate(db, company, monkeypatch):
    """eps x shares weicht >20% von net_income ab: beide Werte werden
    verworfen, die uebrigen Keys geschrieben."""
    payload = _payload(
        eps_diluted={
            "value": 10.0, "source": "consensus",
            "quote": "EPS consensus", "url": None,
        },
    )
    _mock_claude(monkeypatch, payload)
    _seed_shares(db, company, "5000000000")  # 10.0 x 5B = 50B vs NI 20B

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert _fy_rows(db, company, "eps_diluted") == []
    assert _fy_rows(db, company, "net_income") == []
    assert len(_fy_rows(db, company, "revenue")) == 1
    assert len(_fy_rows(db, company, "operating_cash_flow")) == 1


def test_unit_gate_discards_sub_million_absolute(db, company, monkeypatch):
    """Absolutwert unter 1 Mio (fehlende Skalierung) wird verworfen."""
    payload = _payload(
        revenue={
            "value": 5000, "source": "guidance",
            "quote": "110 (in millions)", "url": None,
        },
    )
    _mock_claude(monkeypatch, payload)

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert _fy_rows(db, company, "revenue") == []
    assert len(_fy_rows(db, company, "net_income")) == 1


def test_source_must_be_guidance_or_consensus(db, company, monkeypatch):
    """Werte ohne guidance/consensus-Quelle (z.B. Eigen-Extrapolation)
    werden verworfen."""
    payload = _payload(
        revenue={
            "value": 110_000_000_000, "source": "extrapolation",
            "quote": "own YoY trend", "url": None,
        },
    )
    _mock_claude(monkeypatch, payload)

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert _fy_rows(db, company, "revenue") == []
    assert len(_fy_rows(db, company, "net_income")) == 1


def test_non_us_company_no_call(db, company, monkeypatch):
    """Nicht-US-Filer: kein Claude-Call, Rueckgabe 0."""
    company.isin = "DE0001234567"
    db.commit()
    calls = _mock_claude(monkeypatch, _payload())

    assert ge.fetch_guidance_estimates(db, company, RUNNING_YEAR) == 0
    assert calls == []


def test_closed_fy_no_call(db, company, monkeypatch):
    """Abgeschlossenes Geschaeftsjahr: kein Call — das gehoert dem
    Provider-Anker/Two-Stage-Pfad."""
    calls = _mock_claude(monkeypatch, _payload())

    assert ge.fetch_guidance_estimates(db, company, PREV_YEAR) == 0
    assert calls == []


def test_second_run_updates_existing_forecast(db, company, monkeypatch):
    """Idempotenz: zweiter Lauf aktualisiert die bestehende Forecast-Zeile
    (kein Duplikat, Sidecar bleibt ueberschreibbar)."""
    _mock_claude(monkeypatch, _payload())
    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    updated = _payload()
    updated["net_income"]["value"] = 21_000_000_000
    updated["net_income_non_gaap"]["value"] = 26_000_000_000
    _mock_claude(monkeypatch, updated)
    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    ni = _fy_rows(db, company, "net_income")
    assert len(ni) == 1
    assert ni[0].numeric_value == Decimal("21000000000")
    assert ni[0].numeric_value_adjusted == Decimal("26000000000")


# --- Refresh-Verdrahtung (routes.refresh_company_values) ------------------


def _setup_refresh(client, db, email, isin):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]
    c = client.post(
        f"/api/portfolios/{pid}/companies",
        json={"name": "TestCo", "ticker": "TST", "currency": "USD"},
    ).json()
    cid = UUID(c["id"])
    comp = db.get(Company, cid)
    comp.isin = isin
    comp.fiscal_year_end_month = 12
    comp.fiscal_year_end_day = 31
    db.commit()
    return cid


def _patch_refresh_env(monkeypatch):
    """Refresh-Umfeld isolieren: kein Backfill, keine Calculations, kein
    Prev-Year-Prefetch. Two-Stage-Prozessor und fetch_guidance_estimates
    werden durch zaehlende Mocks ersetzt."""
    import app.values.routes as routes

    monkeypatch.setattr(routes, "_prev_year_needs_backfill",
                        lambda db_, cid_, k, y: False)
    monkeypatch.setattr(routes, "_run_and_persist_calculations", lambda *a, **kw: [])
    monkeypatch.setattr(routes, "_ensure_previous_year_inputs", lambda *a, **kw: None)

    two_stage_keys: list[str] = []

    def fake_process(db, key, company, company_id, payload, updated, target_year=None):
        two_stage_keys.append(key)
        return False

    monkeypatch.setattr(routes, "_process_one_key_via_two_stage", fake_process)

    guidance_calls: list[tuple] = []

    def fake_fetch(db, company, year, cost_tracker=None):
        guidance_calls.append((company.ticker, year))
        return 0

    monkeypatch.setattr(ge, "fetch_guidance_estimates", fake_fetch)
    return two_stage_keys, guidance_calls


def test_us_refresh_calls_guidance_once_and_skips_estimate_keys(client, db, monkeypatch):
    """US-Filer, laufendes FY: Estimate-Keys laufen NICHT durch Two-Stage,
    fetch_guidance_estimates wird genau EINMAL aufgerufen. Nicht abgedeckte
    Keys (Balance-Sheet) laufen weiter durch Two-Stage."""
    cid = _setup_refresh(client, db, "wire-us@example.com", "US0001234567")
    two_stage_keys, guidance_calls = _patch_refresh_env(monkeypatch)

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={
            "keys": ["revenue", "net_income", "cash_and_equivalents"],
            "period_type": "FY", "period_year": RUNNING_YEAR,
        },
    )

    assert r.status_code == 200
    assert guidance_calls == [("TST", RUNNING_YEAR)]
    assert two_stage_keys == ["cash_and_equivalents"]


def test_us_refresh_closed_year_uses_two_stage(client, db, monkeypatch):
    """US-Filer, abgeschlossenes Jahr: kein Guidance-Call, alle Keys
    unveraendert durch Two-Stage."""
    cid = _setup_refresh(client, db, "wire-closed@example.com", "US0001234567")
    two_stage_keys, guidance_calls = _patch_refresh_env(monkeypatch)

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={
            "keys": ["revenue", "net_income"],
            "period_type": "FY", "period_year": PREV_YEAR,
        },
    )

    assert r.status_code == 200
    assert guidance_calls == []
    assert two_stage_keys == ["revenue", "net_income"]


def test_non_us_refresh_uses_two_stage(client, db, monkeypatch):
    """Nicht-US-Firma: kein Guidance-Call, Two-Stage laeuft fuer alle Keys."""
    cid = _setup_refresh(client, db, "wire-eu@example.com", "DE0001234567")
    two_stage_keys, guidance_calls = _patch_refresh_env(monkeypatch)

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={
            "keys": ["revenue", "net_income"],
            "period_type": "FY", "period_year": RUNNING_YEAR,
        },
    )

    assert r.status_code == 200
    assert guidance_calls == []
    assert two_stage_keys == ["revenue", "net_income"]
