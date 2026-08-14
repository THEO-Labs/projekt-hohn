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
            "value": 20_000_000_000, "source": "consensus", "basis": "gaap",
            "quote": "Consensus GAAP net income of $20.0B",
            "url": "https://stockanalysis.com/tst",
        },
        "eps_diluted": {
            "value": 4.0, "source": "consensus", "basis": "gaap",
            "quote": "Consensus GAAP diluted EPS of $4.00", "url": None,
        },
        "eps_diluted_non_gaap": {
            "value": 5.0, "source": "consensus", "basis": "non_gaap",
            "quote": "Non-GAAP EPS consensus $5.00",
            "url": "https://zacks.com/tst",
        },
        "net_income_non_gaap": {
            "value": 25_000_000_000, "source": "consensus", "basis": "non_gaap",
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

    def fake(company, year, cost_tracker=None, open_quarter=None):
        calls.append((company.ticker, year, open_quarter))
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

    written = ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert len(calls) == 1
    assert calls[0][2] is None  # FY-only: kein open_quarter
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


def test_non_us_company_gets_call(db, company, monkeypatch):
    """Nicht-US-Filer sind vom US-Gate befreit (DE-Umbau): der Guidance-
    Call laeuft und schreibt Forecasts wie fuer US-Filer."""
    company.isin = "DE0001234567"
    db.commit()
    calls = _mock_claude(monkeypatch, _payload())

    written = ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert len(calls) == 1
    assert written > 0
    rev = _fy_rows(db, company, "revenue")
    assert len(rev) == 1 and rev[0].is_forecast is True


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


# --- NI/EPS-Ableitung aus Operating-Profit-Guidance (SAP-Muster) ----------


def _op_payload(basis="non_gaap", tax_value=0.29, with_tax=True, **overrides):
    """Payload einer Firma, die kein NI/EPS guidet — nur Operating-Profit-
    Guidance plus (optional) den berichteten effektiven Steuersatz."""
    base = {
        "operating_profit_guidance": {
            "value": 12_000_000_000, "source": "guidance", "basis": basis,
            "reasoning": "Operating profit guidance midpoint of 12.0B",
            "url": "https://example.com/ir/outlook",
        },
    }
    if with_tax:
        base["effective_tax_rate"] = {
            "value": tax_value, "source": None,
            "reasoning": "Last reported effective tax rate of 29%",
            "url": "https://example.com/ir/annual-report",
        }
    base.update(overrides)
    return base


def _helper_rows(db, comp):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key.in_(
                ["operating_profit_guidance", "effective_tax_rate"],
            ),
        )
        .all()
    )


def test_op_guidance_non_ifrs_derives_adjusted_ni_and_eps_sidecars(db, company, monkeypatch):
    """SAP-Muster: non-IFRS-OP-Guidance x (1 - Steuersatz) fuellt den
    adjusted-Sidecar von net_income; eps adjusted folgt als NI / shares.
    Die Hilfsgroessen werden nie als eigene DB-Keys persistiert."""
    _mock_claude(monkeypatch, _op_payload())
    _seed_shares(db, company, "1000000000")

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    ni = _fy_rows(db, company, "net_income")
    assert len(ni) == 1
    assert ni[0].numeric_value is None  # kein GAAP-Wert erfunden
    assert ni[0].numeric_value_adjusted == Decimal("8520000000")
    assert "Abgeleitet: Operating-Profit-Guidance x (1 - Steuersatz)" in ni[0].adjustments_source
    assert "example.com/ir/outlook" in ni[0].adjustments_source

    eps = _fy_rows(db, company, "eps_diluted")
    assert len(eps) == 1
    assert eps[0].numeric_value is None
    assert eps[0].numeric_value_adjusted == Decimal("8.52")
    assert "Abgeleitet: Net-Income-Schaetzung / Aktienzahl" in eps[0].adjustments_source

    assert _helper_rows(db, company) == []


def test_op_guidance_gaap_basis_writes_gaap_slots(db, company, monkeypatch):
    """GAAP-OP-Guidance -> Ableitung landet im GAAP-Slot (numeric_value),
    eps folgt als NI / shares; Vorjahresband-Gate wird durchlaufen."""
    _mock_claude(monkeypatch, _op_payload(basis="gaap"))
    _seed_shares(db, company, "1000000000")
    _seed_prev_actuals(db, company, {"net_income": "7000000000"})

    written = ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert written == 2  # net_income + eps_diluted
    ni = _fy_rows(db, company, "net_income")[0]
    assert ni.numeric_value == Decimal("8520000000")
    assert ni.is_forecast is True
    assert ni.primary_method == "web_guidance"
    assert "Abgeleitet: Operating-Profit-Guidance" in ni.source_name

    eps = _fy_rows(db, company, "eps_diluted")[0]
    assert eps.numeric_value == Decimal("8.52")
    assert "Abgeleitet: Net-Income-Schaetzung" in eps.source_name


