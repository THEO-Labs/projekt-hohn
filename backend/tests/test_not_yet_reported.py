"""Tests fuer "noch nicht berichtet"-Zellen im Detail-Endpoint.

Zellen des laufenden Jahres, deren Zahlen die Firma noch gar nicht
veroeffentlicht haben kann (Quartalsende in der Zukunft oder juenger als
die Reporting-Frist), bekommen status='not_yet_reported' statt not_found.
"""
from datetime import date
from decimal import Decimal

from app.auth.models import User
from app.auth.security import hash_password
from app.values.catalog import SEED_VALUES
from app.values.detail_page import is_not_yet_reported, quarter_end_date
from app.values.models import CompanyValue


# --- quarter_end_date (Kalender- und versetztes Fiskaljahr) ----------------


def test_quarter_end_calendar_fy():
    # FY-Ende 31.12. -> Kalenderquartale
    assert quarter_end_date(2026, "Q1", 12, 31) == date(2026, 3, 31)
    assert quarter_end_date(2026, "Q2", 12, 31) == date(2026, 6, 30)
    assert quarter_end_date(2026, "Q3", 12, 31) == date(2026, 9, 30)
    assert quarter_end_date(2026, "Q4", 12, 31) == date(2026, 12, 31)


def test_quarter_end_fallback_without_fy_end():
    # Ohne bekanntes FY-Ende: Kalenderquartale
    assert quarter_end_date(2026, "Q1", None, None) == date(2026, 3, 31)
    assert quarter_end_date(2026, "Q4", None, None) == date(2026, 12, 31)


def test_quarter_end_september_fy():
    # FY-Ende 30.09. (z.B. Apple-artig): Q4 = FY-Ende, Q1 endet im Dezember
    # des VORjahres (Fiskaljahr-Versatz, gleiche Konvention wie edgar._q_end_date).
    assert quarter_end_date(2026, "Q4", 9, 30) == date(2026, 9, 30)
    assert quarter_end_date(2026, "Q3", 9, 30) == date(2026, 6, 30)
    assert quarter_end_date(2026, "Q2", 9, 30) == date(2026, 3, 30)
    assert quarter_end_date(2026, "Q1", 9, 30) == date(2025, 12, 30)


def test_quarter_end_day_clamping():
    # FY-Ende 31.12.: Q3 endet im September -> Tag auf 30 geklemmt
    assert quarter_end_date(2026, "Q3", 12, 31) == date(2026, 9, 30)
    # FY-Ende 31.05.: Q1 endet im August (31 Tage) -> bleibt 31
    assert quarter_end_date(2026, "Q1", 5, 31) == date(2025, 8, 31)
    # Ungueltiges FY-Ende -> None
    assert quarter_end_date(2026, "Q4", 2, 30) is None
    assert quarter_end_date(2026, "QX", 12, 31) is None


# --- is_not_yet_reported ---------------------------------------------------


def test_not_yet_reported_calendar_fy():
    today = date(2026, 7, 29)
    # Q4 endet 31.12.2026 (Zukunft) -> noch nicht berichtet
    assert is_not_yet_reported(2026, "Q4", 12, 31, today) is True
    # Q3 endet 30.09.2026 (Zukunft) -> noch nicht berichtet
    assert is_not_yet_reported(2026, "Q3", 12, 31, today) is True
    # Q2 endete 30.06.2026 (29 Tage her, innerhalb der 45-Tage-Frist)
    assert is_not_yet_reported(2026, "Q2", 12, 31, today) is True
    # Q1 endete 31.03.2026 (laengst faellig) -> False
    assert is_not_yet_reported(2026, "Q1", 12, 31, today) is False
    # Vorjahr komplett faellig
    assert is_not_yet_reported(2025, "Q4", 12, 31, today) is False


def test_not_yet_reported_grace_boundary():
    # Q2 endet 30.06.2026; Frist = 45 Tage
    q_end = date(2026, 6, 30)
    assert is_not_yet_reported(2026, "Q2", 12, 31, q_end) is True
    # 44 Tage danach: noch innerhalb der Frist
    assert is_not_yet_reported(2026, "Q2", 12, 31, date(2026, 8, 13)) is True
    # Genau 45 Tage danach: faellig
    assert is_not_yet_reported(2026, "Q2", 12, 31, date(2026, 8, 14)) is False


