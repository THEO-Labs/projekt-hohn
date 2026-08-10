"""Tests fuer den FCF-Konsens-Pfad der FY-Guidance-Estimates:
Aggregator-/FCF-Regeln im Prompt, fcf als abfragbarer Key, die
deterministische OCF-Ableitung (fcf + |capex|) und das fcf<=ocf-Gate.
Hermetisch — der Claude-Call ist via _call_claude gemockt."""
from decimal import Decimal

import app.values.guidance_estimates as ge
from tests.test_guidance_estimates import (  # noqa: F401 - company ist Fixture
    RUNNING_YEAR,
    _fy_rows,
    _mock_claude,
    company,
)
from tests.test_guidance_gaap_basis import _entry


def test_prompt_names_aggregators_and_fcf(db, company):
    """System-Prompt nennt die Aggregator-Seiten und die
    FCF/Capex/OCF/EBITDA-Konsens-Tabellen; User-Prompt fragt fcf ab."""
    sys_prompt = ge._build_system_prompt(company, RUNNING_YEAR)
    for site in ("MarketScreener", "StockAnalysis.org", "Zacks", "TipRanks",
                 "Simply Wall St", "Yahoo Finance"):
        assert site in sys_prompt
    assert "free cash flow" in sys_prompt
    assert "estimate tables" in sys_prompt
    user_prompt = ge._build_user_prompt(company, RUNNING_YEAR)
    assert "- fcf:" in user_prompt
    assert "fcf" in ge.GUIDANCE_ESTIMATE_KEYS


def test_fcf_consensus_written_as_fy_forecast(db, company, monkeypatch):
    """FCF-Konsens landet als web_guidance-FY-Forecast."""
    _mock_claude(monkeypatch, {
        "fcf": _entry(18_000_000_000, quote="FCF consensus $18B",
                      url="https://www.marketscreener.com/tst"),
    })

    written = ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    fcf = _fy_rows(db, company, "fcf")
    assert len(fcf) == 1
    assert fcf[0].numeric_value == Decimal("18000000000")
    assert fcf[0].is_forecast is True
    assert fcf[0].primary_method == "web_guidance"
    assert "FCF consensus" in fcf[0].source_name
    assert written >= 1


def test_ocf_derived_from_fcf_plus_capex(db, company, monkeypatch):
    """FCF- und Capex-Konsens ohne OCF-Konsens: OCF = fcf + |capex| als
    calculated-Zeile (spaeter durch Derivations ersetzbar)."""
    _mock_claude(monkeypatch, {
        "fcf": _entry(18_000_000_000, quote="FCF consensus $18B"),
        # Capex negativ geliefert (Cash-Outflow-Konvention) -> abs()
        "capex": _entry(-2_000_000_000, quote="Capex consensus $2B"),
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    ocf = _fy_rows(db, company, "operating_cash_flow")
    assert len(ocf) == 1
    assert ocf[0].numeric_value == Decimal("20000000000")  # 18B + |−2B|
    assert ocf[0].primary_method == "calculated"
    assert ocf[0].source_name == "Abgeleitet: FCF-Konsens + Capex-Konsens"
    assert ocf[0].is_forecast is True


def test_ocf_not_derived_when_delivered(db, company, monkeypatch):
    """Direkter OCF-Konsens hat Vorrang vor der fcf+capex-Ableitung."""
    _mock_claude(monkeypatch, {
        "fcf": _entry(18_000_000_000),
        "capex": _entry(2_000_000_000),
        "operating_cash_flow": _entry(21_000_000_000,
                                      quote="OCF consensus $21B"),
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    ocf = _fy_rows(db, company, "operating_cash_flow")[0]
    assert ocf.numeric_value == Decimal("21000000000")
    assert ocf.primary_method == "web_guidance"
    assert "Abgeleitet" not in ocf.source_name


def test_ocf_not_derived_without_capex(db, company, monkeypatch):
    """Nur FCF ohne Capex: keine OCF-Ableitung, fcf wird geschrieben."""
    _mock_claude(monkeypatch, {
        "fcf": _entry(18_000_000_000),
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert _fy_rows(db, company, "operating_cash_flow") == []
    assert len(_fy_rows(db, company, "fcf")) == 1


def test_fcf_above_ocf_gate_discards_both(db, company, monkeypatch):
    """fcf > ocf ist unplausibel (fcf = ocf - |capex|): beide verwerfen,
    uebrige Keys bleiben."""
    _mock_claude(monkeypatch, {
        "fcf": _entry(31_000_000_000),
        "operating_cash_flow": _entry(30_000_000_000),
        "revenue": _entry(110_000_000_000, source="guidance"),
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert _fy_rows(db, company, "fcf") == []
    assert _fy_rows(db, company, "operating_cash_flow") == []
    assert len(_fy_rows(db, company, "revenue")) == 1


def test_fcf_unit_gate_applies(db, company, monkeypatch):
    """fcf unter 1 Mio (fehlende Skalierung) wird verworfen —
    bestehendes Einheiten-Gate greift auch fuer den neuen Key."""
    _mock_claude(monkeypatch, {
        "fcf": _entry(18_000, quote="18 (in millions)"),
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert _fy_rows(db, company, "fcf") == []
