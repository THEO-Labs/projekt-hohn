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
