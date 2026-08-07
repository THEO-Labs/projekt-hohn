"""apply_to_db (Two-Stage-Pipeline) muss dieselben Schreibpfad-Invarianten
einhalten wie der FY-Refresh: Sign-Normalisierung fuer ALWAYS_POSITIVE_KEYS
und ein Currency-Label, das den geschriebenen Wert beschreibt."""

from decimal import Decimal
from uuid import UUID

from app.auth.models import User
from app.auth.security import hash_password
from app.values.models import CompanyValue
from scripts.two_stage_research import (
    ExtractResult,
    QuarterValue,
    TwoStageResult,
    VerifierVerdict,
    apply_to_db,
)


def _company(client, db, email="ts@example.com"):
    from tests.test_values import _seed_catalog
    _seed_catalog(db)
    user = User(email=email, password_hash=hash_password("pw1234"))
    db.add(user)
    db.commit()
    client.post("/api/auth/login", json={"email": email, "password": "pw1234"})
    pid = client.post("/api/portfolios", json={"name": "P"}).json()["id"]
    c = client.post(
        f"/api/portfolios/{pid}/companies",
        json={"name": "TestCo", "ticker": "TST", "currency": "EUR"},
    ).json()
    return UUID(c["id"])


def _result(value_key: str, fy_value: Decimal) -> TwoStageResult:
    extract = ExtractResult(
        ticker="TST", value_key=value_key, year=2025, currency="EUR",
        q1=None, q2=None, q3=None, q4=None,
        fy=QuarterValue(value=fy_value, source_quote="FY figure from AR", source_url=None,
                        is_estimate=False),
        quarter_only=None, is_adjusted_note=None,
    )
    verdict = VerifierVerdict(
        verdict="confirm", corrections={}, reason="reconciles", confidence=0.9, flags=[],
    )
    return TwoStageResult(extract=extract, verdict=verdict)


def _fy_row(db, cid: UUID, key: str) -> CompanyValue:
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == cid,
            CompanyValue.value_key == key,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == 2025,
        )
        .one()
    )


def test_apply_to_db_skip_stamps_refresh_attempt(client, db):
    """Hard-Skip (keine Evidenz) laesst den alten Wert stehen — muss aber
    last_refresh_attempt stempeln, sonst sieht stale Data ewig frisch aus."""
    cid = _company(client, db, email="ts3@example.com")
    stale = CompanyValue(
        company_id=cid, value_key="sbc", period_type="FY", period_year=2025,
        numeric_value=Decimal("95000000"), source_name="old contaminated row",
    )
    db.add(stale)
    db.commit()

    no_evidence = _result("sbc", Decimal("120000000"))
    no_evidence.extract.fy.source_quote = None
    apply_to_db(db, cid, "sbc", 2025, no_evidence, currency="EUR")
    db.commit()

    row = _fy_row(db, cid, "sbc")
    assert row.numeric_value == Decimal("95000000")
    assert row.last_refresh_attempt is not None


def test_apply_to_db_all_null_creates_not_found_placeholders(client, db):
    """Recherche ohne Fund und ohne bestehende Zeilen: Platzhalter mit
    primary_method='not_found', damit das UI die Zelle rot markieren kann
    (= manuell raussuchen)."""
    cid = _company(client, db, email="ts6@example.com")
    empty = _result("sbc", Decimal("1"))
    empty.extract.fy = None
    apply_to_db(db, cid, "sbc", 2024, empty, currency="EUR")
    db.commit()

    rows = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == cid,
            CompanyValue.value_key == "sbc",
            CompanyValue.period_year == 2024,
        )
        .all()
    )
    assert len(rows) == 5
    assert all(r.numeric_value is None for r in rows)
    assert all(r.primary_method == "not_found" for r in rows)
    assert all(r.last_refresh_attempt is not None for r in rows)


