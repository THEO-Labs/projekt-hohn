"""Gap-Inventar: collect_missing_combos, not_found-Platzhalter,
Vollstaendigkeits-Report."""

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
    # Nicht-US-Neuanlage ist per API gesperrt (Produktentscheid) —
    # Bestands-Firmen direkt per ORM anlegen.
    from app.companies.models import Company
    cids = {}
    for t in tickers:
        company = Company(portfolio_id=UUID(pid), name=t, ticker=t, currency="EUR")
        db.add(company)
        db.commit()
        cids[t] = company.id
    return UUID(pid), cids


def _api_keys(db):
    import scripts.fill_gaps as fg
    return fg._expected_api_keys(db)


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
