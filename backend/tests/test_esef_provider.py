"""ESEF-Provider: Concept-Map fuer capex und operating_cash_flow.

Gemocktes Facts-JSON (kein Netz) — Muster analog zu test_edgar_provider,
nur dass hier die ESEF-internen Fetch-Schichten (GLEIF, Filings-Liste,
Filing-JSON) gepatcht werden.
"""
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.providers.esef import ESEFProvider

YEAR = 2024


@pytest.fixture
def provider():
    return ESEFProvider()


def _fact(concept: str, value, period: str | None = None, unit: str = "iso4217:EUR") -> dict:
    return {
        "dimensions": {
            "concept": concept,
            "period": period or f"{YEAR}-01-01T00:00:00/{YEAR + 1}-01-01T00:00:00",
            "unit": unit,
        },
        "value": str(value),
    }


def _fetch(provider, facts: dict, key: str):
    filing = {
        "attributes": {
            "period_end": f"{YEAR}-12-31",
            "json_url": "/test/filing.json",
            "viewer_url": "/test/viewer",
        }
    }
    with patch.object(provider, "_resolve_isin_to_lei", return_value="LEI123"), \
         patch.object(provider, "_list_filings", return_value=[filing]), \
         patch.object(provider, "_load_filing_facts", return_value={"facts": facts}):
        return provider.fetch("ADS", key, "FY", YEAR, isin="DE000A1EWWW0")


def test_supported_keys_include_capex_and_ocf():
    keys = ESEFProvider.supported_keys
    assert "capex" in keys
    assert "operating_cash_flow" in keys


def test_operating_cash_flow_from_standard_concept(provider):
    facts = {
        "f1": _fact("ifrs-full:CashFlowsFromUsedInOperatingActivities", 5000),
    }
    result = _fetch(provider, facts, "operating_cash_flow")
    assert result is not None
    assert result.value == Decimal("5000")
    assert result.currency == "EUR"


def test_operating_cash_flow_matches_firm_extension_suffix(provider):
    """Suffix-Match ohne Namespace deckt Firm-Extensions ab."""
    facts = {
        "f1": _fact("adidas:CashFlowsFromUsedInOperatingActivities", 4200),
    }
    result = _fetch(provider, facts, "operating_cash_flow")
    assert result is not None
    assert result.value == Decimal("4200")


def test_capex_sums_ppe_and_intangibles(provider):
    """CapEx = PP&E-Kaeufe + Intangible-Kaeufe (IFRS taggt getrennt)."""
    facts = {
        "f1": _fact("ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", 500),
        "f2": _fact("ifrs-full:PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities", 100),
    }
    result = _fetch(provider, facts, "capex")
    assert result is not None
    assert result.value == Decimal("600")
    assert "CapEx" in result.source_name


def test_capex_ppe_only_when_intangibles_untagged(provider):
    facts = {
        "f1": _fact("ifrs-full:PurchaseOfPropertyPlantAndEquipment", 500),
    }
    result = _fetch(provider, facts, "capex")
    assert result is not None
    assert result.value == Decimal("500")


def test_capex_combined_concept_no_double_count(provider):
    """Kombiniertes Concept (PP&E + Intangibles in einem Tag) hat Vorrang —
    Einzel-Concepts duerfen dann nicht zusaetzlich addiert werden."""
    facts = {
        "f1": _fact("adidas:PurchasesOfIntangibleAssetsPropertyPlantAndEquipmentInvestmentProperty", 800),
        "f2": _fact("ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", 500),
        "f3": _fact("ifrs-full:PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities", 100),
    }
    result = _fetch(provider, facts, "capex")
    assert result is not None
    assert result.value == Decimal("800")


def test_capex_negative_facts_stored_positive(provider):
    """Manche Filer taggen Cash-Outflows negativ — CapEx-Ableitung nutzt
    abs(), Ergebnis ist immer positiv."""
    facts = {
        "f1": _fact("ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", -500),
        "f2": _fact("ifrs-full:PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities", -100),
    }
    result = _fetch(provider, facts, "capex")
    assert result is not None
    assert result.value == Decimal("600")


