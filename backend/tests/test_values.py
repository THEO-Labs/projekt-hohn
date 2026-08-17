from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.auth.models import User
from app.auth.security import hash_password
from app.providers.base import ProviderResult
from app.values.catalog import SEED_VALUES
from app.values.models import CompanyValue


def _seed_catalog(db):
    from sqlalchemy import text
    for row in SEED_VALUES:
        db.execute(
            text(
                "INSERT INTO value_definitions "
                "(key, label_de, label_en, category, source_type, data_type, unit, sort_order) "
                "VALUES (:key, :label_de, :label_en, "
                "CAST(:category AS valuecategory), "
                "CAST(:source_type AS sourcetype), "
                "CAST(:data_type AS datatype), "
                ":unit, :sort_order) "
                "ON CONFLICT (key) DO NOTHING"
            ),
            {
                "key": row["key"],
                "label_de": row["label_de"],
                "label_en": row["label_en"],
                "category": row["category"],
                "source_type": row["source_type"],
                "data_type": row["data_type"],
                "unit": row.get("unit"),
                "sort_order": row["sort_order"],
            },
        )
    db.commit()


def _login_with_company(client, db, email="t@example.com", ticker="AAPL"):
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})
    p = client.post("/api/portfolios", json={"name": "P"}).json()
    pid = p["id"]
    # Nicht-US-Firma (Statement-Recherche-Pfad) — Neuanlage per API ist
    # gesperrt (ISIN-Pflicht + Nicht-US-Block), daher direkt per ORM.
    # Ohne ISIN: is_us_company==False, accounting_standard IFRS; die
    # ISIN-basierte Ticker-Aufloesung im Refresh bleibt inaktiv (die
    # Market-Cap-Refresh-Tests mocken nur get_providers, nicht
    # resolve_ticker_from_isin).
    from uuid import UUID
    from app.companies.models import Company
    company = Company(portfolio_id=UUID(pid), name="Apple", ticker=ticker,
                      currency="USD")
    db.add(company)
    db.commit()
    return user, pid, str(company.id)


def _login(client, db, email="catalog@example.com"):
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})


def test_get_value_definitions_returns_catalog(client, db):
    _seed_catalog(db)
    _login(client, db)
    response = client.get("/api/value-definitions")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == len(SEED_VALUES)
    keys = {item["key"] for item in data}
    assert "market_cap" in keys
    assert "hohn_return_simple" in keys
    assert "hohn_return_detailed" in keys
    assert "sbc" in keys
    assert "cash_and_equivalents" in keys
    assert "net_buyback_yield" in keys


def test_get_value_definitions_ordered(client, db):
    _seed_catalog(db)
    _login(client, db)
    response = client.get("/api/value-definitions")
    data = response.json()
    orders = [item["sort_order"] for item in data]
    assert orders == sorted(orders)


def test_get_company_values_empty(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)
    response = client.get(f"/api/companies/{cid}/values")
    assert response.status_code == 200
    assert response.json() == []


