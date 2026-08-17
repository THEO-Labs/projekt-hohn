"""Filing-XBRL-Provider: Parsing der XBRL-Instanz + Extraktions-Modi, und die
Orchestrator-Bridge, die daraus exakte Quartalswerte macht (YTD-Differenz)."""
from datetime import date
from decimal import Decimal

from app.providers.edgar_filing import EdgarFilingProvider, FilingQuarter
from app.values.models import CompanyValue
from app.values.orchestrator import ValueOrchestrator

# Minimal-Instanz: 3M-Revenue (dimensionslos + segmentiert), 9M-YTD-OCF,
# Bilanz-Instant. Der segmentierte Revenue-Fact MUSS ignoriert werden.
_XBRL = b"""<?xml version="1.0"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance"
      xmlns:us-gaap="http://fasb.org/us-gaap/2026"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi">
  <context id="q3">
    <entity><identifier scheme="cik">0001403161</identifier></entity>
    <period><startDate>2026-04-01</startDate><endDate>2026-06-30</endDate></period>
  </context>
  <context id="q3seg">
    <entity><identifier scheme="cik">0001403161</identifier>
      <segment><xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassAMember</xbrldi:explicitMember></segment>
    </entity>
    <period><startDate>2026-04-01</startDate><endDate>2026-06-30</endDate></period>
  </context>
  <context id="ytd">
    <entity><identifier scheme="cik">0001403161</identifier></entity>
    <period><startDate>2025-10-01</startDate><endDate>2026-06-30</endDate></period>
  </context>
  <context id="bal">
    <entity><identifier scheme="cik">0001403161</identifier></entity>
    <period><instant>2026-06-30</instant></period>
  </context>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="q3" unitRef="usd">11633000000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="q3seg" unitRef="usd">5000000000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
  <us-gaap:NetCashProvidedByUsedInOperatingActivities contextRef="ytd" unitRef="usd">16342000000</us-gaap:NetCashProvidedByUsedInOperatingActivities>
  <us-gaap:LongTermDebtCurrent contextRef="bal" unitRef="usd">2996000000</us-gaap:LongTermDebtCurrent>
  <us-gaap:Investments contextRef="bal" unitRef="usd">1433000000</us-gaap:Investments>
</xbrl>"""


def test_parse_ignores_segmented_and_maps_concepts():
    facts = EdgarFilingProvider._parse_instance(_XBRL)
    rev = facts["RevenueFromContractWithCustomerExcludingAssessedTax"]
    # Nur der dimensionslose Fact bleibt (segmentierter verworfen).
    assert len(rev) == 1
    assert rev[0][1] == Decimal("11633000000")


def test_duration_picks_quarter_and_ytd():
    p = EdgarFilingProvider()
    facts = p._parse_instance(_XBRL)
    end = date(2026, 6, 30)
    # 3M-Quartal (Span ~90 Tage)
    rev = p._duration(facts, ["RevenueFromContractWithCustomerExcludingAssessedTax"],
                      end=end, min_days=80, max_days=100)
    assert rev == Decimal("11633000000")
    # YTD (Span ~270 Tage) — 3M-Fenster darf es NICHT greifen
    assert p._duration(facts, ["NetCashProvidedByUsedInOperatingActivities"],
                       end=end, min_days=80, max_days=100) is None
    ytd = p._duration(facts, ["NetCashProvidedByUsedInOperatingActivities"],
                      end=end, min_days=60, max_days=400, start=date(2025, 10, 1))
    assert ytd == Decimal("16342000000")


def test_instant_and_investments_fallback():
    p = EdgarFilingProvider()
    facts = p._parse_instance(_XBRL)
    at = date(2026, 6, 30)
    assert p._instant(facts, ["LongTermDebtCurrent"], at=at) == Decimal("2996000000")
    # st_investments faellt auf generisches "Investments" zurueck (Visa-Fall)
    from app.providers.edgar_filing import _concepts
    assert p._instant(facts, _concepts("st_investments"), at=at) == Decimal("1433000000")