def test_successful_write_replaces_not_found_placeholder(client, db):
    cid = _company(client, db, email="ts7@example.com")
    placeholder = CompanyValue(
        company_id=cid, value_key="revenue", period_type="FY", period_year=2025,
        numeric_value=None, primary_method="not_found", source_name="No source found",
    )
    db.add(placeholder)
    db.commit()

    apply_to_db(db, cid, "revenue", 2025, _result("revenue", Decimal("50000")), currency="EUR")
    db.commit()
    row = _fy_row(db, cid, "revenue")
    assert row.numeric_value == Decimal("50000")
    assert row.primary_method.startswith("two_stage")


def test_apply_to_db_all_null_result_stamps_existing_rows(client, db):
    """Extractor liefert alle Perioden null (sbc-Fall im dritten adidas-Lauf):
    apply_to_db darf nicht lautlos nichts tun — bestehende Zeilen muessen den
    Refresh-Versuch gestempelt bekommen."""
    cid = _company(client, db, email="ts4@example.com")
    stale = CompanyValue(
        company_id=cid, value_key="sbc", period_type="FY", period_year=2025,
        numeric_value=Decimal("95000000"), source_name="old row",
    )
    db.add(stale)
    db.commit()

    empty = _result("sbc", Decimal("1"))
    empty.extract.fy = None
    apply_to_db(db, cid, "sbc", 2025, empty, currency="EUR")
    db.commit()

    row = _fy_row(db, cid, "sbc")
    assert row.numeric_value == Decimal("95000000")
    assert row.last_refresh_attempt is not None


def test_apply_to_db_forces_forecast_for_unfinished_fy(client, db):
    """FY-Wert eines noch nicht beendeten Geschaeftsjahres kann kein Actual
    sein — auch wenn der Extractor is_estimate=false behauptet (OCF-Fall)."""
    from datetime import date

    cid = _company(client, db, email="ts5@example.com")
    running_year = date.today().year
    r = _result("operating_cash_flow", Decimal("751000000"))
    r.extract.year = running_year
    apply_to_db(db, cid, "operating_cash_flow", running_year, r, currency="EUR")
    db.commit()

    row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == cid,
            CompanyValue.value_key == "operating_cash_flow",
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == running_year,
        )
        .one()
    )
    assert row.is_forecast is True


def test_apply_to_db_normalizes_sign(client, db):
    cid = _company(client, db)
    apply_to_db(db, cid, "buyback_volume", 2025, _result("buyback_volume", Decimal("-2000")),
                currency="EUR")
    db.commit()
    assert _fy_row(db, cid, "buyback_volume").numeric_value == Decimal("2000")


def test_apply_to_db_updates_currency_label_on_overwrite(client, db):
    # Der Extractor liefert explizit Firmenwaehrung — beim Ueberschreiben muss
    # ein fehlendes Currency-Label dem neuen Wert folgen. (Ein ABWEICHENDES
    # Label ist ein Currency-Konflikt und blockt den Write, siehe
    # test_apply_to_db_currency_conflict_blocks_write.)
    cid = _company(client, db, email="ts2@example.com")
    stale = CompanyValue(
        company_id=cid, value_key="revenue", period_type="FY", period_year=2025,
        numeric_value=Decimal("999"), currency=None, source_name="old provider row",
    )
    db.add(stale)
    db.commit()

    apply_to_db(db, cid, "revenue", 2025, _result("revenue", Decimal("50000")), currency="EUR")
    db.commit()

    row = _fy_row(db, cid, "revenue")
    assert row.numeric_value == Decimal("50000")
    assert row.currency == "EUR"


def test_apply_to_db_currency_conflict_blocks_write(client, db):
    """Punkt 7: bestehende Zeile mit anderem Currency-Label wird NICHT
    ueberschrieben (Konsistenz mit routes/_anchor) — nur der Refresh-Versuch
    wird gestempelt."""
    cid = _company(client, db, email="ts8@example.com")
    stale = CompanyValue(
        company_id=cid, value_key="revenue", period_type="FY", period_year=2025,
        numeric_value=Decimal("999"), currency="USD", source_name="old provider row",
    )
    db.add(stale)
    db.commit()

    apply_to_db(db, cid, "revenue", 2025, _result("revenue", Decimal("50000")), currency="EUR")
    db.commit()

    row = _fy_row(db, cid, "revenue")
    assert row.numeric_value == Decimal("999")
    assert row.currency == "USD"
    assert row.last_refresh_attempt is not None


