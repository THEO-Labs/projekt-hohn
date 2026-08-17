import respx, httpx, json
from app.llm.perplexity import PerplexityClient, PerplexityValue

def _resp(payload: dict, citations):
    return {"output_text": json.dumps(payload), "citations": citations, "search_results": []}

@respx.mock
def test_fetch_period_parses_values_and_citation():
    route = respx.post("https://api.perplexity.ai/v1/agent").mock(return_value=httpx.Response(
        200, json=_resp({"net_income": 1234.0, "net_income_adjusted": 1300.0, "capex": None},
                        ["https://www.sec.gov/Archives/edgar/x.htm"])))
    c = PerplexityClient(api_key="pk", model="sonar-pro")
    out = c.fetch_period(company_name="Acme Inc", ticker="ACME", fiscal_year=2024,
                         missing_keys=["net_income", "capex"], currency="USD")
    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "sonar-pro"
    assert body["response_format"]["type"] == "json_schema"
    assert body["tools"][0]["type"] == "web_search"
    assert body["tools"][0]["filters"]["search_domain_filter"] == ["sec.gov"]
    assert out["net_income"] == PerplexityValue(value=1234.0, adjusted=1300.0,
                                                source_url="https://www.sec.gov/Archives/edgar/x.htm",
                                                source_title=None)
    assert "capex" not in out  # null -> weggelassen

@respx.mock
def test_fetch_consensus_has_no_domain_filter():
    respx.post("https://api.perplexity.ai/v1/agent").mock(return_value=httpx.Response(
        200, json=_resp({"revenue": 5000.0}, ["https://finance.example.com/x"])))
    c = PerplexityClient(api_key="pk", model="sonar-pro")
    out = c.fetch_consensus(company_name="Acme", ticker="ACME", forward_year=2026,
                            keys=["revenue"], currency="USD")
    assert out["revenue"].value == 5000.0

@respx.mock
def test_api_error_raises_clean():
    respx.post("https://api.perplexity.ai/v1/agent").mock(return_value=httpx.Response(500))
    c = PerplexityClient(api_key="pk", model="sonar-pro")
    import pytest
    with pytest.raises(Exception):
        c.fetch_period(company_name="A", ticker="A", fiscal_year=2024,
                       missing_keys=["revenue"], currency="USD")