def test_capex_returns_none_without_ppe(provider):
    facts = {
        "f1": _fact("ifrs-full:ProfitLoss", 123),
    }
    result = _fetch(provider, facts, "capex")
    assert result is None


# --- fcf: MUSS dieselbe CapEx-Ableitung nutzen wie der capex-Key ----------

_OCF = "ifrs-full:CashFlowsFromUsedInOperatingActivities"
_PPE = "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"
_INTANG = "ifrs-full:PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities"
_COMBINED = "adidas:PurchasesOfIntangibleAssetsPropertyPlantAndEquipmentInvestmentProperty"


def test_fcf_subtracts_ppe_plus_intangibles(provider):
    """fcf = ocf - abs(capex) mit CapEx = PP&E + Intangibles — nicht nur
    PP&E, sonst weicht fcf strukturell vom capex-Key ab."""
    facts = {
        "f1": _fact(_OCF, 5000),
        "f2": _fact(_PPE, -500),
        "f3": _fact(_INTANG, -100),
    }
    result = _fetch(provider, facts, "fcf")
    assert result is not None
    assert result.value == Decimal("4400")


def test_fcf_uses_combined_capex_concept_first(provider):
    """Combined-Concept hat auch in der fcf-Ableitung Vorrang — die
    Einzel-Concepts duerfen nicht zusaetzlich abgezogen werden."""
    facts = {
        "f1": _fact(_OCF, 5000),
        "f2": _fact(_COMBINED, 800),
        "f3": _fact(_PPE, 500),
        "f4": _fact(_INTANG, 100),
    }
    result = _fetch(provider, facts, "fcf")
    assert result is not None
    assert result.value == Decimal("4200")


def test_fcf_structurally_consistent_with_capex_key(provider):
    """Identitaet fcf = ocf - capex haelt fuer identische Facts."""
    facts = {
        "f1": _fact(_OCF, 5000),
        "f2": _fact(_PPE, -500),
        "f3": _fact(_INTANG, -100),
    }
    fcf = _fetch(provider, facts, "fcf")
    capex = _fetch(provider, facts, "capex")
    ocf = _fetch(provider, facts, "operating_cash_flow")
    assert fcf.value == ocf.value - abs(capex.value)


def test_fcf_none_when_capex_underivable(provider):
    facts = {
        "f1": _fact(_OCF, 5000),
    }
    result = _fetch(provider, facts, "fcf")
    assert result is None


# --- Neue Keys + Ranking ---------------------------------------------------


def test_supported_keys_include_new_detail_keys():
    keys = ESEFProvider.supported_keys
    for k in ("revenue", "eps_diluted", "st_debt", "st_investments"):
        assert k in keys


def test_revenue_from_standard_concept(provider):
    facts = {"f1": _fact("ifrs-full:Revenue", 73420000000)}
    result = _fetch(provider, facts, "revenue")
    assert result is not None
    assert result.value == Decimal("73420000000")
    assert result.currency == "EUR"


def test_net_income_prefers_attributable_over_profit_loss(provider):
    """Konvention: Anteil der Mutter-Aktionaere VOR Konzern-ProfitLoss."""
    facts = {
        "f1": _fact("ifrs-full:ProfitLoss", 4960),
        "f2": _fact("ifrs-full:ProfitLossAttributableToOwnersOfParent", 5221),
    }
    result = _fetch(provider, facts, "net_income")
    assert result is not None
    assert result.value == Decimal("5221")


def test_eps_diluted_with_per_share_unit_currency(provider):
    """EPS-Unit ist ein Quotient — Waehrung kommt aus dem Zaehler."""
    facts = {
        "f1": _fact("ifrs-full:DilutedEarningsLossPerShare", "6.6",
                    unit="iso4217:EUR/xbrli:shares"),
    }
    result = _fetch(provider, facts, "eps_diluted")
    assert result is not None
    assert result.value == Decimal("6.6")
    assert result.currency == "EUR"