def _pair(db, cid, key, year=2025, actual=Decimal("111"), forecast=Decimal("222")):
    """Actual+Forecast-Zeilenpaar fuer dieselbe Zelle (Unique-Index erlaubt 2)."""
    a = CompanyValue(
        company_id=cid, value_key=key, period_type="FY", period_year=year,
        numeric_value=actual, is_forecast=False, source_name="actual row",
    )
    f = CompanyValue(
        company_id=cid, value_key=key, period_type="FY", period_year=year,
        numeric_value=forecast, is_forecast=True, source_name="forecast row",
    )
    db.add_all([a, f])
    db.commit()
    return a, f


def test_apply_to_db_pair_actual_result_targets_actual_row(client, db):
    """Punkt 1: Actual+Forecast-Paar pro Zelle darf nicht crashen
    (scalar_one_or_none -> MultipleResultsFound). Ein Actual-Ergebnis
    (is_estimate=False) aktualisiert die Actual-Zeile."""
    cid = _company(client, db, email="ts9@example.com")
    a, f = _pair(db, cid, "revenue")

    apply_to_db(db, cid, "revenue", 2025, _result("revenue", Decimal("50000")), currency="EUR")
    db.commit()
    db.refresh(a)
    db.refresh(f)

    assert a.numeric_value == Decimal("50000")
    assert a.primary_method.startswith("two_stage")
    assert f.numeric_value == Decimal("222")  # Forecast-Zeile unangetastet


def test_apply_to_db_pair_estimate_result_targets_forecast_row(client, db):
    """Punkt 1: ein Estimate-Ergebnis (is_estimate=True) bevorzugt die zur
    is_estimate passende Forecast-Zeile."""
    cid = _company(client, db, email="ts10@example.com")
    a, f = _pair(db, cid, "revenue")

    r = _result("revenue", Decimal("50000"))
    r.extract.fy.is_estimate = True
    apply_to_db(db, cid, "revenue", 2025, r, currency="EUR")
    db.commit()
    db.refresh(a)
    db.refresh(f)

    assert f.numeric_value == Decimal("50000")
    assert a.numeric_value == Decimal("111")  # Actual-Zeile unangetastet


def test_apply_to_db_pair_hard_skip_stamps_both_rows(client, db):
    """Punkt 1: auch der Stempel-Pfad (keine Evidenz) muss paarfest sein —
    beide Zeilen der Zelle bekommen last_refresh_attempt."""
    cid = _company(client, db, email="ts11@example.com")
    a, f = _pair(db, cid, "sbc")

    no_evidence = _result("sbc", Decimal("120000000"))
    no_evidence.extract.fy.source_quote = None
    apply_to_db(db, cid, "sbc", 2025, no_evidence, currency="EUR")
    db.commit()
    db.refresh(a)
    db.refresh(f)

    assert a.numeric_value == Decimal("111")
    assert f.numeric_value == Decimal("222")
    assert a.last_refresh_attempt is not None
    assert f.last_refresh_attempt is not None


def test_apply_to_db_never_touches_manual_override(client, db):
    """Punkt 3: manuell ueberschriebene Zeilen sind authoritative — Wert und
    Flag bleiben, nur last_refresh_attempt wird gestempelt."""
    cid = _company(client, db, email="ts12@example.com")
    manual = CompanyValue(
        company_id=cid, value_key="revenue", period_type="FY", period_year=2025,
        numeric_value=Decimal("999"), manually_overridden=True,
        primary_method="manual", source_name="manual row",
    )
    db.add(manual)
    db.commit()

    apply_to_db(db, cid, "revenue", 2025, _result("revenue", Decimal("50000")), currency="EUR")
    db.commit()
    db.refresh(manual)

    assert manual.numeric_value == Decimal("999")
    assert manual.manually_overridden is True
    assert manual.primary_method == "manual"
    assert manual.last_refresh_attempt is not None


