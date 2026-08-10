"""Tests fuer die GAAP/Non-GAAP-Trennung der FY-Guidance-Estimates:
basis-Feld-Handling, Umbuchung nicht explizit ausgewiesener GAAP-Werte
in die adjusted-Sidecars, deterministische NI-adj-Ableitung
(eps_adj x shares) und das GAAP<=Non-GAAP-Gate. Hermetisch — der
Claude-Call ist via _call_claude gemockt."""
from decimal import Decimal

import app.values.guidance_estimates as ge
from app.values.persistence import adjusted_is_protected
from tests.test_guidance_estimates import (  # noqa: F401 - company ist Fixture
    RUNNING_YEAR,
    _fy_rows,
    _mock_claude,
    _seed_shares,
    company,
)


def _entry(value, source="consensus", basis="unclear", quote="q", url=None):
    return {"value": value, "source": source, "basis": basis,
            "quote": quote, "url": url}


def test_basis_non_gaap_rebooks_gaap_slot_to_adjusted(db, company, monkeypatch):
    """eps/NI mit basis=non_gaap im GAAP-Slot: Umbuchung in den Sidecar,
    GAAP-Slot bleibt leer (Traeger-Zeile ohne numeric_value)."""
    _mock_claude(monkeypatch, {
        "eps_diluted": _entry(4.0, basis="non_gaap",
                              quote="Adjusted EPS consensus $4.00"),
        "net_income": _entry(20_000_000_000, basis="non_gaap",
                             quote="Adjusted net income consensus $20B"),
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    eps = _fy_rows(db, company, "eps_diluted")
    assert len(eps) == 1
    assert eps[0].numeric_value is None
    assert eps[0].numeric_value_adjusted == Decimal("4.0")
    assert "Adjusted EPS consensus" in eps[0].adjustments_source
    ni = _fy_rows(db, company, "net_income")
    assert len(ni) == 1
    assert ni[0].numeric_value is None
    assert ni[0].numeric_value_adjusted == Decimal("20000000000")


def test_basis_unclear_never_lands_in_gaap(db, company, monkeypatch):
    """basis=unclear (oder fehlend) im GAAP-Slot: nur adjusted zulaessig."""
    _mock_claude(monkeypatch, {
        "eps_diluted": _entry(4.0, basis="unclear"),
        # basis fehlt komplett -> wie unclear behandeln
        "net_income": {"value": 20_000_000_000, "source": "consensus",
                       "quote": "q", "url": None},
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    eps = _fy_rows(db, company, "eps_diluted")[0]
    assert eps.numeric_value is None
    assert eps.numeric_value_adjusted == Decimal("4.0")
    ni = _fy_rows(db, company, "net_income")[0]
    assert ni.numeric_value is None
    assert ni.numeric_value_adjusted == Decimal("20000000000")


def test_rebook_with_occupied_sidecar_discards_gaap_slot_value(db, company, monkeypatch):
    """GAAP-Slot ohne explizite GAAP-Basis, Sidecar schon geliefert:
    der gelieferte Sidecar-Wert gewinnt, der GAAP-Slot-Wert wird verworfen."""
    _mock_claude(monkeypatch, {
        "eps_diluted": _entry(3.9, basis="unclear"),
        "eps_diluted_non_gaap": _entry(4.1, basis="non_gaap",
                                       quote="Non-GAAP EPS consensus $4.10"),
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    eps = _fy_rows(db, company, "eps_diluted")[0]
    assert eps.numeric_value is None
    assert eps.numeric_value_adjusted == Decimal("4.1")


def test_only_non_gaap_consensus_derives_adjusted_ni(db, company, monkeypatch):
    """Nur Non-GAAP-EPS-Konsens: adjusted-Spur wird geschrieben, NI adj
    deterministisch als eps_adj x shares abgeleitet, GAAP-Slots leer."""
    _mock_claude(monkeypatch, {
        "eps_diluted_non_gaap": _entry(5.0, basis="non_gaap",
                                       quote="Non-GAAP EPS consensus $5.00",
                                       url="https://zacks.com/tst"),
        "revenue": _entry(110_000_000_000, source="guidance", basis="gaap"),
    })
    _seed_shares(db, company, "5000000000")

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    eps = _fy_rows(db, company, "eps_diluted")[0]
    assert eps.numeric_value is None
    assert eps.numeric_value_adjusted == Decimal("5.0")

    ni = _fy_rows(db, company, "net_income")[0]
    assert ni.numeric_value is None
    assert ni.numeric_value_adjusted == Decimal("25000000000")  # 5.0 x 5B
    assert "Abgeleitet: EPS-Konsens x Aktienzahl" in ni.adjustments_source
    # Ableitung bleibt fuer den naechsten Lauf ueberschreibbar
    assert not adjusted_is_protected(ni.adjustments_source)

    assert _fy_rows(db, company, "revenue")[0].numeric_value == Decimal("110000000000")


def test_no_ni_derivation_without_snapshot_shares(db, company, monkeypatch):
    """Ohne SNAPSHOT-Aktienzahl keine NI-Ableitung — Luecke ist ehrlich."""
    _mock_claude(monkeypatch, {
        "eps_diluted_non_gaap": _entry(5.0, basis="non_gaap"),
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert _fy_rows(db, company, "net_income") == []
    eps = _fy_rows(db, company, "eps_diluted")[0]
    assert eps.numeric_value_adjusted == Decimal("5.0")


def test_delivered_ni_adj_beats_derivation(db, company, monkeypatch):
    """Direkter NI-adj-Konsens hat Vorrang vor der eps x shares-Ableitung."""
    _mock_claude(monkeypatch, {
        "eps_diluted_non_gaap": _entry(5.0, basis="non_gaap"),
        "net_income_non_gaap": _entry(24_000_000_000, basis="non_gaap",
                                      quote="Adjusted NI consensus $24B"),
    })
    _seed_shares(db, company, "5000000000")

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    ni = _fy_rows(db, company, "net_income")[0]
    assert ni.numeric_value_adjusted == Decimal("24000000000")
    assert "Abgeleitet" not in ni.adjustments_source


def test_gaap_above_non_gaap_discards_both(db, company, monkeypatch):
    """GAAP > Non-GAAP + 1% (US-Standard verletzt): beide Werte verwerfen."""
    _mock_claude(monkeypatch, {
        "eps_diluted": _entry(5.0, basis="gaap",
                              quote="GAAP EPS guidance $5.00"),
        "eps_diluted_non_gaap": _entry(4.0, basis="non_gaap"),
        "revenue": _entry(110_000_000_000, source="guidance", basis="gaap"),
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert _fy_rows(db, company, "eps_diluted") == []
    assert len(_fy_rows(db, company, "revenue")) == 1


def test_gaap_within_tolerance_keeps_both(db, company, monkeypatch):
    """GAAP <= Non-GAAP + 1%: beide Werte bleiben (Rundungsfaelle)."""
    _mock_claude(monkeypatch, {
        "eps_diluted": _entry(4.03, basis="gaap",
                              quote="GAAP EPS guidance $4.03"),
        "eps_diluted_non_gaap": _entry(4.0, basis="non_gaap"),
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    eps = _fy_rows(db, company, "eps_diluted")[0]
    assert eps.numeric_value == Decimal("4.03")
    assert eps.numeric_value_adjusted == Decimal("4.0")


def test_explicit_gaap_basis_lands_in_gaap_slot(db, company, monkeypatch):
    """Explizit als GAAP ausgewiesener Konsens landet im GAAP-Slot."""
    _mock_claude(monkeypatch, {
        "eps_diluted": _entry(4.0, basis="gaap",
                              quote="GAAP EPS guidance of $4.00",
                              url="https://example.com/ir"),
    })

    written = ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert written == 1
    eps = _fy_rows(db, company, "eps_diluted")[0]
    assert eps.numeric_value == Decimal("4.0")
    assert eps.numeric_value_adjusted is None
    assert eps.primary_method == "web_guidance"


def test_prompt_mentions_basis_and_gaap_rules(db, company):
    """Prompts enthalten basis-Schema und die GAAP/Non-GAAP-Regeln."""
    sys_prompt = ge._build_system_prompt(company, RUNNING_YEAR)
    assert "NON-GAAP" in sys_prompt
    assert "'basis'" in sys_prompt
    assert "EXPLICITLY" in sys_prompt
    user_prompt = ge._build_user_prompt(company, RUNNING_YEAR)
    assert '"basis": "gaap"|"non_gaap"|"unclear"' in user_prompt