def test_manual_override(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"numeric_value": "5.25", "source_name": "Manual"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["manually_overridden"] is True
    assert data["numeric_value"] == "5.250000"
    assert data["source_name"] == "Manual"


def test_manual_override_sign_normalized(client, db):
    """Negative Eingabe fuer einen ALWAYS_POSITIVE_KEY wird zentral auf abs()
    normalisiert — unabhaengig vom Schreibpfad (hier: Override-Endpoint)."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="sign@example.com")

    response = client.post(
        f"/api/companies/{cid}/values/buyback_volume/override?period_type=FY&period_year=2024",
        json={"numeric_value": "-3000", "source_name": "Manual"},
    )
    assert response.status_code == 200
    assert Decimal(response.json()["numeric_value"]) == Decimal("3000")


def test_manual_override_zero_persists(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)

    response = client.post(
        f"/api/companies/{cid}/values/buyback_volume/override?period_type=FY&period_year=2024",
        json={"numeric_value": 0, "source_name": "Manuell"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["manually_overridden"] is True
    assert Decimal(data["numeric_value"]) == Decimal("0")

    overwrite = client.post(
        f"/api/companies/{cid}/values/buyback_volume/override?period_type=FY&period_year=2024",
        json={"numeric_value": 0, "source_name": "Manuell"},
    )
    assert overwrite.status_code == 200
    assert Decimal(overwrite.json()["numeric_value"]) == Decimal("0")

    fetched = client.get(f"/api/companies/{cid}/values?period_type=FY&period_year=2024")
    assert fetched.status_code == 200
    rows = [r for r in fetched.json() if r["value_key"] == "buyback_volume"]
    assert len(rows) == 1
    assert Decimal(rows[0]["numeric_value"]) == Decimal("0")


def test_company_values_requires_auth(client, db):
    _seed_catalog(db)
    cid = str(uuid4())
    client.post("/api/auth/logout")
    response = client.get(f"/api/companies/{cid}/values")
    assert response.status_code == 401


def test_company_values_other_user_is_404(client, db):
    _seed_catalog(db)
    _u1, _p1, cid = _login_with_company(client, db, email="a@example.com")
    client.post("/api/auth/logout")

    user2 = User(email="b@example.com", password_hash=hash_password("pw1234"))
    db.add(user2)
    db.commit()
    client.post("/api/auth/login", json={"email": "b@example.com", "password": "pw1234"})

    response = client.get(f"/api/companies/{cid}/values")
    assert response.status_code == 404


def _seed_value(
    db, cid, key, period_type, period_year, value, manually_overridden=False,
    adjusted=None, adjustments_note=None, adjustments_source=None,
):
    cv = CompanyValue(
        company_id=cid,
        value_key=key,
        period_type=period_type,
        period_year=period_year,
        numeric_value=Decimal(str(value)),
        numeric_value_adjusted=Decimal(str(adjusted)) if adjusted is not None else None,
        adjustments_note=adjustments_note,
        adjustments_source=adjustments_source,
        source_name="Test",
        manually_overridden=manually_overridden,
    )
    db.add(cv)
    db.commit()


def _get_row(client, cid, key, period_type, period_year=None):
    params = f"period_type={period_type}"
    if period_year is not None:
        params += f"&period_year={period_year}"
    rows = client.get(f"/api/companies/{cid}/values?{params}").json()
    return next((r for r in rows if r["value_key"] == key), None)


def test_override_calculated_key_rejected(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="lock@example.com")

    # net_debt ist bewusst KEIN Calculated-Key mehr (kommt direkt aus der
    # Extraktion) und darf daher manuell ueberschrieben werden.
    for key in ("hohn_return_simple", "hohn_return_detailed", "ni_growth", "fcf_yield"):
        response = client.post(
            f"/api/companies/{cid}/values/{key}/override?period_type=FY&period_year=2024",
            json={"numeric_value": "1.0", "source_name": "Manual"},
        )
        assert response.status_code == 400, f"override of {key} should be rejected"


def test_override_primary_triggers_same_year_recalc(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="recalc@example.com")

    _seed_value(db, cid, "market_cap", "SNAPSHOT", None, "1000")
    _seed_value(db, cid, "net_income", "FY", 2023, "100")
    _seed_value(db, cid, "net_income", "FY", 2024, "150")

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"numeric_value": "200", "source_name": "Manuell"},
    )
    assert response.status_code == 200
    assert Decimal(response.json()["numeric_value"]) == Decimal("200")

    ni_growth = _get_row(client, cid, "ni_growth", "FY", 2024)
    assert ni_growth is not None
    assert Decimal(ni_growth["numeric_value"]) == Decimal("100")
    assert ni_growth["manually_overridden"] is False


def test_override_primary_cascades_to_next_year_when_data_exists(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="cross@example.com")

    _seed_value(db, cid, "market_cap", "SNAPSHOT", None, "1000")
    _seed_value(db, cid, "net_income", "FY", 2023, "100")
    _seed_value(db, cid, "net_income", "FY", 2024, "150")
    _seed_value(db, cid, "net_income", "FY", 2025, "180")

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"numeric_value": "200", "source_name": "Manuell"},
    )
    assert response.status_code == 200

    ng_2025 = _get_row(client, cid, "ni_growth", "FY", 2025)
    assert ng_2025 is not None
    assert Decimal(ng_2025["numeric_value"]) == Decimal("-10")


def test_override_primary_does_not_create_rows_in_empty_next_year(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="empty@example.com")

    _seed_value(db, cid, "market_cap", "SNAPSHOT", None, "1000")
    _seed_value(db, cid, "net_income", "FY", 2023, "100")
    _seed_value(db, cid, "net_income", "FY", 2024, "150")

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"numeric_value": "200", "source_name": "Manuell"},
    )
    assert response.status_code == 200

    rows_2025 = client.get(f"/api/companies/{cid}/values?period_type=FY&period_year=2025").json()
    assert rows_2025 == []


def test_override_market_cap_recalcs_all_existing_fy_years(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="mcap@example.com")

    _seed_value(db, cid, "market_cap", "SNAPSHOT", None, "1000")
    _seed_value(db, cid, "fcf", "FY", 2023, "100")
    _seed_value(db, cid, "fcf", "FY", 2024, "200")

    response = client.post(
        f"/api/companies/{cid}/values/market_cap/override?period_type=SNAPSHOT",
        json={"numeric_value": "2000", "source_name": "Manuell"},
    )
    assert response.status_code == 200

    fy_2023 = _get_row(client, cid, "fcf_yield", "FY", 2023)
    fy_2024 = _get_row(client, cid, "fcf_yield", "FY", 2024)
    assert fy_2023 is not None and Decimal(fy_2023["numeric_value"]) == Decimal("5")
    assert fy_2024 is not None and Decimal(fy_2024["numeric_value"]) == Decimal("10")


def test_old_calc_key_with_stale_lock_gets_overwritten(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="stale@example.com")

    _seed_value(db, cid, "market_cap", "SNAPSHOT", None, "1000")
    _seed_value(db, cid, "net_income", "FY", 2023, "100")
    _seed_value(db, cid, "net_income", "FY", 2024, "150")
    _seed_value(db, cid, "ni_growth", "FY", 2024, "999", manually_overridden=True)

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"numeric_value": "200", "source_name": "Manuell"},
    )
    assert response.status_code == 200

    ng = _get_row(client, cid, "ni_growth", "FY", 2024)
    assert ng is not None
    assert Decimal(ng["numeric_value"]) == Decimal("100")
    assert ng["manually_overridden"] is False


def test_override_adjusted_variant_writes_adjusted_only(client, db):
    """variant=adjusted schreibt numeric_value_adjusted auf der bestehenden
    Zeile; GAAP-Felder bleiben unangetastet."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="adj@example.com")

    _seed_value(db, cid, "net_income", "FY", 2024, "150")

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"numeric_value": "175", "variant": "adjusted"},
    )
    assert response.status_code == 200
    data = response.json()
    assert Decimal(data["numeric_value"]) == Decimal("150")
    assert Decimal(data["numeric_value_adjusted"]) == Decimal("175")
    assert data["adjustments_note"] == "Manuell ueberschrieben"
    assert data["adjustments_source"] == "Manual"
    assert data["manually_overridden"] is False
    assert data["source_name"] == "Test"
    assert data["primary_method"] is None
    assert data["is_forecast"] is False
    assert data["from_ir_pdf"] is False


