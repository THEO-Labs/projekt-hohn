"""Tests fuer den chat-simplen Guidance-Prompt und das reasoning-Feld:
natuerliche Frage ohne Verbots-/Regellisten, reasoning als sichtbarer
Quellentext der Zelle (source_name/adjustments_source, reasoning-first,
Fallback quote). Hermetisch — der Claude-Call ist via _call_claude
gemockt."""
from decimal import Decimal

import app.values.guidance_estimates as ge
from tests.test_guidance_estimates import (  # noqa: F401 - company ist Fixture
    RUNNING_YEAR,
    _fy_rows,
    _mock_claude,
    company,
)
from tests.test_guidance_gaap_basis import _entry


def test_prompt_is_a_simple_chat_question(db, company):
    """System-Prompt ist eine natuerliche Frage: Basis-Satz, reasoning-
    Bitte, ein Einheiten-Satz, JSON-only — keine Regellisten."""
    sys_prompt = ge._build_system_prompt(company, RUNNING_YEAR)
    assert sys_prompt.startswith(
        f"What are the current fiscal year {RUNNING_YEAR}"
    )
    assert "guidance and analyst consensus" in sys_prompt
    assert "one-to-two-sentence reasoning" in sys_prompt
    assert "base units" in sys_prompt
    # Keine Verbots-/Regellisten mehr
    for banned in ("FORBIDDEN", "Sources in this order", "(a)", "ONLY if",
                   "no projections"):
        assert banned not in sys_prompt
    # Budget 900 statt 700: der Spin-off-/Divestiture-Satz (SPGI-Review)
    # kostet rund 180 Zeichen zusaetzlich.
    assert len(sys_prompt) < 900

    user_prompt = ge._build_user_prompt(company, RUNNING_YEAR)
    assert '"reasoning": <string|null>' in user_prompt
    assert "which guidance/consensus figure the value rests" in user_prompt


def test_prompt_mentions_spin_off_awareness(db, company):
    """Ein Satz fuer Konzernumbauten: Schaetzungen fuer den verbleibenden
    Konzern nach Spin-off/Divestiture/Grossakquisition, mit Hinweis im
    reasoning (SPGI-Review: Mobility-Abspaltung wurde weitergerechnet)."""
    sys_prompt = ge._build_system_prompt(company, RUNNING_YEAR)
    assert (
        "If the company has completed or announced a spin-off, "
        "divestiture or major acquisition, base the estimates on the "
        "remaining/post-transaction company and say so in the reasoning."
        in sys_prompt
    )


def test_reasoning_becomes_source_name(db, company, monkeypatch):
    """reasoning ist der sichtbare Quellentext: source_name =
    '<reasoning> | <url>'."""
    _mock_claude(monkeypatch, {
        "revenue": _entry(
            110_000_000_000, source="guidance", basis="gaap", quote=None,
            reasoning="Management guided to about $110 billion for the "
                      "full year and consensus sits right at that level.",
            url="https://example.com/ir/guidance",
        ),
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    rev = _fy_rows(db, company, "revenue")[0]
    assert rev.source_name == (
        "Management guided to about $110 billion for the full year and "
        "consensus sits right at that level. | https://example.com/ir/guidance"
    )
    assert rev.source_link == "https://example.com/ir/guidance"


def test_reasoning_first_in_adjusted_sidecar(db, company, monkeypatch):
    """Sidecar-Quelle ist reasoning-first und bleibt ueberschreibbar
    (beginnt nicht mit https)."""
    _mock_claude(monkeypatch, {
        "eps_diluted_non_gaap": _entry(
            5.0, basis="non_gaap", quote="ignored quote",
            reasoning="Consensus adjusted EPS is $5.00 across analysts.",
            url="https://zacks.com/tst",
        ),
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    eps = _fy_rows(db, company, "eps_diluted")[0]
    assert eps.numeric_value_adjusted == Decimal("5.0")
    assert eps.adjustments_source.startswith(
        "Consensus adjusted EPS is $5.00 across analysts."
    )
    assert "zacks.com" in eps.adjustments_source
    assert not eps.adjustments_source.startswith("https://")


def test_source_name_falls_back_to_quote_then_label(db, company, monkeypatch):
    """Ohne reasoning greift quote, ohne beides das generische Label."""
    _mock_claude(monkeypatch, {
        "revenue": _entry(110_000_000_000, source="guidance", basis="gaap",
                          quote="FY revenue guidance of $110 billion",
                          reasoning=None),
        "operating_cash_flow": _entry(30_000_000_000, quote=None,
                                      reasoning=None),
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    rev = _fy_rows(db, company, "revenue")[0]
    assert rev.source_name.startswith("FY revenue guidance of $110 billion")
    ocf = _fy_rows(db, company, "operating_cash_flow")[0]
    assert ocf.source_name == "FY-Guidance (consensus)"


def test_self_derived_gaap_estimate_accepted(db, company, monkeypatch):
    """Regel-Lockerung: das Modell darf GAAP selbst herleiten (wie im
    Gemini-Referenzfall) — Wert mit basis=gaap + Herleitungs-reasoning
    wird geschrieben, solange GAAP <= NonGAAP + 1%."""
    _mock_claude(monkeypatch, {
        "eps_diluted": _entry(
            3.15, basis="gaap", quote=None,
            reasoning="Non-GAAP consensus is $3.44; GAAP typically runs "
                      "about 6-9% lower, implying roughly $3.10-3.25.",
        ),
        "eps_diluted_non_gaap": _entry(3.44, basis="non_gaap"),
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    eps = _fy_rows(db, company, "eps_diluted")[0]
    assert eps.numeric_value == Decimal("3.15")
    assert eps.numeric_value_adjusted == Decimal("3.44")
    assert "typically runs" in eps.source_name