def test_apply_to_db_preserves_manual_adjusted_fields(client, db):
    """C1-Regression: Zeile mit manuellem Adjusted-Wert (adjustments_source
    ='Manual', GAAP-Teil NICHT manually_overridden) — der Two-Stage-Write
    aktualisiert den GAAP-Wert, laesst die Adjusted-Felder aber
    byte-identisch stehen."""
    cid = _company(client, db, email="ts16@example.com")
    row = CompanyValue(
        company_id=cid, value_key="net_income", period_type="FY", period_year=2025,
        numeric_value=Decimal("999"), source_name="old row",
        numeric_value_adjusted=Decimal("1234"),
        adjustments_note="Manuell ueberschrieben",
        adjustments_source="Manual",
    )
    db.add(row)
    db.commit()

    apply_to_db(db, cid, "net_income", 2025, _result("net_income", Decimal("50000")),
                currency="EUR")
    db.commit()
    db.refresh(row)

    assert row.numeric_value == Decimal("50000")
    assert row.primary_method.startswith("two_stage")
    assert row.numeric_value_adjusted == Decimal("1234")
    assert row.adjustments_note == "Manuell ueberschrieben"
    assert row.adjustments_source == "Manual"


def test_apply_to_db_preserves_enrichment_adjusted_fields(client, db):
    """8-K-Enrichment-Adjusted (SEC-URL) ist wie Manual geschuetzt —
    Two-Stage ueberschreibt nur den GAAP-Teil."""
    cid = _company(client, db, email="ts17@example.com")
    url = "https://www.sec.gov/Archives/edgar/data/1/000000000100000001/ex991.htm"
    row = CompanyValue(
        company_id=cid, value_key="net_income", period_type="FY", period_year=2025,
        numeric_value=Decimal("999"), source_name="old row",
        numeric_value_adjusted=Decimal("1500"),
        adjustments_note="Non-GAAP (Reconciliation 8-K): SBC",
        adjustments_source=url,
    )
    db.add(row)
    db.commit()

    apply_to_db(db, cid, "net_income", 2025, _result("net_income", Decimal("50000")),
                currency="EUR")
    db.commit()
    db.refresh(row)

    assert row.numeric_value == Decimal("50000")
    assert row.numeric_value_adjusted == Decimal("1500")
    assert row.adjustments_note == "Non-GAAP (Reconciliation 8-K): SBC"
    assert row.adjustments_source == url


def test_apply_to_db_never_touches_ir_pdf_row(client, db):
    """Punkt 3: from_ir_pdf-Zeilen sind authoritative (Invariante wie
    provider_anchor/routes)."""
    cid = _company(client, db, email="ts13@example.com")
    pdf = CompanyValue(
        company_id=cid, value_key="revenue", period_type="FY", period_year=2025,
        numeric_value=Decimal("888"), from_ir_pdf=True,
        primary_method="pdf", source_name="pdf row",
    )
    db.add(pdf)
    db.commit()

    apply_to_db(db, cid, "revenue", 2025, _result("revenue", Decimal("50000")), currency="EUR")
    db.commit()
    db.refresh(pdf)

    assert pdf.numeric_value == Decimal("888")
    assert pdf.from_ir_pdf is True
    assert pdf.primary_method == "pdf"
    assert pdf.last_refresh_attempt is not None