def test_eps_diluted_falls_back_to_basic(provider):
    facts = {
        "f1": _fact("ifrs-full:BasicEarningsLossPerShare", "6.61",
                    unit="iso4217:EUR/xbrli:shares"),
    }
    result = _fetch(provider, facts, "eps_diluted")
    assert result is not None
    assert result.value == Decimal("6.61")


def test_st_debt_and_st_investments_concepts(provider):
    facts = {
        "f1": _fact("ifrs-full:CurrentBorrowings", 1500,
                    period=f"{YEAR + 1}-01-01T00:00:00"),
        "f2": _fact("ifrs-full:OtherCurrentFinancialAssets", 2557,
                    period=f"{YEAR + 1}-01-01T00:00:00"),
    }
    assert _fetch(provider, facts, "st_debt").value == Decimal("1500")
    assert _fetch(provider, facts, "st_investments").value == Decimal("2557")


# --- Perioden-Matching (OIM: end/instant exklusiv = Stichtag + 1 Tag) ------


def test_instant_fact_matches_day_after_period_end(provider):
    """Bilanz-Instants sind als Mitternacht NACH dem Stichtag kodiert
    (FY2024 -> '2025-01-01T00:00:00'). Das alte Matching auf 'YYYY-12-31'
    fand deshalb NIE einen Bilanz-Fact."""
    facts = {
        "f1": _fact("ifrs-full:CashAndCashEquivalents", 14128,
                    period=f"{YEAR + 1}-01-01T00:00:00"),
        # Vorjahres-Stichtag darf nicht matchen
        "f0": _fact("ifrs-full:CashAndCashEquivalents", 15003,
                    period=f"{YEAR}-01-01T00:00:00"),
    }
    result = _fetch(provider, facts, "cash_and_equivalents")
    assert result is not None
    assert result.value == Decimal("14128")


def test_quarter_duration_rejected_for_fy(provider):
    """3-Monats-Fact mit gleichem Perioden-Ende ist KEIN FY-Wert."""
    facts = {
        "f1": _fact("ifrs-full:Revenue", 146,
                    period=f"{YEAR}-10-01T00:00:00/{YEAR + 1}-01-01T00:00:00"),
    }
    assert _fetch(provider, facts, "revenue") is None


def test_stub_period_rejected_for_fy(provider):
    """9-Monats-Rumpfperiode (FY-Umstellung) ist kein volles FY."""
    facts = {
        "f1": _fact("ifrs-full:Revenue", 407,
                    period=f"{YEAR}-04-01T00:00:00/{YEAR + 1}-01-01T00:00:00"),
    }
    assert _fetch(provider, facts, "revenue") is None


def test_undimensioned_fact_preferred_over_segment_fact(provider):
    """Facts mit Zusatz-Dimensionen (Equity-/Segment-Achsen) sind Teilwerte
    — der dimensionslose Gesamtwert gewinnt (Muster: DividendsPaid im
    Eigenkapitalspiegel, real bei Airbus beobachtet)."""
    seg = _fact("ifrs-full:DividendsPaid", 2000000)
    seg["dimensions"]["ifrs-full:ComponentsOfEquityAxis"] = \
        "ifrs-full:NoncontrollingInterestsMember"
    facts = {
        "f1": seg,
        "f2": _fact("ifrs-full:DividendsPaid", 2374000000),
    }
    result = _fetch(provider, facts, "dividends")
    assert result is not None
    assert result.value == Decimal("2374000000")


def test_dimensioned_fact_used_as_fallback(provider):
    """Wenn NUR dimensionierte Facts existieren, wird der erste genommen
    (besser als gar kein Wert)."""
    seg = _fact("ifrs-full:DividendsPaid", 2372000000)
    seg["dimensions"]["ifrs-full:ComponentsOfEquityAxis"] = \
        "ifrs-full:EquityAttributableToOwnersOfParentMember"
    result = _fetch(provider, {"f1": seg}, "dividends")
    assert result is not None
    assert result.value == Decimal("2372000000")