def test_op_guidance_percent_tax_rate_normalized(db, company, monkeypatch):
    """Steuersatz als Prozentzahl (29 statt 0.29) wird toleriert."""
    _mock_claude(monkeypatch, _op_payload(basis="gaap", tax_value=29))

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    ni = _fy_rows(db, company, "net_income")
    assert len(ni) == 1
    assert ni[0].numeric_value == Decimal("8520000000")


def test_op_guidance_without_tax_rate_no_derivation(db, company, monkeypatch):
    """Ohne effektiven Steuersatz keine NI-Ableitung — es bleibt leer
    statt geraten."""
    _mock_claude(monkeypatch, _op_payload(with_tax=False))
    _seed_shares(db, company, "1000000000")

    assert ge.fetch_guidance_estimates(db, company, RUNNING_YEAR) == 0
    db.commit()

    assert _fy_rows(db, company, "net_income") == []
    assert _fy_rows(db, company, "eps_diluted") == []


def test_op_derivation_respects_prev_year_gate(db, company, monkeypatch):
    """Die Ableitung unterliegt den bestehenden Gates: liegt das
    abgeleitete NI ausserhalb des 40-160%-Vorjahresbands, wird es
    verworfen."""
    _mock_claude(monkeypatch, _op_payload(basis="gaap"))
    _seed_prev_actuals(db, company, {"net_income": "3000000000"})  # +184%

    assert ge.fetch_guidance_estimates(db, company, RUNNING_YEAR) == 0
    db.commit()

    assert _fy_rows(db, company, "net_income") == []


def test_op_derivation_does_not_override_direct_consensus(db, company, monkeypatch):
    """Direkter NI-adj-Konsens hat Vorrang — die OP-Ableitung fuellt nur
    leere Slots."""
    payload = _op_payload(
        net_income_non_gaap={
            "value": 25_000_000_000, "source": "consensus", "basis": "non_gaap",
            "quote": "Adjusted net income consensus $25B", "url": None,
        },
    )
    _mock_claude(monkeypatch, payload)

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    ni = _fy_rows(db, company, "net_income")
    assert len(ni) == 1
    assert ni[0].numeric_value_adjusted == Decimal("25000000000")


def test_eps_derived_from_direct_ni_consensus(db, company, monkeypatch):
    """Auch ohne OP-Guidance: fehlt der EPS-Konsens, aber NI liegt vor,
    wird eps = NI / shares (SNAPSHOT) abgeleitet."""
    payload = {
        "net_income": {
            "value": 20_000_000_000, "source": "consensus", "basis": "gaap",
            "quote": "Consensus GAAP net income of $20.0B", "url": None,
        },
    }
    _mock_claude(monkeypatch, payload)
    _seed_shares(db, company, "5000000000")

    written = ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert written == 2
    eps = _fy_rows(db, company, "eps_diluted")[0]
    assert eps.numeric_value == Decimal("4")
    assert "Abgeleitet: Net-Income-Schaetzung" in eps.source_name


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
    """Refresh-Umfeld isolieren: kein Backfill, kein Prev-Year-Prefetch.
    EDGAR-Anker, fetch_guidance_estimates, fetch_statement_research,
    Calculations und der historische MCap-Fetch werden durch zaehlende
    Mocks ersetzt."""
    import app.values.routes as routes
    import app.values.statement_research as sr

    monkeypatch.setattr(routes, "_prev_year_needs_backfill",
                        lambda db_, cid_, k, y: False)
    monkeypatch.setattr(routes, "_ensure_previous_year_inputs", lambda *a, **kw: None)

    calc_calls: list[tuple] = []

    def fake_calc(db_, cid_, period_type, period_year):
        calc_calls.append((period_type, period_year))
        return []

    monkeypatch.setattr(routes, "_run_and_persist_calculations", fake_calc)

    mcap_calls: list[int] = []
    monkeypatch.setattr(
        routes, "_fetch_and_store_historical_mcap",
        lambda db_, ticker_, cid_, year: mcap_calls.append(year),
    )

    anchor_keys: list[str] = []

    def fake_anchor(db, key, company, company_id, updated, year):
        anchor_keys.append(key)
        return False

    monkeypatch.setattr(routes, "_anchor_us_key_periods", fake_anchor)

    guidance_calls: list[tuple] = []

    def fake_fetch(db, company, year, cost_tracker=None, open_quarter=None):
        guidance_calls.append((company.ticker, year))
        return 0

    monkeypatch.setattr(ge, "fetch_guidance_estimates", fake_fetch)

    statement_calls: list[tuple] = []

    def fake_statement(db, company, year, cost_tracker=None, groups=None):
        statement_calls.append((company.ticker, year, tuple(groups or ())))
        return 0

    monkeypatch.setattr(sr, "fetch_statement_research", fake_statement)
    return guidance_calls, anchor_keys, statement_calls, calc_calls, mcap_calls