def test_override_adjusted_without_base_row_404(client, db):
    """variant=adjusted ohne bestehende Zeile darf keine neue Zeile anlegen."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="adj404@example.com")

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"numeric_value": "175", "variant": "adjusted"},
    )
    assert response.status_code == 404
    assert "Kein Basiswert" in response.json()["detail"]
    rows = client.get(f"/api/companies/{cid}/values?period_type=FY&period_year=2024").json()
    assert rows == []


def test_override_invalid_variant_422(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="adj422@example.com")

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"numeric_value": "175", "variant": "non_gaap"},
    )
    assert response.status_code == 422


def test_override_adjusted_text_value_400(client, db):
    """Adjusted gibt es nur numerisch — text_value mit variant=adjusted -> 400."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="adjtxt@example.com")

    _seed_value(db, cid, "net_income", "FY", 2024, "150")

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"text_value": "abc", "variant": "adjusted"},
    )
    assert response.status_code == 400


def test_override_adjusted_triggers_recalc(client, db):
    """Adjusted-Override stoesst denselben Recalc an wie der GAAP-Pfad."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="adjcalc@example.com")

    _seed_value(db, cid, "market_cap", "SNAPSHOT", None, "1000")
    _seed_value(db, cid, "net_income", "FY", 2023, "100")
    _seed_value(db, cid, "net_income", "FY", 2024, "150")

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"numeric_value": "200", "variant": "adjusted"},
    )
    assert response.status_code == 200
    # GAAP-basierter ni_growth existiert nach dem Recalc (150 vs 100 = 50%).
    ng = _get_row(client, cid, "ni_growth", "FY", 2024)
    assert ng is not None
    assert Decimal(ng["numeric_value"]) == Decimal("50")


def test_override_gaap_default_leaves_adjusted_untouched(client, db):
    """Regression: Default-Override (ohne variant) schreibt weiterhin nur
    numeric_value und laesst ein vorhandenes Adjusted-Feld stehen."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="gaapreg@example.com")

    _seed_value(db, cid, "net_income", "FY", 2024, "150")
    client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"numeric_value": "175", "variant": "adjusted"},
    )

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"numeric_value": "160", "source_name": "Manual"},
    )
    assert response.status_code == 200
    data = response.json()
    assert Decimal(data["numeric_value"]) == Decimal("160")
    assert Decimal(data["numeric_value_adjusted"]) == Decimal("175")
    assert data["adjustments_note"] == "Manuell ueberschrieben"
    assert data["adjustments_source"] == "Manual"
    assert data["manually_overridden"] is True
    assert data["primary_method"] == "manual"


