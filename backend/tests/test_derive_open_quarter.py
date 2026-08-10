"""derive_open_quarter_from_fy_estimate: das offene Rest-Quartal wird
deterministisch aus dem FY-Estimate (Guidance/Konsens) berechnet:
Q_offen = FY_est - Summe(berichtete Quartale). Ersetzt LLM-Schaetzungen
des offenen Quartals; manuelle/PDF-Zeilen bleiben unangetastet."""
from decimal import Decimal

import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.companies.models import Company
from app.portfolios.models import Portfolio
from app.values.consistency import derive_open_quarter_from_fy_estimate
from app.values.models import CompanyValue

YEAR = 2026


@pytest.fixture
def company(db):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email="openq@example.com", password_hash=hash_password("pw1234"))
    db.add(user)
    db.flush()
    portfolio = Portfolio(name="P", owner_user_id=user.id)
    db.add(portfolio)
    db.flush()
    comp = Company(
        portfolio_id=portfolio.id, name="TestCo", ticker="TST",
        currency="USD", isin="US0001234567",
    )
    db.add(comp)
    db.commit()
    return comp


def _seed(db, comp, key, ptype, value, is_forecast=False, year=YEAR, **kw):
    row = CompanyValue(
        company_id=comp.id, value_key=key, period_type=ptype, period_year=year,
        numeric_value=Decimal(str(value)) if value is not None else None,
        is_forecast=is_forecast, source_name=kw.pop("source_name", "seed"),
        primary_method=kw.pop("primary_method", "provider"),
        currency=kw.pop("currency", "USD"), **kw,
    )
    db.add(row)
    db.commit()
    return row


def _open_q_row(db, comp, key, ptype):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == ptype,
            CompanyValue.period_year == YEAR,
            CompanyValue.is_forecast.is_(True),
        )
        .one_or_none()
    )


def test_derives_open_q4_and_overwrites_llm_estimate(db, company):
    """FY-Estimate + drei berichtete Quartale: das LLM-geschaetzte Q4 wird
    durch das deterministische Residuum ersetzt."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "net_income", q, v)
    _seed(db, company, "net_income", "Q4", 999, is_forecast=True,
          primary_method="web_guidance")
    _seed(db, company, "net_income", "FY", 460, is_forecast=True,
          primary_method="two_stage_confirmed")

    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 1
    row = _open_q_row(db, company, "net_income", "Q4")
    assert row.numeric_value == Decimal("130")
    assert row.primary_method == "calculated"
    assert row.is_forecast is True
    assert row.source_name.startswith("FY-Guidance minus berichtete Quartale")
    assert "460" in row.source_name


def test_creates_missing_open_quarter_row(db, company):
    """Fehlt die Zeile des offenen Quartals ganz, wird sie angelegt."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "revenue", q, v)
    _seed(db, company, "revenue", "FY", 500, is_forecast=True,
          primary_method="web_guidance")

    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 1
    row = _open_q_row(db, company, "revenue", "Q4")
    assert row is not None
    assert row.numeric_value == Decimal("170")
    assert row.currency == "USD"