def test_us_refresh_calls_guidance_once_and_skips_estimate_keys(client, db, monkeypatch):
    """US-Filer, laufendes FY: Estimate-Keys laufen NICHT durch den
    EDGAR-Anker, fetch_guidance_estimates wird genau EINMAL aufgerufen.
    Balance-Keys (cash_and_equivalents) uebernimmt die Bilanz-
    Fortschreibung. Nicht abgedeckte Keys (net_debt) laufen fuer
    US-Filer durch den EDGAR-Anker."""
    cid = _setup_refresh(client, db, "wire-us@example.com", "US0001234567")
    guidance_calls, anchor_keys, statement_calls, *_ = _patch_refresh_env(monkeypatch)

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={
            "keys": ["revenue", "net_income", "cash_and_equivalents", "net_debt"],
            "period_type": "FY", "period_year": RUNNING_YEAR,
        },
    )

    assert r.status_code == 200
    assert guidance_calls == [("TST", RUNNING_YEAR)]
    assert anchor_keys == ["net_debt"]
    assert statement_calls == []  # Statement-Recherche ist Nicht-US-only


def test_refresh_passes_all_open_quarters(client, db, monkeypatch):
    """Das Refresh-Wiring uebergibt ALLE offenen Quartale als Liste
    (SPGI/SAP-Muster: Q3+Q4 offen) — frueher None bei mehr als einem
    offenen Quartal, die H2-Quartalszellen blieben leer.
    _quarter_reported ist gemockt (hermetisch, kein Datums-Drift)."""
    import app.values.consistency as cons
    cid = _setup_refresh(client, db, "wire-openq@example.com", "US0001234567")
    _patch_refresh_env(monkeypatch)
    monkeypatch.setattr(
        cons, "_quarter_reported",
        lambda company, year, q, subs_cache: q in ("Q1", "Q2"),
    )
    seen: list = []

    def fake_fetch(db_, company, year, cost_tracker=None, open_quarter=None):
        seen.append(open_quarter)
        return 0

    monkeypatch.setattr(ge, "fetch_guidance_estimates", fake_fetch)

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={
            "keys": ["revenue"],
            "period_type": "FY", "period_year": RUNNING_YEAR,
        },
    )

    assert r.status_code == 200
    assert seen == [["Q3", "Q4"]]


def test_us_refresh_closed_year_no_guidance_call(client, db, monkeypatch):
    """US-Filer, abgeschlossenes Jahr: kein Guidance-Call, alle Keys
    durch den EDGAR-Anker."""
    cid = _setup_refresh(client, db, "wire-closed@example.com", "US0001234567")
    guidance_calls, anchor_keys, _statement_calls, *_ = _patch_refresh_env(monkeypatch)

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={
            "keys": ["revenue", "net_income"],
            "period_type": "FY", "period_year": PREV_YEAR,
        },
    )

    assert r.status_code == 200
    assert guidance_calls == []
    assert anchor_keys == ["revenue", "net_income"]


def test_non_us_refresh_uses_statement_and_guidance(client, db, monkeypatch):
    """Nicht-US-Firma, laufendes FY (DE-Umbau): EIN Statement-Lauf (nur
    die Gruppen der angefragten Keys) plus EIN Guidance-Call."""
    cid = _setup_refresh(client, db, "wire-eu@example.com", "DE0001234567")
    guidance_calls, anchor_keys, statement_calls, *_ = _patch_refresh_env(monkeypatch)

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={
            "keys": ["revenue", "net_income"],
            "period_type": "FY", "period_year": RUNNING_YEAR,
        },
    )

    assert r.status_code == 200
    assert guidance_calls == [("TST", RUNNING_YEAR)]
    assert anchor_keys == []  # EDGAR-Anker ist US-only
    assert statement_calls == [("TST", RUNNING_YEAR, ("income",))]