def test_override_gaap_leaves_research_adjusted_untouched(client, db):
    """Regression: GAAP-Override auf einer Zeile mit Research-Adjusted
    (adjustments_source gesetzt) laesst alle Adjusted-Felder unveraendert."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="gaapresearch@example.com")

    _seed_value(
        db, cid, "net_income", "FY", 2024, "150",
        adjusted="140", adjustments_note="Excludes restructuring",
        adjustments_source="SEC 10-K",
    )

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=FY&period_year=2024",
        json={"numeric_value": "160", "source_name": "Manual"},
    )
    assert response.status_code == 200
    data = response.json()
    assert Decimal(data["numeric_value"]) == Decimal("160")
    assert Decimal(data["numeric_value_adjusted"]) == Decimal("140")
    assert data["adjustments_note"] == "Excludes restructuring"
    assert data["adjustments_source"] == "SEC 10-K"


def test_q_override_recalc_preserves_sourced_fy_adjusted(client, db):
    """Q-GAAP-Override loest den FY-Derive aus — der darf sourced Adjusted
    (Manual oder Research) auf der FY-Zeile weder ueberschreiben noch nullen."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="qadj@example.com")

    for q in ("Q1", "Q2", "Q3", "Q4"):
        _seed_value(db, cid, "net_income", q, 2024, "25")
    _seed_value(
        db, cid, "net_income", "FY", 2024, "100",
        adjusted="120", adjustments_note="Manuell ueberschrieben",
        adjustments_source="Manual",
    )

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=Q1&period_year=2024",
        json={"numeric_value": "40", "source_name": "Manual"},
    )
    assert response.status_code == 200

    fy = _get_row(client, cid, "net_income", "FY", 2024)
    assert fy is not None
    # GAAP-FY wurde neu abgeleitet (40+25+25+25), Adjusted blieb stehen.
    assert Decimal(fy["numeric_value"]) == Decimal("115")
    assert Decimal(fy["numeric_value_adjusted"]) == Decimal("120")
    assert fy["adjustments_note"] == "Manuell ueberschrieben"
    assert fy["adjustments_source"] == "Manual"


