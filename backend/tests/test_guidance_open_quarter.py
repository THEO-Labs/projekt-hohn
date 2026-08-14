"""Tests fuer den Open-Quarter-Block der Guidance-Estimates: der Call
fragt das Modell DIREKT nach JEDEM offenen Quartal (statt Q-Residuum aus
der FY-Schaetzung zu basteln), schreibt die Q-Werte in die
Quartals-Forecast-Slots und wendet dieselben drei Gates an (Vorjahresband
40-160%, GAAP<=NonGAAP+1%, Einheiten-Check) — pro Quartal. Bei mehreren
offenen Quartalen (Kalenderjahr-Firmen mitten im Jahr, SPGI/SAP-Muster:
Q3+Q4) traegt das Schema pro Metric einen Block je Quartalsnamen; das
Ein-Quartal-Schema (fy/open_quarter) bleibt unveraendert (Regression).
fcf ist nicht mehr abfragbar (berechnet aus OCF - |Capex|); die
gestrichenen Gates (eps x shares vs NI, fcf<=ocf) greifen nicht mehr.
Hermetisch — der Claude-Call ist via _call_claude gemockt."""
from decimal import Decimal

import app.values.guidance_estimates as ge
from app.values.models import CompanyValue
from tests.test_guidance_estimates import (  # noqa: F401 - company ist Fixture
    RUNNING_YEAR,
    _fy_rows,
    _mock_claude,
    _seed_shares,
    company,
)
from tests.test_guidance_gaap_basis import _entry

PREV_YEAR = RUNNING_YEAR - 1


def _q_rows(db, comp, key, q="Q4", year=RUNNING_YEAR):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == comp.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == q,
            CompanyValue.period_year == year,
        )
        .all()
    )


# --- Prompt/Schema --------------------------------------------------------


def test_fy_only_prompt_unchanged(db, company):
    """open_quarter=None: Prompt und Schema wie bisher — flach, ohne
    Quartals-Frage und ohne fy/open_quarter-Verschachtelung."""
    sys_prompt = ge._build_system_prompt(company, RUNNING_YEAR)
    assert "open quarter" not in sys_prompt
    user_prompt = ge._build_user_prompt(company, RUNNING_YEAR)
    assert "open_quarter" not in user_prompt
    assert '"fy"' not in user_prompt
    assert '"revenue": {"value": <number|null>' in user_prompt


def test_prompt_asks_open_quarter(db, company):
    """open_quarter gesetzt: System-Prompt fragt zusaetzlich nach dem
    offenen Quartal, das Schema traegt pro Metric einen fy- und einen
    open_quarter-Block mit denselben Feldern."""
    sys_prompt = ge._build_system_prompt(company, RUNNING_YEAR, "Q4")
    assert (
        f"Also give the estimates for the open quarter Q4 FY{RUNNING_YEAR} "
        "(the quarter not yet reported)." in sys_prompt
    )
    user_prompt = ge._build_user_prompt(company, RUNNING_YEAR, "Q4")
    assert '"revenue": {"fy": <estimate>, "open_quarter": <estimate>}' in user_prompt
    assert '"basis": "gaap"|"non_gaap"|"unclear"' in user_prompt
    assert '"reasoning": <string|null>' in user_prompt
    assert f"Q4 FY{RUNNING_YEAR} (the quarter not yet reported)" in user_prompt


def test_fcf_not_asked_anymore(db, company):
    """fcf ist berechnet (OCF - |Capex|, derive_missing_fcf) und wird
    nicht mehr abgefragt — bleibt aber in GUIDANCE_ESTIMATE_KEYS, damit
    der Refresh-Key-Loop ihn weiter am Two-Stage-Pfad vorbeifuehrt."""
    for q in (None, "Q4"):
        user_prompt = ge._build_user_prompt(company, RUNNING_YEAR, q)
        assert "fcf" not in user_prompt
    assert "fcf" in ge.GUIDANCE_ESTIMATE_KEYS
    assert "operating_cash_flow" in dict(ge._METRIC_SPECS)