def test_non_us_refresh_closed_year_statement_without_guidance(client, db, monkeypatch):
    """Nicht-US-Firma, abgeschlossenes Jahr: Statement-Recherche laeuft,
    Guidance-Call nicht (der gehoert dem laufenden FY)."""
    cid = _setup_refresh(client, db, "wire-eu-closed@example.com", "DE0001234567")
    guidance_calls, _anchor_keys, statement_calls, *_ = _patch_refresh_env(monkeypatch)

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={
            "keys": ["revenue", "operating_cash_flow"],
            "period_type": "FY", "period_year": PREV_YEAR,
        },
    )

    assert r.status_code == 200
    assert guidance_calls == []
    assert statement_calls == [("TST", PREV_YEAR, ("income", "cashflow"))]


def test_refresh_backfill_year_gets_calc_and_price_anchors_us(client, db, monkeypatch):
    """Backfill-Fall (consistency_years = [N-1, N]), US-Filer: das
    abgeschlossene Vorjahr bekommt die historischen Preis-Anker (year:
    Einstieg, year+1: FY-Ende fuer actual_return) und den FY-Ratio-Satz —
    nicht nur payload.period_year (SAP-Befund 1d)."""
    import app.values.routes as routes
    cid = _setup_refresh(client, db, "wire-prev-us@example.com", "US0001234567")
    _g, _a, _s, calc_calls, mcap_calls = _patch_refresh_env(monkeypatch)
    monkeypatch.setattr(routes, "_prev_year_needs_backfill",
                        lambda db_, cid_, k, y: True)

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={
            "keys": ["net_income"],
            "period_type": "FY", "period_year": RUNNING_YEAR,
        },
    )

    assert r.status_code == 200
    assert mcap_calls == [PREV_YEAR, RUNNING_YEAR]
    assert calc_calls == [("FY", PREV_YEAR), ("FY", RUNNING_YEAR)]


def test_refresh_backfill_year_gets_calc_and_price_anchors_non_us(client, db, monkeypatch):
    """Gleiches Wiring fuer Nicht-US-Filer — Vorjahres-Kennzahlen und
    Preis-Anker sind generisch, nicht US-only."""
    import app.values.routes as routes
    cid = _setup_refresh(client, db, "wire-prev-eu@example.com", "DE0001234567")
    _g, _a, _s, calc_calls, mcap_calls = _patch_refresh_env(monkeypatch)
    monkeypatch.setattr(routes, "_prev_year_needs_backfill",
                        lambda db_, cid_, k, y: True)

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={
            "keys": ["net_income"],
            "period_type": "FY", "period_year": RUNNING_YEAR,
        },
    )

    assert r.status_code == 200
    assert mcap_calls == [PREV_YEAR, RUNNING_YEAR]
    assert calc_calls == [("FY", PREV_YEAR), ("FY", RUNNING_YEAR)]


def test_refresh_existing_price_anchors_not_refetched(client, db, monkeypatch):
    """Vorhandene market_cap-FY-Anker werden nicht erneut gefetcht
    (_has_fy_price_anchor-Guard); der Vorjahres-Calc laeuft trotzdem."""
    import app.values.routes as routes
    cid = _setup_refresh(client, db, "wire-anchor-guard@example.com", "US0001234567")
    for yr in (PREV_YEAR, RUNNING_YEAR):
        db.add(CompanyValue(
            company_id=cid, value_key="market_cap", period_type="FY",
            period_year=yr, numeric_value=Decimal("200000000000"),
        ))
    db.commit()
    _g, _a, _s, calc_calls, mcap_calls = _patch_refresh_env(monkeypatch)
    monkeypatch.setattr(routes, "_prev_year_needs_backfill",
                        lambda db_, cid_, k, y: True)

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={
            "keys": ["net_income"],
            "period_type": "FY", "period_year": RUNNING_YEAR,
        },
    )

    assert r.status_code == 200
    assert mcap_calls == []
    assert calc_calls == [("FY", PREV_YEAR), ("FY", RUNNING_YEAR)]