def test_q_override_recalc_rederives_unsourced_fy_adjusted(client, db):
    """Gegenprobe: selbst abgeleitetes FY-Adjusted (adjustments_source NULL)
    wird beim Q-Derive weiterhin neu berechnet (Summe der Q-Adjusted)."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="qadjnull@example.com")

    for q in ("Q1", "Q2", "Q3", "Q4"):
        _seed_value(db, cid, "net_income", q, 2024, "25", adjusted="30")
    _seed_value(db, cid, "net_income", "FY", 2024, "100", adjusted="999")

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=Q1&period_year=2024",
        json={"numeric_value": "40", "source_name": "Manual"},
    )
    assert response.status_code == 200

    fy = _get_row(client, cid, "net_income", "FY", 2024)
    assert fy is not None
    assert Decimal(fy["numeric_value"]) == Decimal("115")
    # Q1-Adjusted (30) + Q2-Q4 (je 30) = 120 — der stale FY-Adjusted (999)
    # ohne Source wird neu abgeleitet.
    assert Decimal(fy["numeric_value_adjusted"]) == Decimal("120")


def test_q_override_recalc_rederives_two_stage_fy_adjusted(client, db):
    """Two-Stage-Adjusted (source im 'quote | url'-Format) ist NICHT
    geschuetzt: der FY-Derive ueberschreibt den stale LLM-Adjusted-Wert
    und raeumt den nicht mehr passenden Beleg ab (adjusted_is_protected
    schuetzt nur Manual und SEC-8-K-URLs)."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="qadjts@example.com")

    for q in ("Q1", "Q2", "Q3", "Q4"):
        _seed_value(db, cid, "net_income", q, 2024, "25", adjusted="30")
    _seed_value(
        db, cid, "net_income", "FY", 2024, "100",
        adjusted="999", adjustments_note="Excludes SBC",
        adjustments_source="Adjusted net income was... | https://ir.example/pr",
    )

    response = client.post(
        f"/api/companies/{cid}/values/net_income/override?period_type=Q1&period_year=2024",
        json={"numeric_value": "40", "source_name": "Manual"},
    )
    assert response.status_code == 200

    fy = _get_row(client, cid, "net_income", "FY", 2024)
    assert fy is not None
    assert Decimal(fy["numeric_value"]) == Decimal("115")
    assert Decimal(fy["numeric_value_adjusted"]) == Decimal("120")
    assert fy["adjustments_note"] is None
    assert fy["adjustments_source"] is None


def test_q_adjusted_override_derives_mixed_fy_adjusted_sum(client, db):
    """User-Bug: Q4-Adjusted-Override — FY-Adjusted muss als Mischsumme
    (adjusted wenn vorhanden, sonst GAAP je Quartal) neu abgeleitet werden.
    Vorher: Summe nur bei 4/4 echten Adjusted-Werten -> FY-Adjusted blieb
    NULL und die Annual-Spalte zeigte weiter die reine GAAP-Summe."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="qadjmix@example.com")

    for q, v in (("Q1", "10901"), ("Q2", "11230"), ("Q3", "11633"), ("Q4", "12000")):
        _seed_value(db, cid, "revenue", q, 2024, v)
    _seed_value(db, cid, "revenue", "FY", 2024, "45764")

    response = client.post(
        f"/api/companies/{cid}/values/revenue/override?period_type=Q4&period_year=2024",
        json={"numeric_value": "12100", "variant": "adjusted"},
    )
    assert response.status_code == 200

    fy = _get_row(client, cid, "revenue", "FY", 2024)
    assert fy is not None
    # GAAP-Summe unveraendert, Adjusted = 10901+11230+11633+12100 (Q1-Q3 GAAP-Fallback).
    assert Decimal(fy["numeric_value"]) == Decimal("45764")
    assert Decimal(fy["numeric_value_adjusted"]) == Decimal("45864")
    assert fy["adjustments_note"] == "Summe der Quartale (adjusted, GAAP-Fallback je Quartal)"
    assert fy["adjustments_source"] is None


def test_q_override_without_any_adjusted_keeps_fy_adjusted_null(client, db):
    """Haben ALLE Quartale kein Adjusted, bleibt FY-Adjusted NULL
    (Fallback-Marker fuers UI) — keine GAAP-Kopie in die Adjusted-Spalte."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="qadjnone@example.com")

    for q in ("Q1", "Q2", "Q3", "Q4"):
        _seed_value(db, cid, "revenue", q, 2024, "100")
    _seed_value(db, cid, "revenue", "FY", 2024, "400")

    response = client.post(
        f"/api/companies/{cid}/values/revenue/override?period_type=Q1&period_year=2024",
        json={"numeric_value": "110", "source_name": "Manual"},
    )
    assert response.status_code == 200

    fy = _get_row(client, cid, "revenue", "FY", 2024)
    assert fy is not None
    assert Decimal(fy["numeric_value"]) == Decimal("410")
    assert fy["numeric_value_adjusted"] is None
    assert fy["adjustments_note"] is None