def test_non_calendar_fy_matched_via_filing_period_end(provider):
    """Filer mit abweichendem FY (z.B. 30.09.): Fact-Matching laeuft ueber
    den Stichtag des Filings, nicht ueber die Kalenderjahr-Annahme."""
    facts = {
        "f1": _fact("ifrs-full:Revenue", 75800,
                    period=f"{YEAR - 1}-10-01T00:00:00/{YEAR}-10-01T00:00:00"),
        "f2": _fact("ifrs-full:CashAndCashEquivalents", 12500,
                    period=f"{YEAR}-10-01T00:00:00"),
    }
    filing = {
        "attributes": {
            "period_end": f"{YEAR}-09-30",
            "json_url": "/test/filing.json",
            "viewer_url": "/test/viewer",
        }
    }
    with patch.object(provider, "_resolve_isin_to_lei", return_value="LEI123"), \
         patch.object(provider, "_list_filings", return_value=[filing]), \
         patch.object(provider, "_load_filing_facts", return_value={"facts": facts}):
        rev = provider.fetch("SIE", "revenue", "FY", YEAR,
                             fy_end_month=9, fy_end_day=30, isin="DE0007236101")
        cash = provider.fetch("SIE", "cash_and_equivalents", "FY", YEAR,
                              fy_end_month=9, fy_end_day=30, isin="DE0007236101")
    assert rev.value == Decimal("75800")
    assert cash.value == Decimal("12500")


# --- Entity-Aufloesung, Filing-Auswahl, Fehler-Cache (HTTP gemockt) --------
# Fixture-Strukturen sind verkleinerte ECHTE API-Antworten (GLEIF bzw.
# filings.xbrl.org JSON:API, Stand 2026-08).

LEI_AIR = "MINO79WLOO247M1IL051"

GLEIF_RESPONSE = {
    "data": [
        {"type": "lei-records", "id": LEI_AIR,
         "attributes": {"lei": LEI_AIR}}
    ],
}

GLEIF_EMPTY_RESPONSE = {"data": []}


def _filing_attrs(period_end: str, country: str = "NL", *, revision: int = 0,
                  date_added: str = "2026-02-25", json_url: str | None = "auto") -> dict:
    fxo = f"{LEI_AIR}-{period_end}-ESEF-{country}-{revision}"
    if json_url == "auto":
        json_url = f"/{LEI_AIR}/{period_end}/ESEF/{country}/{revision}/report.json"
    return {
        "type": "filing",
        "id": f"id-{fxo}",
        "attributes": {
            "fxo_id": fxo,
            "country": country,
            "period_end": period_end,
            "date_added": date_added,
            "json_url": json_url,
            "viewer_url": f"/{LEI_AIR}/{period_end}/ESEF/{country}/{revision}/ixbrlviewer.html",
        },
    }


FILINGS_RESPONSE = {
    "data": [
        _filing_attrs("2022-12-31"),
        _filing_attrs("2025-12-31"),
        _filing_attrs("2024-12-31"),
    ],
    "meta": {"count": 3},
    "jsonapi": {"version": "1.0"},
}


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_resolve_isin_to_lei_parses_gleif_response(provider):
    with patch.object(provider, "_retried_get",
                      return_value=_FakeResponse(GLEIF_RESPONSE)) as mock_get:
        lei = provider._resolve_isin_to_lei("NL0000235190")
    assert lei == LEI_AIR
    assert "filter[isin]=NL0000235190" in mock_get.call_args[0][0]


def test_resolve_isin_caches_empty_result(provider):
    """Genuin unbekannte ISIN wird als None gecacht — kein Re-Fetch."""
    with patch.object(provider, "_retried_get",
                      return_value=_FakeResponse(GLEIF_EMPTY_RESPONSE)) as mock_get:
        assert provider._resolve_isin_to_lei("FI0009000681") is None
        assert provider._resolve_isin_to_lei("FI0009000681") is None
    assert mock_get.call_count == 1