def test_apply_to_db_partial_null_periods_get_placeholders_and_stamps(client, db):
    """Punkt 4: null-Perioden werden PRO PERIODE behandelt, nicht nur wenn
    ALLE Perioden null sind. Bestehende Zeile einer null-Periode wird
    gestempelt, fehlende bekommen not_found-Platzhalter — der FY-Wert wird
    normal geschrieben."""
    cid = _company(client, db, email="ts14@example.com")
    q1_old = CompanyValue(
        company_id=cid, value_key="revenue", period_type="Q1", period_year=2025,
        numeric_value=Decimal("10"), source_name="old q1 row",
    )
    db.add(q1_old)
    db.commit()

    # FY vorhanden, Q1..Q4 null.
    apply_to_db(db, cid, "revenue", 2025, _result("revenue", Decimal("50000")), currency="EUR")
    db.commit()

    assert _fy_row(db, cid, "revenue").numeric_value == Decimal("50000")
    db.refresh(q1_old)
    assert q1_old.numeric_value == Decimal("10")
    assert q1_old.last_refresh_attempt is not None
    for pt in ("Q2", "Q3", "Q4"):
        ph = (
            db.query(CompanyValue)
            .filter(
                CompanyValue.company_id == cid,
                CompanyValue.value_key == "revenue",
                CompanyValue.period_type == pt,
                CompanyValue.period_year == 2025,
            )
            .one()
        )
        assert ph.numeric_value is None
        assert ph.primary_method == "not_found"


def test_apply_to_db_correction_gate_is_per_period(client, db):
    """Punkt 5: eine Verifier-Korrektur einer ANDEREN Periode darf eine
    quote-lose Periode nicht durchrutschen lassen — das Gate zaehlt nur fuer
    Perioden, die in verdict.corrections wirklich korrigiert wurden."""
    from scripts.two_stage_research import QuarterValue

    cid = _company(client, db, email="ts15@example.com")
    r = _result("revenue", Decimal("50000"))
    # FY: vom Verifier korrigiert (mit Reason), aber ohne source_quote.
    r.extract.fy.source_quote = None
    # Q1: Wert ohne Quote, NICHT in corrections -> muss geskippt werden.
    r.extract.q1 = QuarterValue(value=Decimal("100"), source_quote=None,
                                source_url=None, is_estimate=False)
    r.verdict.verdict = "correct"
    r.verdict.reason = "per-share confusion fixed"
    r.verdict.corrections = {"FY": Decimal("51000")}

    apply_to_db(db, cid, "revenue", 2025, r, currency="EUR")
    db.commit()

    fy = _fy_row(db, cid, "revenue")
    assert fy.numeric_value == Decimal("51000")  # Korrektur der Periode selbst
    q1 = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == cid,
            CompanyValue.value_key == "revenue",
            CompanyValue.period_type == "Q1",
            CompanyValue.period_year == 2025,
        )
        .all()
    )
    # Q1 hat weder Quote noch eigene Korrektur -> kein Write (keine Zeile).
    assert q1 == []


# --- EDGAR-only-Gate: berichtete GAAP-Zellen von US-Filern kommen
# ausschliesslich aus EDGAR-XBRL, kein LLM-Fallback. -------------------------


def _us_company(client, db, email):
    """US-Filer: ISIN mit US-Praefix (is_us_company entscheidet am ISIN)."""
    cid = _company(client, db, email=email)
    from app.companies.models import Company
    comp = db.get(Company, cid)
    comp.isin = "US0378331005"
    db.commit()
    return cid


def _row(db, cid, key, ptype, year):
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == cid,
            CompanyValue.value_key == key,
            CompanyValue.period_type == ptype,
            CompanyValue.period_year == year,
        )
        .one()
    )


def test_edgar_only_suppresses_reported_us_gaap_write(client, db):
    """US-Filer + berichtete Periode + EDGAR-Key: der Two-Stage-GAAP-Wert
    wird unterdrueckt — statt Wert entsteht ein not_found-Platzhalter."""
    cid = _us_company(client, db, email="ts18@example.com")
    r = _result("net_income", Decimal("50000"))
    r.extract.year = 2024
    apply_to_db(db, cid, "net_income", 2024, r, currency="USD")
    db.commit()

    row = _row(db, cid, "net_income", "FY", 2024)
    assert row.numeric_value is None
    assert row.primary_method == "not_found"
    assert row.last_refresh_attempt is not None