def test_not_yet_reported_september_fy():
    today = date(2026, 7, 29)
    # FY2026 endet 30.09.2026: Q1 endete 30.12.2025 -> faellig
    assert is_not_yet_reported(2026, "Q1", 9, 30, today) is False
    # Q2 endete 30.03.2026 -> faellig
    assert is_not_yet_reported(2026, "Q2", 9, 30, today) is False
    # Q3 endete 30.06.2026 (29 Tage) -> noch nicht berichtet
    assert is_not_yet_reported(2026, "Q3", 9, 30, today) is True
    # Q4 endet 30.09.2026 (Zukunft) -> noch nicht berichtet
    assert is_not_yet_reported(2026, "Q4", 9, 30, today) is True


def test_not_yet_reported_none_year():
    assert is_not_yet_reported(None, "Q4", 12, 31, date(2026, 7, 29)) is False


# --- Endpoint --------------------------------------------------------------


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


def _login_with_company(client, db, email="t@example.com"):
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})
    p = client.post("/api/portfolios", json={"name": "P"}).json()
    # Nicht-US-Firma (Wolters Kluwer) direkt per ORM — API-Neuanlage
    # gesperrt (ISIN-Pflicht + Nicht-US-Block). DE-ISIN haelt
    # is_us_company==False / IFRS.
    from uuid import UUID
    from app.companies.models import Company
    company = Company(portfolio_id=UUID(p["id"]), name="Wolters", ticker="WKL",
                      currency="EUR", isin="DE0007164600")
    db.add(company)
    db.commit()
    return user, p["id"], str(company.id)


def _seed(db, cid, key, period_type, period_year, value=None, primary_method=None):
    db.add(CompanyValue(
        company_id=cid,
        value_key=key,
        period_type=period_type,
        period_year=period_year,
        numeric_value=Decimal(str(value)) if value is not None else None,
        source_name="Test",
        primary_method=primary_method,
    ))
    db.commit()


def test_detail_endpoint_marks_current_year_cells_not_yet_reported(client, db):
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db)
    cy = date.today().year
    py = cy - 1

    # current_fy_estimate_year = cy (Firma ohne FY-Ende -> Kalenderquartale)
    _seed(db, cid, "hohn_return_detailed", "FY", cy, value=8)
    # Laufendes Jahr: Q4 (endet 31.12., immer Zukunft) als not_found recherchiert
    _seed(db, cid, "revenue", "Q4", cy, value=None, primary_method="not_found")
    # net_income Q4 hat gar keine Zeile -> ebenfalls noch nicht berichtet
    # Vorjahr: Q1 not_found (laengst faellig) bleibt not_found
    _seed(db, cid, "revenue", "Q1", py, value=None, primary_method="not_found")

    detail = client.get(f"/api/companies/{cid}/detail")
    assert detail.status_code == 200
    body = detail.json()
    sections = {s["value_key"]: s for s in body["quarterly"]}

    # Laufendes Jahr, not_found-Zeile mit Quartalsende in der Zukunft ->
    # status statt not_found
    rev_q4 = sections["revenue"]["current"]["q4"]
    assert rev_q4["status"] == "not_yet_reported"
    assert rev_q4["primary_method"] != "not_found"
    assert rev_q4["value"] is None

    # Laufendes Jahr, fehlende Zeile mit Quartalsende in der Zukunft
    ni_q4 = sections["net_income"]["current"]["q4"]
    assert ni_q4["status"] == "not_yet_reported"

    # Vorjahr: laengst faellige Periode bleibt not_found bzw. leer
    prior_q1 = sections["revenue"]["prior"]["q1"]
    assert prior_q1["status"] is None
    assert prior_q1["primary_method"] == "not_found"
    prior_q2 = sections["net_income"]["prior"]["q2"]
    assert prior_q2["status"] is None


def test_researched_value_on_not_found_row_is_never_masked(client, db):
    """Research aktualisiert numeric_value aber nicht primary_method —
    eine not_found-Zeile MIT Wert muss den Wert zeigen, nicht den Status."""
    _seed_catalog(db)
    _user, _pid, cid = _login_with_company(client, db, email="mask@example.com")
    year = date.today().year
    # current_fy_estimate_year auf das laufende Jahr pinnen (wie im Test oben)
    _seed(db, cid, "hohn_return_detailed", "FY", year, value=8)
    _seed(db, cid, "revenue", "Q4", year, value=123000000, primary_method="not_found")
    detail = client.get(f"/api/companies/{cid}/detail").json()
    section = next(s for s in detail["quarterly"] if s["value_key"] == "revenue")
    q4 = section["current"]["q4"]
    assert q4["value"] is not None
    assert q4.get("status") is None
