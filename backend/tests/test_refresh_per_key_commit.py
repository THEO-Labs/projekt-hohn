"""FY-Refresh muss PRO KEY committen: ein Fehler (und der Rollback) bei
Key N darf die bereits erfolgreich geschriebenen Keys 1..N-1 nicht
verwerfen. Verworfene Instanzen des fehlgeschlagenen Keys duerfen nicht
in der Response landen (db.refresh auf nie committete Zeilen crasht)."""

from decimal import Decimal
from uuid import UUID

from app.auth.models import User
from app.auth.security import hash_password
from app.values.models import CompanyValue

YEAR = 2024


def _setup(client, db, email):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]
    c = client.post(
        f"/api/portfolios/{pid}/companies",
        json={"name": "TestCo", "ticker": "TST", "currency": "EUR"},
    ).json()
    return UUID(c["id"])


def _patch_refresh_env(monkeypatch, failing_key):
    """Isoliert den Key-Loop: kein Backfill, keine Calculations, kein
    Prev-Year-Prefetch, keine Statement-Recherche. Der gepatchte Nicht-US-
    Key-Schritt (FY-Anker) schreibt pro Key eine Zeile und wirft fuer
    failing_key NACH einem partiellen Write."""
    import app.values.routes as routes
    import app.values.statement_research as sr

    monkeypatch.setattr(routes, "_prev_year_needs_backfill",
                        lambda db_, cid_, k, y: False)
    monkeypatch.setattr(routes, "_run_and_persist_calculations", lambda *a, **kw: [])
    monkeypatch.setattr(routes, "_ensure_previous_year_inputs", lambda *a, **kw: None)
    monkeypatch.setattr(sr, "fetch_statement_research", lambda *a, **kw: 0)

    def fake_anchor(db, company, key, year, updated):
        row = CompanyValue(
            company_id=company.id, value_key=key, period_type="FY",
            period_year=year, numeric_value=Decimal("42"),
            source_name="fake anchor", primary_method="provider",
        )
        db.add(row)
        db.flush()
        updated.append(row)
        if key == failing_key:
            raise RuntimeError("boom after partial write")
        return True

    monkeypatch.setattr(routes, "_anchor_fy_after_apply", fake_anchor)


def _rows(db, cid, key):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == cid,
            CompanyValue.value_key == key,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == YEAR,
        )
        .all()
    )


def test_failed_key_does_not_discard_previous_keys(client, db, monkeypatch):
    """Key 1 (net_income) schreibt erfolgreich, Key 2 (revenue) wirft:
    der Rollback darf nur revenue treffen — net_income bleibt committet.
    Die Response enthaelt nur die committete Zeile (kein 500 durch
    db.refresh auf einer verworfenen Instanz)."""
    cid = _setup(client, db, "commit1@example.com")
    _patch_refresh_env(monkeypatch, failing_key="revenue")

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={"keys": ["net_income", "revenue"], "period_type": "FY", "period_year": YEAR},
    )

    assert r.status_code == 200
    ni = _rows(db, cid, "net_income")
    assert len(ni) == 1 and ni[0].numeric_value == Decimal("42")
    assert _rows(db, cid, "revenue") == []  # Rollback traf nur den Fehler-Key
    keys_in_response = [row["value_key"] for row in r.json()]
    assert keys_in_response == ["net_income"]


def test_failed_first_key_does_not_block_later_keys(client, db, monkeypatch):
    """Auch umgekehrt: wirft der ERSTE Key, laufen und persistieren die
    folgenden Keys normal weiter."""
    cid = _setup(client, db, "commit2@example.com")
    _patch_refresh_env(monkeypatch, failing_key="revenue")

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={"keys": ["revenue", "net_income"], "period_type": "FY", "period_year": YEAR},
    )

    assert r.status_code == 200
    assert _rows(db, cid, "revenue") == []
    ni = _rows(db, cid, "net_income")
    assert len(ni) == 1 and ni[0].numeric_value == Decimal("42")


def test_mark_success_counts_only_committed_keys(client, db, monkeypatch):
    """mark_success darf erst nach erfolgreichem Commit zaehlen — der
    Fehler-Key erscheint nicht als successful im Progress-Job."""
    from app.values.progress import get_job

    cid = _setup(client, db, "commit3@example.com")
    _patch_refresh_env(monkeypatch, failing_key="revenue")

    r = client.post(
        f"/api/companies/{cid}/values/refresh",
        json={"keys": ["net_income", "revenue"], "period_type": "FY", "period_year": YEAR},
    )

    assert r.status_code == 200
    job = get_job(cid)
    assert job["status"] == "done"
    assert job["successful"] == 1