def test_list_filings_parses_jsonapi_response(provider):
    with patch.object(provider, "_retried_get",
                      return_value=_FakeResponse(FILINGS_RESPONSE)):
        filings = provider._list_filings(LEI_AIR)
    assert len(filings) == 3
    assert filings[1]["attributes"]["period_end"] == "2025-12-31"


def test_pick_filing_selects_target_year(provider):
    filings = FILINGS_RESPONSE["data"]
    picked = provider._pick_filing_for_year(filings, 2025, None, None)
    assert picked["attributes"]["period_end"] == "2025-12-31"
    # Kein 2023er-Filing vorhanden -> None statt falsches Jahr
    assert provider._pick_filing_for_year(filings, 2023, None, None) is None


def test_pick_filing_prefers_json_url_and_latest_amendment(provider):
    """Doppel-Filing gleicher Periode: ohne json_url ist das Filing nutzlos;
    bei Amendments gewinnt das juengste."""
    filings = [
        _filing_attrs("2025-12-31", json_url=None, date_added="2026-03-01"),
        _filing_attrs("2025-12-31", revision=0, date_added="2026-02-20"),
        _filing_attrs("2025-12-31", revision=1, date_added="2026-02-25"),
    ]
    picked = provider._pick_filing_for_year(filings, 2025, None, None)
    assert picked["attributes"]["fxo_id"].endswith("-1")
    assert picked["attributes"]["json_url"]


def test_fetch_fail_cache_prevents_retry_storm(provider):
    """Ausfall der Filings-Liste wird kurz negativ gecacht — die zweite
    Zelle loest KEINEN neuen HTTP-Call mit Retry-Kette aus."""
    with patch.object(provider, "_retried_get",
                      return_value=_FakeResponse(None, status_code=503)) as mock_get:
        assert provider._list_filings(LEI_AIR) == []
        assert provider._list_filings(LEI_AIR) == []
    assert mock_get.call_count == 1
    # Fehler darf NICHT als "genuin leer" im 1h-Positiv-Cache landen
    assert LEI_AIR not in provider._lei_filings_cache


def test_gleif_error_negative_cached(provider):
    with patch.object(provider, "_retried_get", return_value=None) as mock_get:
        assert provider._resolve_isin_to_lei("DE0007164600") is None
        assert provider._resolve_isin_to_lei("DE0007164600") is None
    assert mock_get.call_count == 1


def test_filing_json_error_negative_cached(provider):
    filing = FILINGS_RESPONSE["data"][1]
    with patch.object(provider, "_retried_get",
                      return_value=_FakeResponse(None, status_code=500)) as mock_get:
        assert provider._load_filing_facts(filing) is None
        assert provider._load_filing_facts(filing) is None
    assert mock_get.call_count == 1


def test_fetch_end_to_end_with_mocked_http(provider):
    """Komplette Kette GLEIF -> Filings -> Facts mit gemockten HTTP-
    Antworten in echten API-Strukturen."""
    facts_json = {
        "documentInfo": {"documentType": "https://xbrl.org/2021/xbrl-json"},
        "facts": {
            "f1": {
                "value": "73420000000.0",
                "dimensions": {
                    "concept": "ifrs-full:Revenue",
                    "entity": f"scheme:{LEI_AIR}",
                    "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
                    "unit": "iso4217:EUR",
                },
            },
        },
    }

    def route(url, *args, **kwargs):
        if "api.gleif.org" in url:
            return _FakeResponse(GLEIF_RESPONSE)
        if "/api/entities/" in url:
            return _FakeResponse(FILINGS_RESPONSE)
        if url.endswith("report.json"):
            return _FakeResponse(facts_json)
        raise AssertionError(f"unexpected URL {url}")

    with patch.object(provider, "_retried_get", side_effect=route):
        result = provider.fetch("AIR", "revenue", "FY", 2025, isin="NL0000235190")
    assert result is not None
    assert result.value == Decimal("73420000000.0")
    assert result.currency == "EUR"
    assert "ixbrlviewer" in result.source_link