def test_manual_estimate_not_overwritten(db, company):
    """Manuelle Zeilen des offenen Quartals sind authoritative."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "net_income", q, v)
    manual = _seed(db, company, "net_income", "Q4", 200, is_forecast=True,
                   primary_method="manual", manually_overridden=True)
    _seed(db, company, "net_income", "FY", 460, is_forecast=True)

    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 0
    db.refresh(manual)
    assert manual.numeric_value == Decimal("200")
    assert manual.primary_method == "manual"


def test_skips_when_more_than_one_quarter_open(db, company):
    """Zwei offene Quartale: das Residuum ist nicht eindeutig verteilbar."""
    for q, v in (("Q1", 100), ("Q2", 110)):
        _seed(db, company, "net_income", q, v)
    _seed(db, company, "net_income", "FY", 460, is_forecast=True)

    assert derive_open_quarter_from_fy_estimate(db, company.id, YEAR) == 0
    assert _open_q_row(db, company, "net_income", "Q3") is None
    assert _open_q_row(db, company, "net_income", "Q4") is None


def test_skips_when_fy_actual_exists(db, company):
    """Abgeschlossenes Jahr (FY-Actual mit Wert): nichts abzuleiten."""
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "net_income", q, v)
    _seed(db, company, "net_income", "FY", 470)
    _seed(db, company, "net_income", "FY", 460, is_forecast=True)

    assert derive_open_quarter_from_fy_estimate(db, company.id, YEAR) == 0


def test_skips_when_no_fy_estimate(db, company):
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "net_income", q, v)

    assert derive_open_quarter_from_fy_estimate(db, company.id, YEAR) == 0


def test_negative_residual_for_always_positive_key_skipped(db, company):
    """Stale FY-Guidance unter der Summe der berichteten Quartale wuerde
    ein negatives Dividenden-Quartal ergeben — wird nicht persistiert."""
    for q, v in (("Q1", 200), ("Q2", 200), ("Q3", 200)):
        _seed(db, company, "dividends", q, v)
    _seed(db, company, "dividends", "FY", 500, is_forecast=True)

    assert derive_open_quarter_from_fy_estimate(db, company.id, YEAR) == 0
    assert _open_q_row(db, company, "dividends", "Q4") is None


# --- Berichtet-Kriterium fuer US-Filer: ein beendetes Quartal mit
# Item-2.02-8-K (bzw. abgelaufener Karenz) gilt nicht als offen. ------------


CIK = "0000789019"
SUB_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"


def _submissions(filing_date, items="2.02,9.01"):
    return {"filings": {"recent": {
        "form": ["8-K"],
        "filingDate": [filing_date.isoformat()],
        "accessionNumber": ["0000789019-90-000001"],
        "primaryDocument": ["doc0.htm"],
        "items": [items],
    }}}


def _in_grace_setup(db, company, days_back=20):
    """FY-Ende so setzen, dass Q4 vor `days_back` Tagen endete (beendet,
    Karenz nicht um), plus Q1-Q3-Actuals, Q4-LLM-Forecast und FY-Estimate."""
    from datetime import date, timedelta

    p_end = date.today() - timedelta(days=days_back)
    company.fiscal_year_end_month = p_end.month
    company.fiscal_year_end_day = p_end.day
    db.commit()
    year = p_end.year
    for q, v in (("Q1", 100), ("Q2", 110), ("Q3", 120)):
        _seed(db, company, "net_income", q, v, year=year)
    est = _seed(db, company, "net_income", "Q4", 999, is_forecast=True,
                primary_method="web_guidance", year=year)
    _seed(db, company, "net_income", "FY", 460, is_forecast=True,
          primary_method="two_stage_confirmed", year=year)
    return year, p_end, est


def test_quarter_with_8k_not_treated_as_open(db, company, monkeypatch):
    """Q4 beendet, Karenz nicht um, Item-2.02-8-K existiert: das Quartal
    gilt als berichtet — die FY-Guidance-Ableitung ueberschreibt die
    Zelle NICHT (Actual kommt gleich per Bruecke/XBRL)."""
    from datetime import timedelta

    import app.values.adjusted_enrichment as adj

    year, p_end, est = _in_grace_setup(db, company)
    subs = _submissions(p_end + timedelta(days=5))
    monkeypatch.setattr(adj, "_resolve_cik", lambda ticker: CIK)
    monkeypatch.setattr(adj, "_fetch_json",
                        lambda url: subs if url == SUB_URL else None)

    written = derive_open_quarter_from_fy_estimate(db, company.id, year)
    db.commit()

    assert written == 0
    db.refresh(est)
    assert est.numeric_value == Decimal("999")
    assert est.primary_method == "web_guidance"


def test_quarter_without_8k_still_derived(db, company, monkeypatch):
    """Q4 beendet, Karenz nicht um, KEIN Item-2.02-8-K (nur 5.02): das
    Quartal ist offen — die Ableitung laeuft wie bisher."""
    from datetime import timedelta

    import app.values.adjusted_enrichment as adj

    year, p_end, est = _in_grace_setup(db, company)
    subs = _submissions(p_end + timedelta(days=5), items="5.02")
    monkeypatch.setattr(adj, "_resolve_cik", lambda ticker: CIK)
    monkeypatch.setattr(adj, "_fetch_json",
                        lambda url: subs if url == SUB_URL else None)

    written = derive_open_quarter_from_fy_estimate(db, company.id, year)
    db.commit()

    assert written == 1
    db.refresh(est)
    assert est.numeric_value == Decimal("130")
    assert est.primary_method == "calculated"


def test_fetch_error_falls_back_to_grace_rule(db, company, monkeypatch):
    """Submissions-Fetch schlaegt fehl (CIK None, conftest-Default):
    konservativ gilt die Karenz-Regel — Karenz nicht um -> Quartal offen,
    Ableitung laeuft."""
    year, _, est = _in_grace_setup(db, company)

    written = derive_open_quarter_from_fy_estimate(db, company.id, year)
    db.commit()

    assert written == 1
    db.refresh(est)
    assert est.numeric_value == Decimal("130")


def test_grace_passed_quarter_not_treated_as_open(db, company, monkeypatch):
    """Karenz abgelaufen (Q4-Ende 60 Tage her): das Quartal gilt fuer
    US-Filer als berichtet — kein Netz-Call noetig, keine Ableitung."""
    import app.values.adjusted_enrichment as adj

    year, _, est = _in_grace_setup(db, company, days_back=60)

    def _boom(ticker):
        raise AssertionError("Karenz abgelaufen — kein Submissions-Fetch noetig")

    monkeypatch.setattr(adj, "_resolve_cik", _boom)
    written = derive_open_quarter_from_fy_estimate(db, company.id, year)
    db.commit()

    assert written == 0
    db.refresh(est)
    assert est.numeric_value == Decimal("999")


def test_non_us_ended_quarter_still_derived(db, company, monkeypatch):
    """Nicht-US-Firma: beendetes Quartal bleibt wie bisher offen — keine
    EDGAR-Anfrage, Ableitung unveraendert."""
    import app.values.adjusted_enrichment as adj

    company.isin = "DE0001234567"
    year, _, est = _in_grace_setup(db, company)

    def _boom(ticker):
        raise AssertionError("non-US darf EDGAR nicht anfragen")

    monkeypatch.setattr(adj, "_resolve_cik", _boom)
    written = derive_open_quarter_from_fy_estimate(db, company.id, year)
    db.commit()

    assert written == 1
    db.refresh(est)
    assert est.numeric_value == Decimal("130")


def test_negative_residual_for_net_income_allowed(db, company):
    """Fuer nicht-always-positive Keys (net_income) ist ein negatives
    Residuum legitim (Verlustquartal laut Guidance)."""
    for q, v in (("Q1", 200), ("Q2", 200), ("Q3", 200)):
        _seed(db, company, "net_income", q, v)
    _seed(db, company, "net_income", "FY", 550, is_forecast=True)

    written = derive_open_quarter_from_fy_estimate(db, company.id, YEAR)
    db.commit()

    assert written == 1
    row = _open_q_row(db, company, "net_income", "Q4")
    assert row.numeric_value == Decimal("-50")