def test_delivered_fcf_ignored(db, company, monkeypatch):
    """Liefert das Modell trotzdem einen fcf-Eintrag, wird er ignoriert
    (kein Schema-Key mehr) — kein fcf-Write, kein OCF-Bastel-Derivat."""
    _mock_claude(monkeypatch, {
        "fcf": _entry(18_000_000_000, quote="FCF consensus $18B"),
        "capex": _entry(2_000_000_000),
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert _fy_rows(db, company, "fcf") == []
    assert _fy_rows(db, company, "operating_cash_flow") == []
    assert len(_fy_rows(db, company, "capex")) == 1


# --- Open-Quarter-Persistenz ----------------------------------------------


def test_open_quarter_block_written_to_q_slots(db, company, monkeypatch):
    """fy- und open_quarter-Block werden geparst: FY-Werte wie bisher,
    Q-Werte in die Quartals-Forecast-Slots (web_guidance, reasoning als
    source_name, Non-GAAP-Sidecar in numeric_value_adjusted)."""
    _mock_claude(monkeypatch, {
        "revenue": {
            "fy": _entry(110_000_000_000, source="guidance",
                         reasoning="FY guidance of $110B."),
            "open_quarter": _entry(
                30_000_000_000,
                reasoning="Q4 revenue consensus sits at $30 billion.",
                url="https://example.com/q4",
            ),
        },
        "eps_diluted": {
            "fy": _entry(4.0, basis="gaap"),
            "open_quarter": _entry(1.1, basis="gaap",
                                   reasoning="Q4 GAAP EPS consensus $1.10."),
        },
        "eps_diluted_non_gaap": {
            "open_quarter": _entry(1.3, basis="non_gaap",
                                   reasoning="Q4 adjusted EPS consensus $1.30.",
                                   url="https://zacks.com/tst"),
        },
    })

    written = ge.fetch_guidance_estimates(
        db, company, RUNNING_YEAR, open_quarter="Q4",
    )
    db.commit()

    # FY-Zeilen unveraendert geschrieben
    assert _fy_rows(db, company, "revenue")[0].numeric_value == Decimal("110000000000")
    assert _fy_rows(db, company, "eps_diluted")[0].numeric_value == Decimal("4.0")

    q_rev = _q_rows(db, company, "revenue")
    assert len(q_rev) == 1
    assert q_rev[0].is_forecast is True
    assert q_rev[0].primary_method == "web_guidance"
    assert q_rev[0].numeric_value == Decimal("30000000000")
    assert q_rev[0].source_name == (
        "Q4 revenue consensus sits at $30 billion. | https://example.com/q4"
    )
    assert q_rev[0].source_link == "https://example.com/q4"
    assert q_rev[0].currency == "USD"

    # eps_diluted GAAP wird im Q-Block verworfen (Streuungs-Schutz) —
    # Traeger-Zeile mit leerem GAAP-Slot, Sidecar bleibt.
    q_eps = _q_rows(db, company, "eps_diluted")[0]
    assert q_eps.numeric_value is None
    assert q_eps.numeric_value_adjusted == Decimal("1.3")
    assert "Q4 adjusted EPS consensus" in q_eps.adjustments_source
    assert "zacks.com" in q_eps.adjustments_source
    assert q_eps.adjustments_note == "Non-GAAP Q4-Estimate (consensus)"

    # 2x FY (revenue, eps) + 1x Q4 (revenue); Sidecars/Traeger zaehlen nicht
    assert written == 3


def test_open_quarter_sidecar_without_gaap_and_ni_derivation(db, company, monkeypatch):
    """Nur Non-GAAP-EPS im Q-Block: Traeger-Zeile mit leerem GAAP-Slot,
    NI-adj-Fallback (eps_adj x shares) greift auch im Quartal."""
    _mock_claude(monkeypatch, {
        "eps_diluted_non_gaap": {
            "open_quarter": _entry(1.3, basis="non_gaap",
                                   reasoning="Q4 adjusted EPS consensus $1.30."),
        },
    })
    _seed_shares(db, company, "5000000000")

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR, open_quarter="Q4")
    db.commit()

    q_eps = _q_rows(db, company, "eps_diluted")[0]
    assert q_eps.numeric_value is None
    assert q_eps.numeric_value_adjusted == Decimal("1.3")

    q_ni = _q_rows(db, company, "net_income")[0]
    assert q_ni.numeric_value is None
    assert q_ni.numeric_value_adjusted == Decimal("6500000000")  # 1.3 x 5B
    assert "Abgeleitet: EPS-Konsens x Aktienzahl" in q_ni.adjustments_source


def test_manual_q_row_stays_untouched(db, company, monkeypatch):
    """Manual-Override im Quartals-Slot ist authoritative — der Q-Write
    ueberspringt den Key."""
    _mock_claude(monkeypatch, {
        "revenue": {
            "open_quarter": _entry(30_000_000_000),
        },
    })
    db.add(CompanyValue(
        company_id=company.id, value_key="revenue", period_type="Q4",
        period_year=RUNNING_YEAR, numeric_value=Decimal("1"),
        is_forecast=True, manually_overridden=True, source_name="Manual",
    ))
    db.commit()

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR, open_quarter="Q4")
    db.commit()

    q_rev = _q_rows(db, company, "revenue")
    assert len(q_rev) == 1
    assert q_rev[0].numeric_value == Decimal("1")
    assert q_rev[0].manually_overridden is True