def test_edgar_only_resets_stale_llm_row_to_not_found(client, db):
    """Bestehende (nicht-authoritative) LLM-Zeile einer berichteten Periode
    wird auf not_found zurueckgesetzt — die Zelle wird leer/rot statt einen
    alten Two-Stage-Wert zu behalten oder einen neuen zu bekommen."""
    cid = _us_company(client, db, email="ts19@example.com")
    stale = CompanyValue(
        company_id=cid, value_key="net_income", period_type="FY", period_year=2024,
        numeric_value=Decimal("999"), primary_method="two_stage_confirmed",
        source_name="old llm row",
    )
    db.add(stale)
    db.commit()

    r = _result("net_income", Decimal("50000"))
    r.extract.year = 2024
    apply_to_db(db, cid, "net_income", 2024, r, currency="USD")
    db.commit()
    db.refresh(stale)

    assert stale.numeric_value is None
    assert stale.primary_method == "not_found"
    assert stale.last_refresh_attempt is not None


def test_edgar_only_keeps_unreported_fy_writable(client, db):
    """Unberichtete Periode (laufendes Geschaeftsjahr) bleibt voll
    beschreibbar — Schaetzungen sind ausdruecklich erwuenscht."""
    from datetime import date

    cid = _us_company(client, db, email="ts20@example.com")
    running_year = date.today().year
    r = _result("net_income", Decimal("50000"))
    r.extract.year = running_year
    apply_to_db(db, cid, "net_income", running_year, r, currency="USD")
    db.commit()

    row = _row(db, cid, "net_income", "FY", running_year)
    assert row.numeric_value == Decimal("50000")
    assert row.primary_method.startswith("two_stage")
    assert row.is_forecast is True  # FY-Ende in der Zukunft -> Forecast


def test_edgar_only_estimate_still_writes_forecast_row(client, db):
    """Forecast-Slot (is_estimate=True) bleibt auch fuer berichtete Perioden
    beschreibbar — das Gate betrifft nur den Actual-Slot."""
    cid = _us_company(client, db, email="ts21@example.com")
    r = _result("net_income", Decimal("50000"))
    r.extract.year = 2024
    r.extract.fy.is_estimate = True
    apply_to_db(db, cid, "net_income", 2024, r, currency="USD")
    db.commit()

    row = _row(db, cid, "net_income", "FY", 2024)
    assert row.numeric_value == Decimal("50000")
    assert row.is_forecast is True


def test_edgar_only_non_us_company_unchanged(client, db):
    """Nicht-US-Firma (keine US-ISIN): berichtete Periode bleibt per
    Two-Stage beschreibbar wie bisher."""
    cid = _company(client, db, email="ts22@example.com")
    r = _result("net_income", Decimal("50000"))
    r.extract.year = 2024
    apply_to_db(db, cid, "net_income", 2024, r, currency="EUR")
    db.commit()

    row = _row(db, cid, "net_income", "FY", 2024)
    assert row.numeric_value == Decimal("50000")
    assert row.primary_method.startswith("two_stage")


def test_edgar_only_existing_provider_value_untouched(client, db):
    """Bestehender Provider-Wert (XBRL-Anker) bleibt unangetastet — nur der
    Refresh-Versuch wird gestempelt, kein Reset auf not_found."""
    cid = _us_company(client, db, email="ts23@example.com")
    prov = CompanyValue(
        company_id=cid, value_key="net_income", period_type="FY", period_year=2024,
        numeric_value=Decimal("777"), primary_method="provider",
        source_name="SEC EDGAR 10-K (FY2024)", currency="USD",
    )
    db.add(prov)
    db.commit()

    r = _result("net_income", Decimal("50000"))
    r.extract.year = 2024
    apply_to_db(db, cid, "net_income", 2024, r, currency="USD")
    db.commit()
    db.refresh(prov)

    assert prov.numeric_value == Decimal("777")
    assert prov.primary_method == "provider"
    assert prov.last_refresh_attempt is not None