def test_consensus_most_common():
    d = Decimal
    assert EdgarFilingProvider._consensus([d("5"), d("5"), d("7")]) == d("5")
    assert EdgarFilingProvider._consensus([]) is None


# ---- Orchestrator-Bridge: YTD -> Standalone-Quartal -----------------------

class _FakeFilingProvider:
    def fetch_quarter(self, **k):
        return FilingQuarter(
            quarter_values={"revenue": Decimal("11633000000"),
                            "net_income": Decimal("5628000000")},
            ytd_values={"operating_cash_flow": Decimal("16342000000")},
            balance_values={"st_debt": Decimal("2996000000"),
                            "lt_debt": Decimal("20862000000")},
            diluted_shares=None,
            source_url="https://sec.gov/filing",
            quarter_end=date(2026, 6, 30),
            fy_start=date(2025, 10, 1),
        )


def _orch(db):
    return ValueOrchestrator(
        db=db, stammdaten_fetch=lambda c: {}, edgar_fetch=lambda c, y: {},
        perplexity=None, history_years=2, filing_provider=_FakeFilingProvider(),
    )


def _seed_quarter(db, cid, key, year, q, val):
    from uuid import uuid4
    db.add(CompanyValue(id=uuid4(), company_id=cid, value_key=key, period_type=q,
                        period_year=year, numeric_value=Decimal(str(val)),
                        source_name="SEC EDGAR", primary_method="provider",
                        is_forecast=False, manually_overridden=False))
    db.flush()


def test_bridge_from_filing_differences_ytd(db, us_company, monkeypatch):
    # Filing-Pfad in diesem Test aktivieren (conftest deaktiviert ihn global).
    monkeypatch.setattr(ValueOrchestrator, "_filing", lambda self: self._filing_provider)
    cid = us_company.id
    # Q1, Q2 OCF liegen als EDGAR-Standalone vor -> Q3 = YTD - (Q1+Q2)
    _seed_quarter(db, cid, "operating_cash_flow", 2026, "Q1", "6780000000")
    _seed_quarter(db, cid, "operating_cash_flow", 2026, "Q2", "3008000000")
    orch = _orch(db)
    filled = orch._bridge_from_filing(us_company, 2026, "Q3", "USD")
    db.flush()

    def q(key, period):
        return db.query(CompanyValue).filter_by(
            company_id=cid, value_key=key, period_year=2026, period_type=period).one()

    # Income-Statement: direkter 3M-Wert
    assert q("revenue", "Q3").numeric_value == Decimal("11633000000")
    assert q("revenue", "Q3").primary_method == "provider"
    # Cashflow: 16342 - (6780+3008) = 6554
    assert q("operating_cash_flow", "Q3").numeric_value == Decimal("6554000000")
    # Bilanz-Instant fuer Carry-Forward
    assert q("st_debt", "Q3").numeric_value == Decimal("2996000000")
    assert "operating_cash_flow" in filled and "st_debt" in filled


def test_bridge_skips_ytd_when_priors_missing(db, us_company, monkeypatch):
    monkeypatch.setattr(ValueOrchestrator, "_filing", lambda self: self._filing_provider)
    # Nur Q1 vorhanden -> Q3-OCF kann nicht korrekt differenziert werden -> skip
    _seed_quarter(db, us_company.id, "operating_cash_flow", 2026, "Q1", "6780000000")
    orch = _orch(db)
    orch._bridge_from_filing(us_company, 2026, "Q3", "USD")
    db.flush()
    ocf_q3 = db.query(CompanyValue).filter_by(
        company_id=us_company.id, value_key="operating_cash_flow",
        period_year=2026, period_type="Q3").one_or_none()
    assert ocf_q3 is None  # nicht geschrieben (unvollstaendige Vorquartale)