def test_invalid_open_quarter_falls_back_to_fy_only(db, company, monkeypatch):
    """Ungueltiges open_quarter (z.B. "Q5"): FY-only-Verhalten."""
    calls = _mock_claude(monkeypatch, {
        "revenue": _entry(110_000_000_000, source="guidance"),
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR, open_quarter="Q5")
    db.commit()

    assert calls == [("TST", RUNNING_YEAR, None)]
    assert len(_fy_rows(db, company, "revenue")) == 1


# --- Gates fuer Q-Werte ---------------------------------------------------


def test_q_prev_year_band_gate(db, company, monkeypatch):
    """Q-Wert ausserhalb 40-160% des VORJAHRES-QUARTALS-Ist wird
    verworfen; der FY-Wert desselben Keys bleibt (eigenes Band, hier ohne
    FY-Vorjahreswert -> Gate uebersprungen)."""
    _mock_claude(monkeypatch, {
        "revenue": {
            "fy": _entry(110_000_000_000, source="guidance"),
            "open_quarter": _entry(60_000_000_000),  # 240% von 25B
        },
    })
    db.add(CompanyValue(
        company_id=company.id, value_key="revenue", period_type="Q4",
        period_year=PREV_YEAR, numeric_value=Decimal("25000000000"),
        is_forecast=False, currency="USD", primary_method="provider",
    ))
    db.commit()

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR, open_quarter="Q4")
    db.commit()

    assert _q_rows(db, company, "revenue") == []
    assert len(_fy_rows(db, company, "revenue")) == 1


