"""Next Earnings Release pro Firma: Provider-Parser, 24h-Refresh-Hook
im Stammdaten-Only-Pfad und CompanyOut-Feld."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import UUID

import pandas as pd
import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.providers.yahoo import YahooFinanceProvider

FUTURE = date.today() + timedelta(days=30)
FAR_FUTURE = date.today() + timedelta(days=120)
PAST = date.today() - timedelta(days=90)


# ---------------------------------------------------------------------------
# Provider-Parser
# ---------------------------------------------------------------------------

@pytest.fixture
def provider():
    return YahooFinanceProvider()


def _fake_ticker(calendar=None, earnings_df=None, calendar_raises=False):
    t = MagicMock()
    if calendar_raises:
        type(t).calendar = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    else:
        t.calendar = calendar if calendar is not None else {}
    t.get_earnings_dates.return_value = earnings_df
    return t


def test_calendar_returns_only_future_date(provider):
    """Kalender liefert vergangenen + kuenftigen Termin -> nur der
    naechste ZUKUENFTIGE wird zurueckgegeben."""
    fake = _fake_ticker(calendar={"Earnings Date": [PAST, FAR_FUTURE, FUTURE]})
    with patch.object(provider, "_get_ticker", return_value=fake):
        assert provider.fetch_next_earnings_date("TST") == FUTURE
    fake.get_earnings_dates.assert_not_called()


def test_calendar_empty_falls_back_to_earnings_dates(provider):
    """Leerer Kalender -> Fallback auf get_earnings_dates (DataFrame mit
    Timestamp-Index, vergangene Termine werden verworfen)."""
    df = pd.DataFrame(
        {"EPS Estimate": [1.0, 2.0]},
        index=[pd.Timestamp(PAST), pd.Timestamp(FUTURE)],
    )
    fake = _fake_ticker(calendar={}, earnings_df=df)
    with patch.object(provider, "_get_ticker", return_value=fake):
        assert provider.fetch_next_earnings_date("TST") == FUTURE


def test_only_past_dates_returns_none(provider):
    fake = _fake_ticker(calendar={"Earnings Date": [PAST]}, earnings_df=None)
    with patch.object(provider, "_get_ticker", return_value=fake):
        assert provider.fetch_next_earnings_date("TST") is None


def test_error_raises_for_caller_to_distinguish(monkeypatch):
    """Ausfall raist (statt None), damit der Hook 'Ausfall' von
    'kein Termin bekannt' unterscheiden kann."""
    import pytest
    from app.providers.yahoo import YahooFinanceProvider

    p = YahooFinanceProvider()

    class _Boom:
        @property
        def calendar(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(p, "_get_ticker", lambda ticker: _Boom())
    with pytest.raises(RuntimeError):
        p.fetch_next_earnings_date("TST.BOOM")


def test_result_is_cached(provider):
    """TTL-Cache: zweiter Call fragt Yahoo nicht erneut."""
    fake = _fake_ticker(calendar={"Earnings Date": [FUTURE]})
    with patch.object(provider, "_get_ticker", return_value=fake) as get_ticker:
        assert provider.fetch_next_earnings_date("TST") == FUTURE
        assert provider.fetch_next_earnings_date("TST") == FUTURE
    assert get_ticker.call_count == 1


# ---------------------------------------------------------------------------
# Refresh-Hook (stammdaten_only) + CompanyOut
# ---------------------------------------------------------------------------

class _FakeEarningsProvider:
    """Minimal-Provider fuer den Hook: zaehlt Calls, liefert festes Datum."""

    def __init__(self, result=FUTURE):
        self.result = result
        self.calls = 0

    def fetch_next_earnings_date(self, ticker):
        self.calls += 1
        return self.result


def _setup(client, db, email):
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]
    c = client.post(
        f"/api/portfolios/{pid}/companies",
        json={"name": "TestCo", "ticker": "TST", "currency": "EUR"},
    ).json()
    return pid, UUID(c["id"])


def _patch_hook_env(monkeypatch, fake_provider):
    import app.values.routes as routes

    monkeypatch.setattr(routes, "get_providers", lambda key: [fake_provider])
    monkeypatch.setattr(routes, "_run_and_persist_calculations", lambda *a, **kw: [])


def _daily_refresh(client, cid):
    return client.post(
        f"/api/companies/{cid}/values/refresh",
        json={"keys": [], "stammdaten_only": True},
    )


def test_hook_fetches_and_persists_when_never_checked(client, db, monkeypatch):
    """earnings_checked_at is None -> Provider-Call + Persist von Datum
    und Zeitstempel."""
    _, cid = _setup(client, db, "earn1@example.com")
    fake = _FakeEarningsProvider()
    _patch_hook_env(monkeypatch, fake)

    r = _daily_refresh(client, cid)

    assert r.status_code == 200
    assert fake.calls == 1
    company = db.get(Company, cid)
    db.refresh(company)
    assert company.next_earnings_date == FUTURE
    assert company.earnings_checked_at is not None


def test_hook_skips_when_checked_recently(client, db, monkeypatch):
    """earnings_checked_at juenger als 24h -> kein Provider-Call, Werte
    bleiben unveraendert."""
    _, cid = _setup(client, db, "earn2@example.com")
    company = db.get(Company, cid)
    fresh = datetime.now(timezone.utc) - timedelta(hours=1)
    company.earnings_checked_at = fresh
    company.next_earnings_date = PAST
    db.commit()

    fake = _FakeEarningsProvider()
    _patch_hook_env(monkeypatch, fake)

    r = _daily_refresh(client, cid)

    assert r.status_code == 200
    assert fake.calls == 0
    db.refresh(company)
    assert company.next_earnings_date == PAST


def test_hook_refetches_when_stale(client, db, monkeypatch):
    """earnings_checked_at aelter als 24h -> neuer Call + Persist."""
    _, cid = _setup(client, db, "earn3@example.com")
    company = db.get(Company, cid)
    company.earnings_checked_at = datetime.now(timezone.utc) - timedelta(hours=25)
    company.next_earnings_date = PAST
    db.commit()

    fake = _FakeEarningsProvider()
    _patch_hook_env(monkeypatch, fake)

    r = _daily_refresh(client, cid)

    assert r.status_code == 200
    assert fake.calls == 1
    db.refresh(company)
    assert company.next_earnings_date == FUTURE
    assert company.earnings_checked_at > datetime.now(timezone.utc) - timedelta(minutes=5)


def test_company_out_contains_next_earnings_date(client, db):
    """CompanyOut liefert das Feld ueber die Companies-Liste aus."""
    pid, cid = _setup(client, db, "earn4@example.com")
    company = db.get(Company, cid)
    company.next_earnings_date = FUTURE
    db.commit()

    r = client.get(f"/api/portfolios/{pid}/companies")
    assert r.status_code == 200
    row = next(c for c in r.json() if c["id"] == str(cid))
    assert row["next_earnings_date"] == FUTURE.isoformat()


def test_today_counts_as_future(monkeypatch):
    """Ein Earnings-Release HEUTE darf nicht verschwinden."""
    from datetime import date
    from app.providers.yahoo import YahooFinanceProvider

    p = YahooFinanceProvider()

    class _T:
        calendar = {"Earnings Date": [date.today()]}

    monkeypatch.setattr(p, "_get_ticker", lambda ticker: _T())
    assert p.fetch_next_earnings_date("TST.TODAY") == date.today()


def test_provider_outage_preserves_known_date(client, db, monkeypatch):
    """Yahoo-Ausfall darf einen bekannten Termin nicht mit None
    ueberschreiben und nicht den 24h-Backoff stempeln."""
    from datetime import date, timedelta
    import app.values.routes as routes
    from app.companies.models import Company

    known = date.today() + timedelta(days=10)

    class _BoomProvider:
        def fetch_next_earnings_date(self, ticker):
            raise RuntimeError("yahoo down")

    monkeypatch.setattr(routes, "get_providers", lambda key: [_BoomProvider()])

    from tests.test_values import _seed_catalog, _login_with_company
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="outage@example.com")
    comp = db.query(Company).filter(Company.id == cid).one()
    comp.next_earnings_date = known
    comp.earnings_checked_at = None
    db.commit()

    routes._maybe_refresh_next_earnings(db, comp, comp.ticker)
    db.rollback()
    comp = db.query(Company).filter(Company.id == comp.id).one()
    assert comp.next_earnings_date == known
    assert comp.earnings_checked_at is None