def test_q_adjusted_override_respects_protected_fy_adjusted(client, db):
    """Geschuetzte FY-Adjusted (adjustments_source='Manual') bleiben auch
    beim Adjusted-Q-Override-Refresh unangetastet."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="qadjprot@example.com")

    for q in ("Q1", "Q2", "Q3", "Q4"):
        _seed_value(db, cid, "revenue", q, 2024, "100")
    _seed_value(
        db, cid, "revenue", "FY", 2024, "400",
        adjusted="999", adjustments_note="Manuell ueberschrieben",
        adjustments_source="Manual",
    )

    response = client.post(
        f"/api/companies/{cid}/values/revenue/override?period_type=Q4&period_year=2024",
        json={"numeric_value": "120", "variant": "adjusted"},
    )
    assert response.status_code == 200

    fy = _get_row(client, cid, "revenue", "FY", 2024)
    assert fy is not None
    assert Decimal(fy["numeric_value_adjusted"]) == Decimal("999")
    assert fy["adjustments_note"] == "Manuell ueberschrieben"
    assert fy["adjustments_source"] == "Manual"


def test_q_adjusted_override_full_coverage_sum_without_fallback_note(client, db):
    """4/4 echte Q-Adjusted: reine Adjusted-Summe, keine Fallback-Note."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="qadjfull@example.com")

    for q in ("Q1", "Q2", "Q3", "Q4"):
        _seed_value(db, cid, "revenue", q, 2024, "100", adjusted="110")
    _seed_value(db, cid, "revenue", "FY", 2024, "400")

    response = client.post(
        f"/api/companies/{cid}/values/revenue/override?period_type=Q4&period_year=2024",
        json={"numeric_value": "120", "variant": "adjusted"},
    )
    assert response.status_code == 200

    fy = _get_row(client, cid, "revenue", "FY", 2024)
    assert fy is not None
    # 110+110+110+120 — alle Quartale echt adjusted, kein Fallback-Hinweis.
    assert Decimal(fy["numeric_value_adjusted"]) == Decimal("450")
    assert fy["adjustments_note"] is None


def test_detail_annual_adjusted_shows_mixed_quarter_sum(client, db):
    """Serve-Pfad (_derive_annual): die Annual-Zelle der Adjusted-Ansicht
    liefert die Mischsumme, sobald mindestens ein Quartal echt adjusted ist."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="detailmix@example.com")

    _seed_value(db, cid, "hohn_return_detailed", "FY", 2024, "8")
    for q, v in (("Q1", "10901"), ("Q2", "11230"), ("Q3", "11633")):
        _seed_value(db, cid, "revenue", q, 2024, v)
    _seed_value(db, cid, "revenue", "Q4", 2024, "12000", adjusted="12100")

    detail = client.get(f"/api/companies/{cid}/detail")
    assert detail.status_code == 200
    section = next(s for s in detail.json()["quarterly"] if s["value_key"] == "revenue")
    annual = section["current"]["annual"]
    assert Decimal(annual["value"]) == Decimal("45764")
    assert Decimal(annual["adjusted"]) == Decimal("45864")


def test_detail_annual_adjusted_null_without_any_quarter_adjusted(client, db):
    """Serve-Pfad-Gegenprobe: ohne jedes Q-Adjusted bleibt die Annual-
    Adjusted-Zelle NULL (UI rendert den GAAP-Fallback-Marker)."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="detailnull@example.com")

    _seed_value(db, cid, "hohn_return_detailed", "FY", 2024, "8")
    for q in ("Q1", "Q2", "Q3", "Q4"):
        _seed_value(db, cid, "revenue", q, 2024, "100")

    detail = client.get(f"/api/companies/{cid}/detail")
    assert detail.status_code == 200
    section = next(s for s in detail.json()["quarterly"] if s["value_key"] == "revenue")
    annual = section["current"]["annual"]
    assert Decimal(annual["value"]) == Decimal("400")
    assert annual["adjusted"] is None