def test_q_band_gate_skipped_without_prev_quarter(db, company, monkeypatch):
    """Fehlt das Vorjahres-Quartal, wird das Band-Gate uebersprungen."""
    _mock_claude(monkeypatch, {
        "revenue": {
            "open_quarter": _entry(60_000_000_000),
        },
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR, open_quarter="Q4")
    db.commit()

    assert len(_q_rows(db, company, "revenue")) == 1


def test_q_gaap_above_non_gaap_discards_both(db, company, monkeypatch):
    """GAAP > Non-GAAP + 1% gilt auch im Q-Block: beide verwerfen."""
    _mock_claude(monkeypatch, {
        "net_income": {
            "open_quarter": _entry(1_500_000_000, basis="gaap"),
        },
        "net_income_non_gaap": {
            "open_quarter": _entry(1_200_000_000, basis="non_gaap"),
        },
        "revenue": {
            "open_quarter": _entry(30_000_000_000),
        },
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR, open_quarter="Q4")
    db.commit()

    assert _q_rows(db, company, "net_income") == []
    assert len(_q_rows(db, company, "revenue")) == 1


def test_q_unit_gate(db, company, monkeypatch):
    """Einheiten-Check (< 1 Mio) greift auch fuer Q-Werte."""
    _mock_claude(monkeypatch, {
        "revenue": {
            "open_quarter": _entry(30_000, quote="30 (in millions)"),
        },
    })

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR, open_quarter="Q4")
    db.commit()

    assert _q_rows(db, company, "revenue") == []


# --- Mehrere offene Quartale (SPGI/SAP-Muster: Q3+Q4) ---------------------


def test_prompt_asks_multiple_open_quarters(db, company):
    """Zwei offene Quartale: System-Prompt fragt beide, das Schema traegt
    pro Metric einen fy-Block plus einen Block je Quartalsnamen."""
    sys_prompt = ge._build_system_prompt(company, RUNNING_YEAR, ["Q3", "Q4"])
    assert (
        "Also give the estimates for each of the open quarters Q3 and Q4 "
        f"FY{RUNNING_YEAR} (the quarters not yet reported)." in sys_prompt
    )
    user_prompt = ge._build_user_prompt(company, RUNNING_YEAR, ["Q3", "Q4"])
    assert (
        '"revenue": {"fy": <estimate>, "Q3": <estimate>, "Q4": <estimate>}'
        in user_prompt
    )
    assert "where <estimate> =" in user_prompt
    assert '"open_quarter"' not in user_prompt


def test_single_quarter_as_list_matches_str(db, company):
    """Rueckwaerts-Kompatibilitaet: ["Q4"] erzeugt exakt dieselben
    Prompts wie der bisherige str-Parameter "Q4"."""
    assert ge._build_system_prompt(company, RUNNING_YEAR, ["Q4"]) == \
        ge._build_system_prompt(company, RUNNING_YEAR, "Q4")
    assert ge._build_user_prompt(company, RUNNING_YEAR, ["Q4"]) == \
        ge._build_user_prompt(company, RUNNING_YEAR, "Q4")


def test_max_tokens_grows_per_extra_quarter(db, company):
    """Antwort-Budget waechst mit jedem weiteren offenen Quartal
    (Truncation-Lektion: abgeschnittenes JSON bei zu kleinem Budget)."""
    assert ge._max_tokens(0) == ge.MAX_TOKENS
    assert ge._max_tokens(1) == ge.MAX_TOKENS
    assert ge._max_tokens(2) == ge.MAX_TOKENS + ge.MAX_TOKENS_PER_EXTRA_QUARTER
    assert ge._max_tokens(4) == ge.MAX_TOKENS + 3 * ge.MAX_TOKENS_PER_EXTRA_QUARTER


def test_two_open_quarters_written_to_both_slots(db, company, monkeypatch):
    """Beide Q-Bloecke werden geparst und in die jeweiligen
    Quartals-Forecast-Slots geschrieben (inkl. Non-GAAP-Sidecar je
    Quartal); FY wie bisher."""
    _mock_claude(monkeypatch, {
        "revenue": {
            "fy": _entry(110_000_000_000, source="guidance",
                         reasoning="FY guidance of $110B."),
            "Q3": _entry(27_000_000_000,
                         reasoning="Q3 revenue consensus $27B.",
                         url="https://example.com/q3"),
            "Q4": _entry(30_000_000_000,
                         reasoning="Q4 revenue consensus $30B.",
                         url="https://example.com/q4"),
        },
        "eps_diluted_non_gaap": {
            "Q3": _entry(1.2, basis="non_gaap",
                         reasoning="Q3 adjusted EPS consensus $1.20."),
            "Q4": _entry(1.3, basis="non_gaap",
                         reasoning="Q4 adjusted EPS consensus $1.30."),
        },
    })

    written = ge.fetch_guidance_estimates(
        db, company, RUNNING_YEAR, open_quarter=["Q3", "Q4"],
    )
    db.commit()

    assert _fy_rows(db, company, "revenue")[0].numeric_value == Decimal("110000000000")
    for q, val, url in (("Q3", "27000000000", "q3"), ("Q4", "30000000000", "q4")):
        (q_rev,) = _q_rows(db, company, "revenue", q=q)
        assert q_rev.is_forecast is True
        assert q_rev.primary_method == "web_guidance"
        assert q_rev.numeric_value == Decimal(val)
        assert url in q_rev.source_link

    # Sidecar je Quartal: Traeger-Zeile mit leerem GAAP-Slot.
    (q3_eps,) = _q_rows(db, company, "eps_diluted", q="Q3")
    assert q3_eps.numeric_value is None
    assert q3_eps.numeric_value_adjusted == Decimal("1.2")
    assert q3_eps.adjustments_note == "Non-GAAP Q3-Estimate (consensus)"
    (q4_eps,) = _q_rows(db, company, "eps_diluted", q="Q4")
    assert q4_eps.numeric_value_adjusted == Decimal("1.3")

    # 1x FY (revenue) + 2x Q (revenue); Sidecar-Traeger zaehlen nicht.
    assert written == 3


def test_gates_apply_per_quarter(db, company, monkeypatch):
    """Vorjahresband je Quartal: der Q3-Ausreisser (vs Q3-Vorjahres-Ist)
    wird verworfen, der plausible Q4-Wert desselben Keys bleibt."""
    _mock_claude(monkeypatch, {
        "revenue": {
            "Q3": _entry(60_000_000_000),  # 240% von 25B -> Gate
            "Q4": _entry(26_000_000_000),  # 104% von 25B -> ok
        },
    })
    for q in ("Q3", "Q4"):
        db.add(CompanyValue(
            company_id=company.id, value_key="revenue", period_type=q,
            period_year=PREV_YEAR, numeric_value=Decimal("25000000000"),
            is_forecast=False, currency="USD", primary_method="provider",
        ))
    db.commit()

    ge.fetch_guidance_estimates(
        db, company, RUNNING_YEAR, open_quarter=["Q3", "Q4"],
    )
    db.commit()

    assert _q_rows(db, company, "revenue", q="Q3") == []
    (q4,) = _q_rows(db, company, "revenue", q="Q4")
    assert q4.numeric_value == Decimal("26000000000")


def test_ocf_and_eps_gaap_dropped_in_every_quarter(db, company, monkeypatch):
    """Streuungs-Schutz gilt fuer JEDES offene Quartal: OCF und der
    GAAP-Slot von eps_diluted werden in Q3 UND Q4 verworfen (Arithmetik
    im Konsistenz-Pass uebernimmt); die FY-Werte bleiben."""
    _mock_claude(monkeypatch, {
        "operating_cash_flow": {
            "fy": _entry(30_000_000_000),
            "Q3": _entry(9_000_000_000),
            "Q4": _entry(9_400_000_000),
        },
        "eps_diluted": {
            "fy": _entry(4.0, basis="gaap"),
            "Q3": _entry(1.0, basis="gaap"),
            "Q4": _entry(1.1, basis="gaap"),
        },
    })

    ge.fetch_guidance_estimates(
        db, company, RUNNING_YEAR, open_quarter=["Q3", "Q4"],
    )
    db.commit()

    assert len(_fy_rows(db, company, "operating_cash_flow")) == 1
    assert len(_fy_rows(db, company, "eps_diluted")) == 1
    for q in ("Q3", "Q4"):
        assert _q_rows(db, company, "operating_cash_flow", q=q) == []
        assert _q_rows(db, company, "eps_diluted", q=q) == []


def test_duplicates_and_invalid_quarters_normalized(db, company, monkeypatch):
    """Duplikate werden dedupliziert, ungueltige Eintraege verworfen —
    der Call laeuft mit der bereinigten Liste weiter (kein FY-only-
    Fallback wie beim komplett ungueltigen str)."""
    calls = _mock_claude(monkeypatch, {
        "revenue": {
            "fy": _entry(110_000_000_000, source="guidance"),
            "Q4": _entry(30_000_000_000),
        },
    })

    ge.fetch_guidance_estimates(
        db, company, RUNNING_YEAR, open_quarter=["Q4", "Q4", "Q5"],
    )
    db.commit()

    assert calls == [("TST", RUNNING_YEAR, ["Q4"])]
    assert len(_q_rows(db, company, "revenue", q="Q4")) == 1
    assert len(_fy_rows(db, company, "revenue")) == 1


# --- Gestrichene Gates ----------------------------------------------------


def test_eps_ni_gate_removed(db, company, monkeypatch):
    """Das eps x shares vs net_income-Gate ist gestrichen: beide Werte
    werden trotz rechnerischer Abweichung geschrieben."""
    _mock_claude(monkeypatch, {
        "eps_diluted": _entry(10.0, basis="gaap"),
        "net_income": _entry(20_000_000_000, basis="gaap"),
    })
    _seed_shares(db, company, "5000000000")  # 10 x 5B = 50B vs 20B

    ge.fetch_guidance_estimates(db, company, RUNNING_YEAR)
    db.commit()

    assert _fy_rows(db, company, "eps_diluted")[0].numeric_value == Decimal("10.0")
    assert _fy_rows(db, company, "net_income")[0].numeric_value == Decimal("20000000000")


def test_fcf_ocf_gate_and_ocf_derivation_removed(db, company):
    """fcf<=ocf-Gate und die OCF-aus-FCF-Ableitung sind ersatzlos raus."""
    assert not hasattr(ge, "_derive_ocf_from_fcf_capex")
    assert not hasattr(ge, "_EPS_NI_TOL")
