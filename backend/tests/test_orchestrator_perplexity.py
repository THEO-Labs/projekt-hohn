from decimal import Decimal

from app.values.orchestrator import ValueOrchestrator
from app.llm.perplexity import PerplexityValue
from app.values.models import CompanyValue


class FakePplx:
    def __init__(self):
        self.period_calls = []
        self.consensus_calls = []

    def fetch_period(self, *, company_name, ticker, fiscal_year, missing_keys, currency):
        self.period_calls.append((fiscal_year, tuple(sorted(missing_keys))))
        if "revenue" in missing_keys:
            return {"revenue": PerplexityValue(Decimal("900"), Decimal("950"),
                                               "https://sec.gov/r", None)}
        return {}

    def fetch_consensus(self, *, company_name, ticker, forward_year, keys, currency):
        self.consensus_calls.append(forward_year)
        return {"revenue": PerplexityValue(Decimal("1100"), None, "https://x/c", None)}


def test_perplexity_fills_only_missing(db, us_company):
    pplx = FakePplx()
    orch = ValueOrchestrator(
        db=db, stammdaten_fetch=lambda c: {},
        edgar_fetch=lambda c, years: {},   # nichts vom Anker -> alles offen
        perplexity=pplx, history_years=2)
    orch.run(us_company)
    rev = db.query(CompanyValue).filter_by(company_id=us_company.id, value_key="revenue").all()
    assert any(r.source_name == "Perplexity" and r.numeric_value == Decimal("900") for r in rev)
    assert any(r.numeric_value_adjusted == Decimal("950") for r in rev)
    assert any(r.is_forecast and r.primary_method == "perplexity_consensus" for r in rev)


def test_perplexity_skips_keys_already_anchored(db, us_company):
    from app.values.orchestrator import AnchorValue
    pplx = FakePplx()
    orch = ValueOrchestrator(
        db=db, stammdaten_fetch=lambda c: {},
        edgar_fetch=lambda c, years: {("revenue", years[0]): AnchorValue(
            Decimal("777"), "SEC EDGAR", "https://sec.gov/e", "USD")},
        perplexity=pplx, history_years=2)
    orch.run(us_company)
    anchored_year = orch.target_years(us_company)[0]
    assert all("revenue" not in keys for (fy, keys) in pplx.period_calls if fy == anchored_year)
