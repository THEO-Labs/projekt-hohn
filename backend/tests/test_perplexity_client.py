import json

import httpx
import respx

from app.llm.perplexity import PerplexityClient, PerplexityValue

# Perplexity Sonar chat/completions: content liegt in choices[0].message.content,
# Quellen in citations.
_URL = "https://api.perplexity.ai/chat/completions"


def _resp(payload: dict, citations):
    return {
        "choices": [{"message": {"role": "assistant", "content": json.dumps(payload)}}],
        "citations": citations,
        "search_results": [],
    }


@respx.mock
def test_fetch_period_parses_values_and_citation():
    route = respx.post(_URL).mock(return_value=httpx.Response(
        200, json=_resp({"net_income": 1234.0, "net_income_adjusted": 1300.0, "capex": None},
                        ["https://www.sec.gov/Archives/edgar/x.htm"])))
    c = PerplexityClient(api_key="pk", model="sonar-pro")
    out = c.fetch_period(company_name="Acme Inc", ticker="ACME", fiscal_year=2024,
                         missing_keys=["net_income", "capex"], currency="USD")
    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "sonar-pro"
    assert body["messages"][0]["role"] == "user"
    assert body["response_format"]["type"] == "json_schema"
    assert body["search_domain_filter"] == ["sec.gov"]
    assert body["web_search_options"]["search_context_size"] == "high"
    assert out["net_income"] == PerplexityValue(value=1234.0, adjusted=1300.0,
                                                source_url="https://www.sec.gov/Archives/edgar/x.htm",
                                                source_title=None)
    assert "capex" not in out  # null -> weggelassen


@respx.mock
def test_fetch_consensus_has_no_domain_filter():
    route = respx.post(_URL).mock(return_value=httpx.Response(
        200, json=_resp({"revenue": 5000.0}, ["https://finance.example.com/x"])))
    c = PerplexityClient(api_key="pk", model="sonar-pro")
    out = c.fetch_consensus(company_name="Acme", ticker="ACME", forward_year=2026,
                            keys=["revenue"], currency="USD")
    body = json.loads(route.calls[0].request.content)
    assert "search_domain_filter" not in body
    assert out["revenue"].value == 5000.0


@respx.mock
def test_api_error_raises_clean():
    respx.post(_URL).mock(return_value=httpx.Response(500))
    c = PerplexityClient(api_key="pk", model="sonar-pro")
    import pytest
    with pytest.raises(Exception):
        c.fetch_period(company_name="A", ticker="A", fiscal_year=2024,
                       missing_keys=["revenue"], currency="USD")
