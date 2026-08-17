from decimal import Decimal

from app.values.models import CompanyValue
from app.values.orchestrator import AnchorValue, ValueOrchestrator


class FakePerplexity:
    def fetch_period(self, **k):
        return {}

    def fetch_consensus(self, **k):
        return {}


def _rows(db, cid, key, year):
    return db.query(CompanyValue).filter_by(company_id=cid, value_key=key, period_year=year).all()


def test_edgar_values_persisted_1to1(db, us_company):
    orch = ValueOrchestrator(
        db=db,
        stammdaten_fetch=lambda c: {"market_cap": (Decimal("1000"), "USD"),
                                     "stock_price": (Decimal("10"), "USD"),
                                     "shares_outstanding": (Decimal("100"), "USD")},
        edgar_fetch=lambda c, years: {("net_income", 2024): AnchorValue(Decimal("500"),
                                       "SEC EDGAR", "https://sec.gov/x", "USD")},
        perplexity=FakePerplexity(),
        history_years=1,
    )
    orch.run(us_company)
    r = _rows(db, us_company.id, "net_income", 2024)
    assert len(r) == 1 and r[0].numeric_value == Decimal("500")
    assert r[0].source_name == "SEC EDGAR" and r[0].primary_method == "provider"
    # Stammdaten liegen als SNAPSHOT (period_year=None), nicht als FY
    mc = db.query(CompanyValue).filter_by(company_id=us_company.id, value_key="market_cap",
                                          period_type="SNAPSHOT", period_year=None).one()
    assert mc.numeric_value == Decimal("1000") and mc.source_name == "Market Data Feed"


def test_manual_override_never_touched(db, us_company):
    db.add(CompanyValue(company_id=us_company.id, value_key="net_income", period_type="FY",
                        period_year=2024, numeric_value=Decimal("999"),
                        source_name="Manual Override", manually_overridden=True,
                        primary_method="manual"))
    db.flush()
    orch = ValueOrchestrator(
        db=db, stammdaten_fetch=lambda c: {},
        edgar_fetch=lambda c, years: {("net_income", 2024): AnchorValue(Decimal("500"),
                                       "SEC EDGAR", "https://sec.gov/x", "USD")},
        perplexity=FakePerplexity(), history_years=1)
    orch.run(us_company)
    r = _rows(db, us_company.id, "net_income", 2024)
    assert len(r) == 1 and r[0].numeric_value == Decimal("999")  # unveraendert


def test_edgar_refresh_updates_provider_row(db, us_company):
    def run_with(val):
        ValueOrchestrator(
            db=db, stammdaten_fetch=lambda c: {},
            edgar_fetch=lambda c, years: {("net_income", 2024): AnchorValue(
                Decimal(val), "SEC EDGAR", "https://sec.gov/x", "USD")},
            perplexity=FakePerplexity(), history_years=1).run(us_company)
    run_with("500")
    run_with("600")
    r = _rows(db, us_company.id, "net_income", 2024)
    assert len(r) == 1 and r[0].numeric_value == Decimal("600")  # refresh aktualisiert


def test_perplexity_method_cannot_overwrite_provider(db, us_company):
    orch = ValueOrchestrator(
        db=db, stammdaten_fetch=lambda c: {},
        edgar_fetch=lambda c, years: {("net_income", 2024): AnchorValue(
            Decimal("500"), "SEC EDGAR", "https://sec.gov/x", "USD")},
        perplexity=FakePerplexity(), history_years=1)
    orch.run(us_company)
    orch._upsert(us_company.id, "net_income", 2024, value=Decimal("111"),
                 source_name="Perplexity", source_link="https://x", currency="USD",
                 primary_method="perplexity")
    r = _rows(db, us_company.id, "net_income", 2024)
    assert len(r) == 1 and r[0].numeric_value == Decimal("500") and r[0].primary_method == "provider"


def test_actual_deletes_manual_forecast_twin(db, us_company):
    db.add(CompanyValue(company_id=us_company.id, value_key="net_income", period_type="FY",
                        period_year=2024, numeric_value=Decimal("42"), is_forecast=True,
                        source_name="Manual Override", manually_overridden=True,
                        primary_method="manual"))
    db.flush()
    ValueOrchestrator(
        db=db, stammdaten_fetch=lambda c: {},
        edgar_fetch=lambda c, years: {("net_income", 2024): AnchorValue(
            Decimal("500"), "SEC EDGAR", "https://sec.gov/x", "USD")},
        perplexity=FakePerplexity(), history_years=1).run(us_company)
    rows = _rows(db, us_company.id, "net_income", 2024)
    assert len(rows) == 1 and rows[0].is_forecast is False and rows[0].numeric_value == Decimal("500")


def _q(db, cid, key, year, period, val, forecast=False, method="provider"):
    from uuid import uuid4
    db.add(CompanyValue(id=uuid4(), company_id=cid, value_key=key, period_type=period,
                        period_year=year, numeric_value=Decimal(str(val)), is_forecast=forecast,
                        source_name="x", primary_method=method, manually_overridden=False))
    db.flush()


def test_yoy_q4_estimate_anchors_to_runrate(db, us_company):
    """Q4 = Q4(Vorjahr) x YTD-Wachstum, verankert an berichtete Quartale."""
    orch = ValueOrchestrator(db=db, stammdaten_fetch=lambda c: {},
                             edgar_fetch=lambda c, y: {}, perplexity=FakePerplexity(),
                             history_years=2)
    cid = us_company.id
    for p, v in [("Q1", 5853), ("Q2", 6021), ("Q3", 5628)]:
        _q(db, cid, "net_income", 2026, p, v)
    for p, v in [("Q1", 5119), ("Q2", 4577), ("Q3", 5272), ("Q4", 5090)]:
        _q(db, cid, "net_income", 2025, p, v)
    # Q4 = 5090 * (17502/14968) = 5951.6...
    est = orch._yoy_q4_estimate(cid, "net_income", 2026)
    assert 5900 < est < 6000
    # ohne Vorjahres-Q4 -> None (Perplexity-Fallback)
    assert orch._yoy_q4_estimate(cid, "revenue", 2026) is None
