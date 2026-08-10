"""Cross-Metrik-Konsistenz: deterministische Checks nach jedem Refresh.

Prompts koennen Konsistenz nur fordern — hier wird sie geprueft (Flags)
bzw. hergestellt (net_debt aus Komponenten). Flags werden bei jedem Lauf
neu gesetzt und geloescht, wenn der Check wieder besteht.
"""

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.values.models import CompanyValue
from app.values.period_keys import SUMMABLE_QUARTERLY_KEYS

logger = logging.getLogger(__name__)

_Q_TYPES = ("Q1", "Q2", "Q3", "Q4")

# Toleranzen bewusst weit genug fuer Schaetz-Unschaerfe, eng genug fuer
# die beobachteten Fehlerklassen (Faktor 2-4 Abweichungen).
_QSUM_TOL = Decimal("0.05")
_FCF_TOL = Decimal("0.10")
_EPS_NI_TOL = Decimal("0.20")
_EST_JUMP_TOL = Decimal("0.5")
_EST_MIN_BASE = Decimal("1000000")

_NET_DEBT_COMPONENTS = ("st_debt", "lt_debt", "cash_and_equivalents", "st_investments")

# Point-in-Time-Bilanz-Keys der Carry-Forward-Ableitung: der letzte
# berichtete Quartalsstichtag ersetzt die Two-Stage-Forecast-Recherche
# fuer Q4/FY des laufenden FY vollstaendig (siehe
# derive_balance_carry_forward + Key-Loop-Skip in routes).
BALANCE_CARRY_FORWARD_KEYS = frozenset(
    {"cash_and_equivalents", "st_investments", "st_debt", "lt_debt"}
)

# Keys, die per LLM-Recherche befuellt werden — Ziel der Vorjahres-Kopie-
# und Schaetzungs-Plausibilitaets-Checks. eps_diluted explizit dabei.
_RESEARCH_KEYS = tuple(sorted(set(SUMMABLE_QUARTERLY_KEYS) | {"eps_diluted"}))

# Nicht-LLM-Herkunft: bewusste Werte, kein prior_year_copy-Verdacht.
_NON_LLM_METHODS = ("provider", "manual", "calculated")


def _rows_for_year(db: Session, company_id: UUID, year: int) -> list[CompanyValue]:
    # Deterministische Ordnung: der Unique-Index erlaubt pro Zelle ZWEI
    # Zeilen (Actual + Forecast). Ohne order_by haengt es von der Query-
    # Reihenfolge ab, welche Zeile _row_of/_value_of als "erste passende"
    # sehen — mit is_forecast asc gewinnt immer die Actual-Zeile.
    return (
        db.query(CompanyValue)
        .filter(CompanyValue.company_id == company_id, CompanyValue.period_year == year)
        .order_by(CompanyValue.is_forecast.asc())
        .all()
    )


def _value_of(rows: list[CompanyValue], key: str, period_type: str) -> Decimal | None:
    # Erste passende Zeile = Actual bevorzugt (rows sind is_forecast-asc
    # sortiert, siehe _rows_for_year).
    for r in rows:
        if r.value_key == key and r.period_type == period_type:
            return r.numeric_value
    return None


def _row_of(rows: list[CompanyValue], key: str, period_type: str) -> CompanyValue | None:
    # Erste passende Zeile = Actual bevorzugt (rows sind is_forecast-asc
    # sortiert, siehe _rows_for_year).
    for r in rows:
        if r.value_key == key and r.period_type == period_type:
            return r
    return None


def _set_flag(row: CompanyValue | None, flag: str, active: bool) -> None:
    if row is None:
        return
    current = set(f for f in (row.consistency_flags or "").split(",") if f)
    if active:
        current.add(flag)
    else:
        current.discard(flag)
    row.consistency_flags = ",".join(sorted(current)) or None


def _reload_slot(
    db: Session, company_id: UUID, key: str, pt: str, year: int, is_forecast: bool,
) -> CompanyValue | None:
    """Slot-Zeile nach IntegrityError-Race frisch laden."""
    return (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == pt,
            CompanyValue.period_year == year,
            CompanyValue.is_forecast == is_forecast,
        )
        .first()
    )


def _derivation_replaceable(row: CompanyValue) -> bool:
    """Lock-Konvention der deterministischen Ableitungen: ersetzbar sind
    leere Zeilen, not_found-Platzhalter, alte LLM-Werte (two_stage_*/web_*)
    und fruehere Ableitungen (calculated). Manuelle, PDF- und Provider-
    Zeilen mit Wert sind authoritative (Guards wie provider_anchor/
    gaap_bridge)."""
    if row.manually_overridden or (row.from_ir_pdf and row.numeric_value is not None):
        return False
    if row.numeric_value is None:
        return True
    pm = row.primary_method or ""
    return (
        pm in ("not_found", "calculated")
        or pm.startswith("two_stage")
        or pm.startswith("web")
    )


def _rel_diff(a: Decimal, b: Decimal) -> Decimal:
    denom = max(abs(a), abs(b))
    if denom == 0:
        return Decimal("0")
    return abs(a - b) / denom


def validate_cross_metrics(
    db: Session, company_id: UUID, year: int, is_us: bool = False,
) -> list[str]:
    """Prueft die Kern-Identitaeten und setzt/loescht consistency_flags.

    is_us=True (Validator-Diet): nur qsum_mismatch und fcf_vs_ocf_capex —
    die uebrigen Checks zielen auf LLM-Recherche-Fehlerklassen (Skalierung,
    Vorjahres-Kopie, stale Schaetzung, IFRS-Notes-SBC), die es im
    EDGAR/Bruecke/Guidance-Pfad nicht mehr gibt. DE unveraendert.

    Gibt die Liste aktiver Flags zurueck (fuer Logging/Response).
    """
    rows = _rows_for_year(db, company_id, year)
    active: list[str] = []

    # 1. Q-Summe = FY fuer summierbare Keys (faengt Stale/Neu-Mischreihen,
    #    die der per-Run-Enforcement nicht sehen kann).
    for key in SUMMABLE_QUARTERLY_KEYS:
        fy_row = _row_of(rows, key, "FY")
        if fy_row is None or fy_row.numeric_value in (None, 0):
            continue
        q_vals = [_value_of(rows, key, q) for q in _Q_TYPES]
        if any(v is None for v in q_vals):
            continue
        mismatch = _rel_diff(sum(q_vals), fy_row.numeric_value) > _QSUM_TOL
        _set_flag(fy_row, "qsum_mismatch", mismatch)
        if mismatch:
            active.append(f"qsum_mismatch:{key}")

    # 2. fcf = ocf - capex, pro Periode wo alle drei vorhanden.
    for pt in ("FY",) + _Q_TYPES:
        fcf_row = _row_of(rows, "fcf", pt)
        ocf = _value_of(rows, "operating_cash_flow", pt)
        capex = _value_of(rows, "capex", pt)
        if fcf_row is None or fcf_row.numeric_value is None or ocf is None or capex is None:
            continue
        derived = ocf - abs(capex)
        mismatch = _rel_diff(fcf_row.numeric_value, derived) > _FCF_TOL
        _set_flag(fcf_row, "fcf_vs_ocf_capex", mismatch)
        if mismatch:
            active.append(f"fcf_vs_ocf_capex:{pt}")

    # Validator-Diet fuer US: Checks 3-7 ueberspringen und deren evtl.
    # noch gesetzte Alt-Flags (aus der Two-Stage-Aera) raeumen, damit
    # keine stale Flags im UI stehen bleiben.
    if is_us:
        for row in rows:
            for flag in (
                "eps_ni_mismatch", "sbc_implausibly_low", "unit_scale_suspect",
                "prior_year_copy", "estimate_jump", "estimate_below_prior",
            ):
                _set_flag(row, flag, False)
        if active:
            logger.warning("consistency: %s/%s flags: %s", company_id, year, ",".join(active))
        return active

    # 3. eps x shares ~= net_income (FY). Weite Toleranz: weighted-avg
    #    diluted Shares vs Snapshot-Shares driften durch Buybacks.
    ni_row = _row_of(rows, "net_income", "FY")
    eps = _value_of(rows, "eps_diluted", "FY")
    shares = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == "shares_outstanding",
            CompanyValue.period_type == "SNAPSHOT",
        )
        .one_or_none()
    )
    if ni_row is not None and ni_row.numeric_value and eps and shares and shares.numeric_value:
        implied = eps * shares.numeric_value
        mismatch = _rel_diff(implied, ni_row.numeric_value) > _EPS_NI_TOL
        _set_flag(ni_row, "eps_ni_mismatch", mismatch)
        if mismatch:
            active.append("eps_ni_mismatch")

    # 4. SBC-Teilkomponenten-Detektor: non-zero aber winzig relativ zum
    #    Umsatz = fast sicher nur EIN Plan aus der IFRS-2-Note uebernommen
    #    (adidas: 4.1M LTIP statt 83.6M Gesamtsumme). Explizite 0 (Familien-
    #    Konzerne ohne SBC) ist legitim und wird nicht geflaggt.
    sbc_row = _row_of(rows, "sbc", "FY")
    rev = _value_of(rows, "revenue", "FY")
    if (
        sbc_row is not None and sbc_row.numeric_value is not None
        and sbc_row.numeric_value > 0
        and rev is not None and rev > Decimal("5000000000")
    ):
        mismatch = sbc_row.numeric_value / rev < Decimal("0.0005")
        _set_flag(sbc_row, "sbc_implausibly_low", mismatch)
        if mismatch:
            active.append("sbc_implausibly_low")

    # 5. Einheiten-Detektor: absolute-EUR-Keys unter 1 Mio (aber != 0) sind
    #    fast immer eine fehlende Mio-Skalierung des Extractors
    #    (BAYN capex '2510' = 2.51 Mrd, CBK cash '31525' = 31.5 Mrd).
    _ABS_EUR_KEYS = (
        "revenue", "net_income", "ebitda", "fcf", "operating_cash_flow", "capex",
        "sbc", "buyback_volume", "dividends", "cash_and_equivalents",
        "st_investments", "st_debt", "lt_debt", "net_debt",
    )
    for key in _ABS_EUR_KEYS:
        for pt in ("FY",) + _Q_TYPES:
            row = _row_of(rows, key, pt)
            if row is None or row.numeric_value is None:
                continue
            suspect = row.numeric_value != 0 and abs(row.numeric_value) < Decimal("1000000")
            _set_flag(row, "unit_scale_suspect", suspect)
            if suspect:
                active.append(f"unit_scale_suspect:{key}:{pt}")

    # 6. Vorjahres-Kopie-Detektor: FY-Wert des Jahres N EXAKT gleich FY N-1
    #    ist bei LLM-Zeilen (two_stage/web_guidance) fast immer ein Kopier-
    #    fehler der Recherche. Manual/Provider/Calculated-Zeilen sind
    #    bewusste Werte und bleiben flag-frei. N-1 wird selbst nachgeladen,
    #    weil validate_cross_metrics nur den Jahres-Snapshot N sieht.
    prev_rows = _rows_for_year(db, company_id, year - 1)
    for key in _RESEARCH_KEYS:
        # dividends/buybacks koennen legitim exakt gleich bleiben (stabile
        # Ausschuettungspolitik, fixe Jahrestranchen) — vom Kopie-Check
        # ausgenommen, sonst Dauerrauschen.
        if key in ("dividends", "buyback_volume"):
            continue
        prev_val = _value_of(prev_rows, key, "FY")
        if prev_val is None:
            continue
        for row in rows:
            if row.value_key != key or row.period_type != "FY":
                continue
            if row.numeric_value is None:
                continue
            is_llm = (
                not row.manually_overridden
                and not row.from_ir_pdf
                and (row.primary_method or "") not in _NON_LLM_METHODS
            )
            copied = is_llm and row.numeric_value != 0 and row.numeric_value == prev_val
            _set_flag(row, "prior_year_copy", copied)
            if copied:
                active.append(f"prior_year_copy:{key}")

    # 7. Schaetzungs-Plausibilitaet: Forecast-FY vs Actual-FY des Vorjahres.
    #    >50% Abweichung ist fast immer eine grob falsche/veraltete
    #    Schaetzung — ausser bei Vorzeichenwechsel (bei Cashflows legitim).
    #    Umsatz-Schaetzung DEUTLICH unter dem letzten Ist (>3%) ist meist
    #    veraltet gegenueber neuer Guidance. Manuelle Zeilen ausgenommen.
    #    Bekannte False-Positives (advisory, manuell zu quittieren):
    #    Spin-off-Jahre — der konventionskonforme restatete Forecast liegt
    #    zwangslaeufig unter dem Alt-Struktur-Actual des Vorjahres.
    #    Per-Share-Keys (eps) liegen unter _EST_MIN_BASE und sind bewusst
    #    ausgenommen — deren Konsistenz sichert der eps_ni-Check.
    for key in _RESEARCH_KEYS:
        prev_actual = None
        for r in prev_rows:
            if r.value_key == key and r.period_type == "FY" and not r.is_forecast:
                prev_actual = r
                break
        if (
            prev_actual is None or prev_actual.numeric_value is None
            or abs(prev_actual.numeric_value) <= _EST_MIN_BASE
        ):
            continue
        for row in rows:
            if (
                row.value_key != key or row.period_type != "FY"
                or not row.is_forecast or row.numeric_value is None
            ):
                continue
            manual = bool(row.manually_overridden)
            sign_flip = (
                (row.numeric_value > 0 and prev_actual.numeric_value < 0)
                or (row.numeric_value < 0 and prev_actual.numeric_value > 0)
            )
            jump = (
                not manual and not sign_flip
                and abs(row.numeric_value / prev_actual.numeric_value - 1) > _EST_JUMP_TOL
            )
            _set_flag(row, "estimate_jump", jump)
            if jump:
                active.append(f"estimate_jump:{key}")
            if key == "revenue":
                below = (
                    not manual
                    and row.numeric_value
                    < prev_actual.numeric_value * Decimal("0.97")
                )
                _set_flag(row, "estimate_below_prior", below)
                if below:
                    active.append("estimate_below_prior:revenue")

    if active:
        logger.warning("consistency: %s/%s flags: %s", company_id, year, ",".join(active))
    return active


def derive_net_debt_from_components(db: Session, company_id: UUID, year: int) -> int:
    """net_debt = st_debt + lt_debt - cash - st_investments, pro Periode.

    Erzwingt EINE net_debt-Definition (klassisch) ueber alle Jahre — Company-
    eigene Varianten ('adjusted net borrowings' inkl. Leases) erzeugten
    Phantom-Verschuldungsspruenge im YoY-Vergleich. Manuelle und PDF-Zeilen
    werden nicht angefasst. Gibt die Anzahl geschriebener Perioden zurueck.
    """
    rows = _rows_for_year(db, company_id, year)
    written = 0
    for pt in ("FY",) + _Q_TYPES:
        comps = {k: _row_of(rows, k, pt) for k in _NET_DEBT_COMPONENTS}
        if any(c is None or c.numeric_value is None for c in comps.values()):
            continue
        derived = (
            comps["st_debt"].numeric_value
            + comps["lt_debt"].numeric_value
            - comps["cash_and_equivalents"].numeric_value
            - comps["st_investments"].numeric_value
        )
        target = _row_of(rows, "net_debt", pt)
        if target is not None and (target.manually_overridden or target.from_ir_pdf):
            continue
        is_forecast = any(bool(c.is_forecast) for c in comps.values())
        source = (
            f"Derived (classic): st_debt {comps['st_debt'].numeric_value} + "
            f"lt_debt {comps['lt_debt'].numeric_value} - "
            f"cash {comps['cash_and_equivalents'].numeric_value} - "
            f"st_investments {comps['st_investments'].numeric_value} = {derived}"
        )
        now = datetime.now(timezone.utc)
        if target is None:
            target = CompanyValue(
                id=uuid4(), company_id=company_id, value_key="net_debt",
                period_type=pt, period_year=year, is_forecast=is_forecast,
            )
            # SAVEPOINT pro Insert: Unique-Index-Kollision (Race mit
            # parallelem Writer) -> Zeile neu laden, nur updaten wenn sie
            # noch keinen Wert hat, sonst skip.
            try:
                with db.begin_nested():
                    db.add(target)
                    db.flush()
            except IntegrityError:
                target = _reload_slot(db, company_id, "net_debt", pt, year, is_forecast)
                if target is None or target.numeric_value is not None:
                    continue
        prev = target.numeric_value
        target.numeric_value = derived
        target.source_name = source[:4096]
        target.source_link = None
        target.primary_method = "calculated"
        target.is_forecast = is_forecast
        target.currency = comps["st_debt"].currency or target.currency
        target.fetched_at = now
        target.last_refresh_attempt = now
        if prev is not None and prev != derived:
            logger.info(
                "net_debt %s/%s %s: %s -> %s (component derivation)",
                company_id, year, pt, prev, derived,
            )
        written += 1
    db.flush()
    return written


def derive_missing_ocf(db: Session, company_id: UUID, year: int) -> int:
    """Fehlende operating_cash_flow-Zeilen aus der Identitaet ocf = fcf + capex.

    NUR fuer fehlende Zellen (nie ueberschreiben) — wenn der Extractor keine
    zitierfaehige OCF-Quelle findet, ist die deterministische Ableitung aus
    den vorhandenen fcf/capex-Zeilen besser als eine leere Zelle.
    """
    rows = _rows_for_year(db, company_id, year)
    written = 0
    for pt in ("FY",) + _Q_TYPES:
        existing = _row_of(rows, "operating_cash_flow", pt)
        if existing is not None and existing.numeric_value is not None:
            continue
        fcf_row = _row_of(rows, "fcf", pt)
        capex_row = _row_of(rows, "capex", pt)
        if (fcf_row is None or fcf_row.numeric_value is None
                or capex_row is None or capex_row.numeric_value is None):
            continue
        derived = fcf_row.numeric_value + abs(capex_row.numeric_value)
        now = datetime.now(timezone.utc)
        is_forecast = bool(fcf_row.is_forecast or capex_row.is_forecast)
        target = existing
        if target is None:
            target = CompanyValue(
                id=uuid4(), company_id=company_id, value_key="operating_cash_flow",
                period_type=pt, period_year=year, is_forecast=is_forecast,
            )
            # SAVEPOINT pro Insert: bei Race-Kollision Zeile neu laden,
            # nur updaten wenn sie noch keinen Wert hat, sonst skip.
            try:
                with db.begin_nested():
                    db.add(target)
                    db.flush()
            except IntegrityError:
                target = _reload_slot(
                    db, company_id, "operating_cash_flow", pt, year, is_forecast
                )
                if target is None or target.numeric_value is not None:
                    continue
        target.numeric_value = derived
        target.source_name = (
            f"Derived (identity): fcf {fcf_row.numeric_value} + capex "
            f"{abs(capex_row.numeric_value)} = {derived}"
        )[:4096]
        target.primary_method = "calculated"
        target.is_forecast = is_forecast
        target.currency = fcf_row.currency or target.currency
        target.fetched_at = now
        target.last_refresh_attempt = now
        written += 1
    db.flush()
    return written


def derive_missing_fcf(db: Session, company_id: UUID, years: list[int]) -> int:
    """fcf = operating_cash_flow - |capex| als berechneter Wert, pro Periode.

    Beschlossene Umstellung: fcf wird nicht mehr recherchiert (EDGAR-only-
    Gate), sondern deterministisch aus den beiden Inputs berechnet, sobald
    beide vorhanden sind. not_found-Platzhalter und alte LLM-Werte werden
    ersetzt (_derivation_replaceable); manuelle, PDF- und Provider-Zeilen
    bleiben unangetastet. Adjusted-Spur analog, wenn BEIDE Inputs adjusted-
    Werte haben. Rueckgabe: Anzahl geschriebener Perioden.
    """
    from app.values.persistence import adjusted_is_protected

    written = 0
    for year in years:
        rows = _rows_for_year(db, company_id, year)
        for pt in ("FY",) + _Q_TYPES:
            ocf_row = _row_of(rows, "operating_cash_flow", pt)
            capex_row = _row_of(rows, "capex", pt)
            if (ocf_row is None or ocf_row.numeric_value is None
                    or capex_row is None or capex_row.numeric_value is None):
                continue
            slot_rows = [
                r for r in rows
                if r.value_key == "fcf" and r.period_type == pt
            ]
            if any(not _derivation_replaceable(r) for r in slot_rows):
                continue
            derived = ocf_row.numeric_value - abs(capex_row.numeric_value)
            is_forecast = bool(ocf_row.is_forecast or capex_row.is_forecast)
            # Adjusted nur wenn beide Inputs eine adjusted-Spur haben.
            derived_adj: Decimal | None = None
            if (ocf_row.numeric_value_adjusted is not None
                    and capex_row.numeric_value_adjusted is not None):
                derived_adj = (
                    ocf_row.numeric_value_adjusted
                    - abs(capex_row.numeric_value_adjusted)
                )
            # Zielzeile: Slot mit passendem is_forecast bevorzugt, sonst
            # den einzigen anderen Slot umziehen (kein Kollisionsrisiko,
            # der passende Slot existiert dann nicht).
            target = next(
                (r for r in slot_rows if bool(r.is_forecast) == is_forecast),
                None,
            )
            if target is None:
                target = next(iter(slot_rows), None)
            now = datetime.now(timezone.utc)
            if target is None:
                target = CompanyValue(
                    id=uuid4(), company_id=company_id, value_key="fcf",
                    period_type=pt, period_year=year, is_forecast=is_forecast,
                )
                # SAVEPOINT pro Insert: bei Race-Kollision Zeile neu laden,
                # Guards erneut anwenden.
                try:
                    with db.begin_nested():
                        db.add(target)
                        db.flush()
                except IntegrityError:
                    target = _reload_slot(db, company_id, "fcf", pt, year, is_forecast)
                    if target is None or not _derivation_replaceable(target):
                        continue
            target.numeric_value = derived
            target.source_name = (
                f"Berechnet: OCF - Capex ({ocf_row.numeric_value} - "
                f"{abs(capex_row.numeric_value)} = {derived})"
            )[:4096]
            target.source_link = None
            target.primary_method = "calculated"
            target.is_forecast = is_forecast
            target.currency = ocf_row.currency or target.currency
            target.fetched_at = now
            target.last_refresh_attempt = now
            if derived_adj is not None and not adjusted_is_protected(
                target.adjustments_source
            ):
                target.numeric_value_adjusted = derived_adj
                target.adjustments_note = "Berechnet: OCF - Capex (adjusted)"
                target.adjustments_source = None
            written += 1
        db.flush()
    return written


# Q4-Residuum-Keys abgeschlossener Jahre: additiv, Provider liefert kein
# Q4 (kein 3M-Frame im 10-K). eps_diluted bewusst NICHT dabei — nicht
# additiv (weighted diluted shares), kommt aus 8-K-Bruecke/XBRL.
_Q4_RESIDUAL_KEYS = ("ebitda", "net_income", "revenue")
# Negativer Rest = Dateninkonsistenz bei diesen Keys (revenue per
# Konvention immer positiv, ebitda bei den Zielfirmen). net_income darf
# negativ sein (Verlustquartal).
_Q4_RESIDUAL_NONNEGATIVE = frozenset({"ebitda", "revenue"})


def derive_q4_residual_from_fy(db: Session, company_id: UUID, year: int) -> int:
    """Q4 = FY - Q1 - Q2 - Q3 aus berichteten Actuals, pro Residuum-Key
    (ebitda, net_income, revenue).

    EDGAR kann Q4 fuer Income-Keys strukturell nicht liefern (kein
    3M-Frame im 10-K, providers/edgar sucht nur standalone) — der
    Restwert schliesst die Luecke deterministisch und ersetzt die
    Two-Stage-Recherche fuer abgeschlossene US-Jahre. Lock-Guards wie
    _derivation_replaceable. Rueckgabe: Anzahl geschriebener Q4-Zellen.
    """
    rows = _rows_for_year(db, company_id, year)
    written = 0
    for key in _Q4_RESIDUAL_KEYS:
        def _actual(pt: str) -> CompanyValue | None:
            return next(
                (r for r in rows
                 if r.value_key == key and r.period_type == pt
                 and not r.is_forecast and r.numeric_value is not None),
                None,
            )

        fy_row = _actual("FY")
        if fy_row is None:
            continue
        q_rows = [_actual(q) for q in ("Q1", "Q2", "Q3")]
        if any(r is None for r in q_rows):
            continue
        slot_rows = [
            r for r in rows
            if r.value_key == key and r.period_type == "Q4"
        ]
        if any(not _derivation_replaceable(r) for r in slot_rows):
            continue
        q_sum = sum((r.numeric_value for r in q_rows), Decimal("0"))
        derived = fy_row.numeric_value - q_sum
        if key in _Q4_RESIDUAL_NONNEGATIVE and derived < 0:
            logger.warning(
                "%s-q4 residual implausibel %s/FY%s: fy=%s q1-q3=%s -> %s "
                "negativ — skip",
                key, company_id, year, fy_row.numeric_value, q_sum, derived,
            )
            continue
        target = next((r for r in slot_rows if not r.is_forecast), None)
        now = datetime.now(timezone.utc)
        if target is None:
            target = CompanyValue(
                id=uuid4(), company_id=company_id, value_key=key,
                period_type="Q4", period_year=year, is_forecast=False,
            )
            # SAVEPOINT pro Insert: bei Race-Kollision Zeile neu laden,
            # Guards erneut anwenden.
            try:
                with db.begin_nested():
                    db.add(target)
                    db.flush()
            except IntegrityError:
                target = _reload_slot(db, company_id, key, "Q4", year, False)
                if target is None or not _derivation_replaceable(target):
                    continue
        target.numeric_value = derived
        target.source_name = (
            f"Berechnet: FY minus Q1-Q3 ({fy_row.numeric_value} - {q_sum} = {derived})"
        )[:4096]
        target.source_link = None
        target.primary_method = "calculated"
        target.is_forecast = False
        target.currency = fy_row.currency or target.currency
        target.fetched_at = now
        target.last_refresh_attempt = now
        written += 1
    db.flush()
    return written


def _quarter_reported_us(company, year: int, q: str, subs_cache: dict) -> bool:
    """Berichtet-Kriterium wie im apply_to_db-Gate (nur US-Filer): Quartal
    beendet UND (Karenz abgelaufen ODER Item-2.02-8-K nach Quartalsende).
    Der 8-K-Check (Netz) laeuft nur fuer beendete Quartale innerhalb der
    Karenz; Fetch-Fehler fallen konservativ auf die Karenz-Regel zurueck
    (Quartal gilt dann als offen)."""
    reported = False
    try:
        from app.calculations.lock import is_us_company
        if company is not None and is_us_company(company):
            from app.values.detail_page import (
                REPORTING_GRACE_DAYS,
                quarter_end_date,
            )
            p_end = quarter_end_date(
                year, q,
                getattr(company, "fiscal_year_end_month", None),
                getattr(company, "fiscal_year_end_day", None),
            )
            today = date.today()
            if p_end is not None and p_end < today:
                if (today - p_end).days >= REPORTING_GRACE_DAYS:
                    reported = True
                else:
                    from app.values.gaap_bridge import has_reported_8k
                    reported = has_reported_8k(company.ticker, p_end, subs_cache)
    except Exception as e:
        logger.warning(
            "reported-check failed %s/%s FY%s: %s — Karenz-Regel greift "
            "nicht, Quartal gilt als offen",
            getattr(company, "id", None), q, year, e,
        )
        reported = False
    return reported


def derive_open_quarter_from_fy_estimate(db: Session, company_id: UUID, year: int) -> int:
    """Offenes Rest-Quartal deterministisch aus dem FY-Estimate ableiten:
    Q_offen = FY_est (Guidance/Konsens) - Summe(berichtete Quartale).

    Greift pro summierbarem Key, wenn ein FY-Forecast mit Wert existiert
    und GENAU EIN Quartal noch keinen berichteten Actual hat. Ersetzt die
    LLM-Schaetzung des offenen Quartals durch Arithmetik (kein LLM-Call,
    kein Trend-Drift). Manuelle und PDF-Zeilen bleiben unangetastet.
    US-Filer: ein bereits berichtetes Quartal (Karenz abgelaufen ODER
    Item-2.02-8-K nach Quartalsende) gilt NICHT als offen — sonst
    ueberschreibt die FY-Guidance ein Quartal, dessen Actual gleich per
    8-K-Bruecke/XBRL-Anker kommt. Rueckgabe: Anzahl geschriebener
    Quartals-Zellen.
    """
    from app.companies.models import Company
    from app.values.persistence import adjusted_is_protected
    from app.values.sign_keys import ALWAYS_POSITIVE_KEYS

    rows = _rows_for_year(db, company_id, year)
    written = 0

    company = db.get(Company, company_id)
    reported_memo: dict[str, bool] = {}
    subs_cache: dict = {}

    def _quarter_already_reported(q: str) -> bool:
        # Memoisierter Wrapper um das gemeinsame Berichtet-Kriterium.
        if q not in reported_memo:
            reported_memo[q] = _quarter_reported_us(company, year, q, subs_cache)
        return reported_memo[q]

    for key in sorted(SUMMABLE_QUARTERLY_KEYS):
        # FY-Estimate (Guidance/Konsens): Forecast-Zeile mit Wert. Ein
        # FY-Actual mit Wert heisst Jahr abgeschlossen — nichts abzuleiten.
        fy_actual = next(
            (r for r in rows
             if r.value_key == key and r.period_type == "FY"
             and not r.is_forecast and r.numeric_value is not None),
            None,
        )
        if fy_actual is not None:
            continue
        # FY-Anker: GAAP-Wert ODER adjusted-only (numeric_value=None, aber
        # numeric_value_adjusted vorhanden — z.B. Non-GAAP-Konsens ohne
        # explizite GAAP-Basis aus guidance_estimates).
        fy_est = next(
            (r for r in rows
             if r.value_key == key and r.period_type == "FY"
             and r.is_forecast
             and (r.numeric_value is not None
                  or r.numeric_value_adjusted is not None)),
            None,
        )
        if fy_est is None:
            # Hygiene: verwaiste Residuen raeumen. Ein frueher abgeleitetes
            # Q-Residuum ohne FY-Anker (z.B. nachdem ein kontaminierter
            # GAAP-Konsens geraeumt wurde) ist nicht mehr belegbar. Greift
            # NUR wenn weder GAAP- noch adjusted-Anker existieren.
            for r in rows:
                if (
                    r.value_key == key and r.period_type in _Q_TYPES
                    and r.is_forecast and not r.manually_overridden
                    and r.primary_method == "calculated"
                    and r.numeric_value is not None
                    and (r.source_name or "").startswith("FY-Guidance minus")
                ):
                    r.numeric_value = None
                    r.source_name = (
                        "Geraeumt: FY-Anker der Ableitung entfallen"
                    )
                    logger.info(
                        "open-quarter orphan geraeumt %s/%s/%s FY%s",
                        company_id, key, r.period_type, year,
                    )
            continue
        if fy_est.numeric_value is None:
            # Adjusted-only-Anker: GAAP-Pfad ueberspringen, nur das
            # adjusted-Residuum rechnen. Berichtet heisst hier: Actual-
            # Zeile MIT adjusted-Wert — alle berichteten Quartale muessen
            # adjusted haben, sonst ist das Residuum nicht belegbar.
            reported_adj: dict[str, CompanyValue] = {}
            open_adj_qs: list[str] = []
            for q in _Q_TYPES:
                actual = next(
                    (r for r in rows
                     if r.value_key == key and r.period_type == q
                     and not r.is_forecast
                     and r.numeric_value_adjusted is not None),
                    None,
                )
                if actual is not None:
                    reported_adj[q] = actual
                else:
                    open_adj_qs.append(q)
            if len(open_adj_qs) != 1:
                continue
            target_q = open_adj_qs[0]
            # Berichtetes Quartal ohne DB-Actual: nicht als offen behandeln
            # (gleiches Gate wie im GAAP-Pfad).
            if _quarter_already_reported(target_q):
                continue
            adj_sum = sum(
                (r.numeric_value_adjusted for r in reported_adj.values()),
                Decimal("0"),
            )
            derived_adj = fy_est.numeric_value_adjusted - adj_sum
            if key in ALWAYS_POSITIVE_KEYS and derived_adj < 0:
                logger.warning(
                    "open-quarter derive adjusted-only implausibel %s/%s/FY%s: "
                    "fy_adj=%s reported_adj_sum=%s -> %s negativ — skip",
                    company_id, key, year, fy_est.numeric_value_adjusted,
                    adj_sum, derived_adj,
                )
                continue
            slot_rows = [
                r for r in rows
                if r.value_key == key and r.period_type == target_q
            ]
            if any(
                r.manually_overridden
                or (r.from_ir_pdf and r.numeric_value is not None)
                for r in slot_rows
            ):
                continue
            target = next((r for r in slot_rows if r.is_forecast), None)
            now = datetime.now(timezone.utc)
            if target is None:
                target = CompanyValue(
                    id=uuid4(), company_id=company_id, value_key=key,
                    period_type=target_q, period_year=year, is_forecast=True,
                )
                # SAVEPOINT pro Insert: bei Race-Kollision Zeile neu laden,
                # Guards erneut anwenden.
                try:
                    with db.begin_nested():
                        db.add(target)
                        db.flush()
                except IntegrityError:
                    target = _reload_slot(db, company_id, key, target_q, year, True)
                    if target is None or target.manually_overridden:
                        continue
            if adjusted_is_protected(target.adjustments_source):
                continue
            # GAAP-Spur bleibt None/unveraendert — es gibt keinen GAAP-Anker.
            was_empty = (
                target.numeric_value is None
                and target.numeric_value_adjusted is None
            )
            target.numeric_value_adjusted = derived_adj
            target.adjustments_note = (
                "Abgeleitet: FY-Schaetzung minus berichtete Quartale"
            )
            target.adjustments_source = None
            # primary_method nur setzen wenn Zeile neu/leer war — eine
            # bestehende GAAP-Herkunft der Zeile nicht umdeklarieren.
            if not (target.primary_method or "") or was_empty:
                target.primary_method = "calculated"
            target.is_forecast = True
            target.currency = target.currency or fy_est.currency
            target.fetched_at = now
            target.last_refresh_attempt = now
            written += 1
            continue
        reported: dict[str, CompanyValue] = {}
        open_qs: list[str] = []
        for q in _Q_TYPES:
            actual = next(
                (r for r in rows
                 if r.value_key == key and r.period_type == q
                 and not r.is_forecast and r.numeric_value is not None),
                None,
            )
            if actual is not None:
                reported[q] = actual
            else:
                open_qs.append(q)
        if len(open_qs) != 1:
            continue
        target_q = open_qs[0]
        # Berichtetes Quartal ohne DB-Actual (8-K existiert, XBRL/Bruecke
        # noch nicht durch): nicht als offen behandeln — keine Guidance-
        # Ableitung ueber echte, gleich eintreffende Zahlen legen.
        if _quarter_already_reported(target_q):
            continue
        reported_sum = sum(
            (r.numeric_value for r in reported.values()), Decimal("0")
        )
        derived = fy_est.numeric_value - reported_sum
        # Negatives Residuum bei Always-Positive-Keys = stale/inkonsistente
        # FY-Guidance — nicht persistieren.
        if key in ALWAYS_POSITIVE_KEYS and derived < 0:
            logger.warning(
                "open-quarter derive implausibel %s/%s/FY%s: fy_est=%s "
                "reported_sum=%s -> %s negativ — skip",
                company_id, key, year, fy_est.numeric_value,
                reported_sum, derived,
            )
            continue
        # Adjusted-Spur analog: nur wenn der FY-Forecast UND alle
        # berichteten Quartale adjusted-Werte haben. Negatives Residuum
        # bei Always-Positive-Keys wie im GAAP-Pfad nicht persistieren.
        derived_adj: Decimal | None = None
        if fy_est.numeric_value_adjusted is not None:
            adj_vals = [r.numeric_value_adjusted for r in reported.values()]
            if all(v is not None for v in adj_vals):
                adj_sum = sum(adj_vals, Decimal("0"))
                candidate = fy_est.numeric_value_adjusted - adj_sum
                if key in ALWAYS_POSITIVE_KEYS and candidate < 0:
                    logger.warning(
                        "open-quarter derive adjusted implausibel %s/%s/FY%s: "
                        "fy_adj=%s reported_adj_sum=%s -> %s negativ — skip",
                        company_id, key, year, fy_est.numeric_value_adjusted,
                        adj_sum, candidate,
                    )
                else:
                    derived_adj = candidate
        # Zielzeile: Forecast-Slot des offenen Quartals. Manuelle Overrides
        # und PDF-Guidance mit Wert sind authoritative.
        slot_rows = [
            r for r in rows
            if r.value_key == key and r.period_type == target_q
        ]
        if any(
            r.manually_overridden or (r.from_ir_pdf and r.numeric_value is not None)
            for r in slot_rows
        ):
            continue
        target = next((r for r in slot_rows if r.is_forecast), None)
        now = datetime.now(timezone.utc)
        if target is None:
            target = CompanyValue(
                id=uuid4(), company_id=company_id, value_key=key,
                period_type=target_q, period_year=year, is_forecast=True,
            )
            # SAVEPOINT pro Insert: bei Race-Kollision Zeile neu laden,
            # Guards erneut anwenden.
            try:
                with db.begin_nested():
                    db.add(target)
                    db.flush()
            except IntegrityError:
                target = _reload_slot(db, company_id, key, target_q, year, True)
                if target is None or target.manually_overridden:
                    continue
        target.numeric_value = derived
        target.source_name = (
            f"FY-Guidance minus berichtete Quartale: FY {fy_est.numeric_value} "
            f"- Sigma({'+'.join(sorted(reported))}) {reported_sum} = {derived}"
        )[:4096]
        target.source_link = None
        target.primary_method = "calculated"
        target.is_forecast = True
        target.currency = fy_est.currency or target.currency
        target.fetched_at = now
        target.last_refresh_attempt = now
        # Adjusted nur setzen, wenn ableitbar und die vorhandenen Adjusted-
        # Felder nicht authoritative sind (Manual/URL-belegt).
        if derived_adj is not None and not adjusted_is_protected(
            target.adjustments_source
        ):
            target.numeric_value_adjusted = derived_adj
            target.adjustments_note = (
                "Abgeleitet: FY-Schaetzung minus berichtete Quartale"
            )
            target.adjustments_source = None
        written += 1
    db.flush()
    return written


def derive_declared_dividend_quarter(db: Session, company_id: UUID, year: int) -> int:
    """Offenes Dividenden-Quartal aus der deklarierten Rate fortschreiben.

    US-Firmen (Visa) aendern die Quartalsdividende typischerweise nur
    einmal pro FY — ist genau EIN Quartal des laufenden FY offen (gleiches
    Berichtet-Kriterium wie derive_open_quarter: Karenz ODER 8-K) und
    tragen die letzten beiden berichteten Quartale dividends-Werte, wird
    der zuletzt berichtete Quartalswert fortgeschrieben. 0 -> 0 ist
    korrekt (keine Dividende bleibt keine Dividende). Nur leere/ersetzbare
    Slots (_derivation_replaceable); manual/PDF bleiben. Sind damit alle
    4 Quartale voll, wird FY = Summe ueber den bestehenden
    _refresh_fy_from_quarters-Mechanismus nachgezogen (der Refresh-Flow
    ruft ihn hier sonst nicht auf). Rueckgabe: 1 wenn geschrieben.
    """
    from app.companies.models import Company

    rows = _rows_for_year(db, company_id, year)
    # Abgeschlossenes Jahr (FY-Actual mit Wert): das exakte Residuum
    # FY - Sigma(Q) ist der bessere Weg — keine Raten-Fortschreibung.
    fy_actual = next(
        (r for r in rows
         if r.value_key == "dividends" and r.period_type == "FY"
         and not r.is_forecast and r.numeric_value is not None),
        None,
    )
    if fy_actual is not None:
        return 0
    reported: dict[str, CompanyValue] = {}
    open_qs: list[str] = []
    for q in _Q_TYPES:
        actual = next(
            (r for r in rows
             if r.value_key == "dividends" and r.period_type == q
             and not r.is_forecast and r.numeric_value is not None),
            None,
        )
        if actual is not None:
            reported[q] = actual
        else:
            open_qs.append(q)
    if len(open_qs) != 1:
        return 0
    target_q = open_qs[0]
    # Die letzten beiden berichteten Quartale muessen Werte tragen — mit
    # genau einem offenen Quartal sind das die zwei juengsten Actuals.
    ordered = [q for q in _Q_TYPES if q in reported]
    if len(ordered) < 2:
        return 0
    # Berichtetes Quartal ohne DB-Actual (8-K da, XBRL/Bruecke noch nicht
    # durch): nicht fortschreiben — das echte Actual kommt gleich.
    company = db.get(Company, company_id)
    if _quarter_reported_us(company, year, target_q, {}):
        return 0
    src_row = reported[ordered[-1]]
    rate = src_row.numeric_value
    slot_rows = [
        r for r in rows
        if r.value_key == "dividends" and r.period_type == target_q
    ]
    if any(not _derivation_replaceable(r) for r in slot_rows):
        return 0
    target = next((r for r in slot_rows if r.is_forecast), None)
    now = datetime.now(timezone.utc)
    if target is None:
        target = CompanyValue(
            id=uuid4(), company_id=company_id, value_key="dividends",
            period_type=target_q, period_year=year, is_forecast=True,
        )
        # SAVEPOINT pro Insert: bei Race-Kollision Zeile neu laden,
        # Guards erneut anwenden.
        try:
            with db.begin_nested():
                db.add(target)
                db.flush()
        except IntegrityError:
            target = _reload_slot(db, company_id, "dividends", target_q, year, True)
            if target is None or not _derivation_replaceable(target):
                return 0
    target.numeric_value = rate
    target.source_name = (
        f"Fortschreibung deklarierte Quartalsdividende ({ordered[-1]} {rate})"
    )[:4096]
    target.source_link = None
    target.primary_method = "calculated"
    target.is_forecast = True
    target.currency = src_row.currency or target.currency
    target.fetched_at = now
    target.last_refresh_attempt = now
    db.flush()
    # FY = Q1+Q2+Q3+Q4, wenn nun alle 4 Quartale Werte haben — der
    # bestehende Mechanismus prueft die Vollstaendigkeit selbst (kein
    # Doppeln). Import lokal: routes importiert consistency (Zyklus).
    from app.values.routes import _refresh_fy_from_quarters
    _refresh_fy_from_quarters(db, company_id, "dividends", year)
    db.flush()
    return 1


_SPREAD_KEYS = ("eps_diluted", "net_income")
_SPREAD_QUANT = {"eps_diluted": Decimal("0.01"), "net_income": Decimal("1")}
# Herkuenfte, die als exakte berichtete Werte fuer die Spread-Basis zaehlen.
_SPREAD_REPORTED_METHODS = ("provider", "manual", "pdf")


def derive_gaap_from_adjusted_spread(db: Session, company_id: UUID, year: int) -> int:
    """GAAP-EPS/NI-Schaetzung aus der Non-GAAP-Schaetzung ableiten — via
    beobachtetem GAAP/Non-GAAP-Abstand der berichteten Quartale.

    User-Entscheidung: adjusted-only-Forecast-Slots (numeric_value=None,
    numeric_value_adjusted gesetzt) bleiben nicht mehr leer, sondern
    bekommen deterministisch gaap = adjusted - Spread, wobei Spread der
    Durchschnitt von (adjusted - gaap) ueber die berichteten Quartale
    (beide Spuren, provider/manual/PDF) ist — reine Arithmetik, klar
    gekennzeichnet. Ersetzt NICHT die Regel, dass der Guidance-Call
    unklaren Konsens nie in GAAP-Slots schreibt. FY = Summe der
    berichteten GAAP-Quartale + abgeleitete offene Quartale (die
    berichteten Quartale sind exakt — kein FY_adj - 4x Spread).
    Rundung: eps 2 Nachkommastellen, net_income ganzzahlig.
    Plausibilitaet: 0 < gaap < adjusted, sonst skip + Warnung.
    Rueckgabe: Anzahl geschriebener Zellen.
    """
    from decimal import ROUND_HALF_UP

    rows = _rows_for_year(db, company_id, year)
    now = datetime.now(timezone.utc)
    written = 0
    for key in _SPREAD_KEYS:
        quant = _SPREAD_QUANT[key]
        # Berichtete Quartale: exakte Actuals; fuer die Spread-Basis
        # brauchen sie BEIDE Spuren.
        reported_gaap: dict[str, CompanyValue] = {}
        both_tracks: dict[str, CompanyValue] = {}
        for qt in _Q_TYPES:
            actual = next(
                (r for r in rows
                 if r.value_key == key and r.period_type == qt
                 and not r.is_forecast and r.numeric_value is not None
                 and ((r.primary_method or "") in _SPREAD_REPORTED_METHODS
                      or r.manually_overridden or r.from_ir_pdf)),
                None,
            )
            if actual is not None:
                reported_gaap[qt] = actual
                if actual.numeric_value_adjusted is not None:
                    both_tracks[qt] = actual
        if len(both_tracks) < 2:
            continue
        spread = sum(
            (r.numeric_value_adjusted - r.numeric_value
             for r in both_tracks.values()),
            Decimal("0"),
        ) / Decimal(len(both_tracks))
        if spread < 0:
            logger.warning(
                "gaap-spread %s/%s/FY%s: mittlerer Abstand %s negativ "
                "(adjusted < GAAP) — Ableitung liefert wegen des "
                "0<gaap<adjusted-Gates keine Werte",
                company_id, key, year, spread,
            )
        spread_disp = spread.quantize(quant, rounding=ROUND_HALF_UP)
        source_name = (
            "Abgeleitet: Non-GAAP-Schaetzung minus mittlerer "
            f"GAAP/Non-GAAP-Abstand der berichteten Quartale ({spread_disp})"
        )[:4096]

        derived_q: dict[str, Decimal] = {}
        for qt in _Q_TYPES:
            if qt in reported_gaap:
                continue
            # Konservativ: irgendein Actual mit GAAP-Wert (auch two_stage)
            # blockt — hier nicht gegen bestehende Actuals anschreiben.
            if any(
                r.value_key == key and r.period_type == qt
                and not r.is_forecast and r.numeric_value is not None
                for r in rows
            ):
                continue
            fc = next(
                (r for r in rows
                 if r.value_key == key and r.period_type == qt
                 and r.is_forecast and r.numeric_value_adjusted is not None),
                None,
            )
            if fc is None:
                continue
            # Nur adjusted-only-Slots (GAAP leer) bzw. die eigene fruehere
            # Ableitung (calculated, Idempotenz) — einen echten GAAP-
            # Konsens (web_guidance mit Wert) nicht ueberschreiben.
            if fc.numeric_value is not None and fc.primary_method != "calculated":
                continue
            slot_rows = [
                r for r in rows
                if r.value_key == key and r.period_type == qt
            ]
            if any(not _derivation_replaceable(r) for r in slot_rows):
                continue
            gaap_q = (fc.numeric_value_adjusted - spread).quantize(
                quant, rounding=ROUND_HALF_UP
            )
            if not (0 < gaap_q < fc.numeric_value_adjusted):
                logger.warning(
                    "gaap-spread %s/%s/%s FY%s: gaap %s ausserhalb "
                    "(0, adjusted %s) — skip",
                    company_id, key, qt, year, gaap_q,
                    fc.numeric_value_adjusted,
                )
                continue
            fc.numeric_value = gaap_q
            fc.source_name = source_name
            fc.source_link = None
            fc.primary_method = "calculated"
            fc.is_forecast = True
            fc.fetched_at = now
            fc.last_refresh_attempt = now
            derived_q[qt] = gaap_q
            written += 1

        # FY nur wenn alle 4 Quartale GAAP tragen (exakt berichtete +
        # abgeleitete) — sonst waere die Summe nicht belegbar.
        if not derived_q or len(reported_gaap) + len(derived_q) != 4:
            continue
        fy_fc = next(
            (r for r in rows
             if r.value_key == key and r.period_type == "FY"
             and r.is_forecast and r.numeric_value_adjusted is not None),
            None,
        )
        if fy_fc is None:
            continue
        if fy_fc.numeric_value is not None and fy_fc.primary_method != "calculated":
            continue
        fy_slot_rows = [
            r for r in rows
            if r.value_key == key and r.period_type == "FY"
        ]
        if any(not _derivation_replaceable(r) for r in fy_slot_rows):
            continue
        gaap_fy = sum(
            (r.numeric_value for r in reported_gaap.values()),
            Decimal("0"),
        ) + sum(derived_q.values(), Decimal("0"))
        gaap_fy = gaap_fy.quantize(quant, rounding=ROUND_HALF_UP)
        if not (0 < gaap_fy < fy_fc.numeric_value_adjusted):
            logger.warning(
                "gaap-spread %s/%s/FY FY%s: gaap %s ausserhalb "
                "(0, adjusted %s) — skip",
                company_id, key, year, gaap_fy, fy_fc.numeric_value_adjusted,
            )
            continue
        fy_fc.numeric_value = gaap_fy
        fy_fc.source_name = source_name
        fy_fc.source_link = None
        fy_fc.primary_method = "calculated"
        fy_fc.is_forecast = True
        fy_fc.fetched_at = now
        fy_fc.last_refresh_attempt = now
        written += 1
    db.flush()
    return written


# capex/operating_cash_flow ergaenzt (User-Freigabe): ohne sie bleibt
# fcf = OCF - Capex fuer Q4/FY dauerhaft leer, da Analysten diese Keys
# selten quotieren. OCF ist volatiler als die anderen Runrate-Keys —
# der Konsens-Pfad (derive_open_quarter) behaelt Vorrang.
_RUNRATE_KEYS = ("sbc", "buyback_volume", "capex", "operating_cash_flow", "ebitda")


def derive_runrate_quarter(db: Session, company_id: UUID, year: int) -> int:
    """SBC/Buybacks: offenes Quartal als Runrate fortschreiben (Fallback).

    Freigegebene Konvention: existiert KEIN FY-Konsens/Guidance-Forecast
    mit Wert (der einen Q4-Rest via derive_open_quarter_from_fy_estimate
    ermoeglichen wuerde — dieser Pfad hat Vorrang) und ist genau EIN
    Quartal des laufenden FY offen (Berichtet-Kriterium wie
    derive_open_quarter), wird Q_offen = Durchschnitt der berichteten
    Quartale gesetzt und FY = Summe der 4 Quartale nachgezogen
    (_refresh_fy_from_quarters, gleicher Mechanismus wie bei der
    Dividenden-Fortschreibung). Nur leere/ersetzbare Slots
    (_derivation_replaceable); manual/PDF bleiben. Rueckgabe: Anzahl
    geschriebener Q-Zellen.
    """
    from app.companies.models import Company

    rows = _rows_for_year(db, company_id, year)
    company = db.get(Company, company_id)
    subs_cache: dict = {}
    written = 0
    for key in _RUNRATE_KEYS:
        # Abgeschlossenes Jahr (FY-Actual mit Wert): kein Runrate-Fall.
        fy_actual = next(
            (r for r in rows
             if r.value_key == key and r.period_type == "FY"
             and not r.is_forecast and r.numeric_value is not None),
            None,
        )
        if fy_actual is not None:
            continue
        # Konsens-Pfad hat Vorrang: FY-Forecast mit Wert -> die normale
        # Q4-Rest-Ableitung liefert das offene Quartal, Runrate greift nicht.
        fy_est = next(
            (r for r in rows
             if r.value_key == key and r.period_type == "FY"
             and r.is_forecast and r.numeric_value is not None),
            None,
        )
        if fy_est is not None:
            continue
        reported: dict[str, CompanyValue] = {}
        open_qs: list[str] = []
        for q in _Q_TYPES:
            actual = next(
                (r for r in rows
                 if r.value_key == key and r.period_type == q
                 and not r.is_forecast and r.numeric_value is not None),
                None,
            )
            if actual is not None:
                reported[q] = actual
            else:
                open_qs.append(q)
        if len(open_qs) != 1:
            continue
        target_q = open_qs[0]
        # Berichtetes Quartal ohne DB-Actual (8-K da, XBRL/Bruecke noch
        # nicht durch): nicht fortschreiben — das echte Actual kommt gleich.
        if _quarter_reported_us(company, year, target_q, subs_cache):
            continue
        runrate = sum(
            (r.numeric_value for r in reported.values()), Decimal("0")
        ) / Decimal(len(reported))
        slot_rows = [
            r for r in rows
            if r.value_key == key and r.period_type == target_q
        ]
        if any(not _derivation_replaceable(r) for r in slot_rows):
            continue
        target = next((r for r in slot_rows if r.is_forecast), None)
        now = datetime.now(timezone.utc)
        if target is None:
            target = CompanyValue(
                id=uuid4(), company_id=company_id, value_key=key,
                period_type=target_q, period_year=year, is_forecast=True,
            )
            # SAVEPOINT pro Insert: bei Race-Kollision Zeile neu laden,
            # Guards erneut anwenden.
            try:
                with db.begin_nested():
                    db.add(target)
                    db.flush()
            except IntegrityError:
                target = _reload_slot(db, company_id, key, target_q, year, True)
                if target is None or not _derivation_replaceable(target):
                    continue
        src_currency = next(
            (r.currency for r in reported.values() if r.currency), None
        )
        target.numeric_value = runrate
        target.source_name = (
            "Fortschreibung Quartals-Runrate (Q1-Qn-Durchschnitt)"
        )
        target.source_link = None
        target.primary_method = "calculated"
        target.is_forecast = True
        target.currency = src_currency or target.currency
        target.fetched_at = now
        target.last_refresh_attempt = now
        db.flush()
        written += 1
        # FY = Q1+Q2+Q3+Q4 ueber den bestehenden Mechanismus; danach die
        # konventionsgemaesse Quelle setzen (nur auf der frisch
        # abgeleiteten Zeile — 'Derived Annual'-Signatur).
        from app.values.routes import _refresh_fy_from_quarters
        _refresh_fy_from_quarters(db, company_id, key, year)
        fy_rows = (
            db.query(CompanyValue)
            .filter(
                CompanyValue.company_id == company_id,
                CompanyValue.value_key == key,
                CompanyValue.period_type == "FY",
                CompanyValue.period_year == year,
            )
            .all()
        )
        for r in fy_rows:
            if (
                not r.manually_overridden
                and r.numeric_value is not None
                and (r.source_name or "").startswith("Derived Annual")
            ):
                r.source_name = "Summe der Quartale (inkl. Fortschreibung)"
    db.flush()
    return written


def derive_balance_carry_forward(db: Session, company_id: UUID, year: int) -> int:
    """Point-in-Time-Bilanz-Keys: unberichtete Q4-/FY-Slots des laufenden
    FY mit dem letzten berichteten Quartalsstichtag fortschreiben.

    Bilanzstaende driften zwischen Stichtagen wenig — die Fortschreibung
    (z.B. Q3-Wert) ersetzt die Two-Stage-Forecast-Recherche fuer diese
    Keys vollstaendig. Ersetzbare Slots (two_stage_*/not_found/leer/
    calculated) werden ueberschrieben — die Two-Stage-Bilanz-Schaetzungen
    sind damit obsolet; manual/PDF und Provider-Actuals bleiben.
    net_debt danach via derive_net_debt_from_components neu rechnen
    (Aufruf-Reihenfolge im Refresh-Flow). Rueckgabe: Anzahl
    geschriebener Slots.
    """
    rows = _rows_for_year(db, company_id, year)
    written = 0
    for key in sorted(BALANCE_CARRY_FORWARD_KEYS):
        # Letzter berichteter Stichtag: hoechstes Quartal mit authoritativem
        # Actual (provider/manual/PDF). Ersetzbare Zeilen (two_stage_*/
        # calculated/web_*) sind Schaetzungen — kein Fortschreibungs-Anker.
        src: CompanyValue | None = None
        src_q: str | None = None
        for q in _Q_TYPES:
            actual = next(
                (r for r in rows
                 if r.value_key == key and r.period_type == q
                 and not r.is_forecast and r.numeric_value is not None
                 and not _derivation_replaceable(r)),
                None,
            )
            if actual is not None:
                src, src_q = actual, q
        if src is None:
            continue
        for pt in ("Q4", "FY"):
            if pt == src_q:
                continue
            slot_rows = [
                r for r in rows
                if r.value_key == key and r.period_type == pt
            ]
            if any(not _derivation_replaceable(r) for r in slot_rows):
                continue
            # Zielzeile: Forecast-Slot bevorzugt, sonst den (ersetzbaren)
            # anderen Slot umziehen (Muster derive_missing_fcf).
            target = next((r for r in slot_rows if r.is_forecast), None)
            if target is None:
                target = next(iter(slot_rows), None)
            now = datetime.now(timezone.utc)
            if target is None:
                target = CompanyValue(
                    id=uuid4(), company_id=company_id, value_key=key,
                    period_type=pt, period_year=year, is_forecast=True,
                )
                # SAVEPOINT pro Insert: bei Race-Kollision Zeile neu laden,
                # Guards erneut anwenden.
                try:
                    with db.begin_nested():
                        db.add(target)
                        db.flush()
                except IntegrityError:
                    target = _reload_slot(db, company_id, key, pt, year, True)
                    if target is None or not _derivation_replaceable(target):
                        continue
            target.numeric_value = src.numeric_value
            target.source_name = (
                f"Fortschreibung letzter Bilanzstichtag ({src_q})"
            )
            target.source_link = None
            target.primary_method = "calculated"
            target.is_forecast = True
            target.currency = src.currency or target.currency
            target.fetched_at = now
            target.last_refresh_attempt = now
            written += 1
    db.flush()
    return written


def derive_sbc_quarters(db: Session, company_id: UUID, year: int) -> int:
    """SBC-Quartale fuer Annual-only-Reporter: FY gleichmaessig auf Q1-Q4
    verteilen (FY/4). Kunden-Feedback: leere SBC-Quartale bei vorhandenem
    FY-Wert. Nur fehlende Zellen; berichtete Quartale bleiben unberuehrt."""
    rows = _rows_for_year(db, company_id, year)
    fy_row = _row_of(rows, "sbc", "FY")
    if fy_row is None or fy_row.numeric_value is None:
        return 0
    quarter_val = fy_row.numeric_value / Decimal("4")
    now = datetime.now(timezone.utc)
    written = 0
    for pt in _Q_TYPES:
        existing = _row_of(rows, "sbc", pt)
        if existing is not None and existing.numeric_value is not None:
            continue
        is_forecast = bool(fy_row.is_forecast)
        target = existing
        if target is None:
            target = CompanyValue(
                id=uuid4(), company_id=company_id, value_key="sbc",
                period_type=pt, period_year=year, is_forecast=is_forecast,
            )
            # SAVEPOINT pro Insert: bei Race-Kollision Zeile neu laden,
            # nur updaten wenn sie noch keinen Wert hat, sonst skip.
            try:
                with db.begin_nested():
                    db.add(target)
                    db.flush()
            except IntegrityError:
                target = _reload_slot(db, company_id, "sbc", pt, year, is_forecast)
                if target is None or target.numeric_value is not None:
                    continue
        target.numeric_value = quarter_val
        target.source_name = (
            f"Convention: annual-only SBC disclosure, FY {fy_row.numeric_value} / 4 = {quarter_val}"
        )[:4096]
        target.primary_method = "calculated"
        target.is_forecast = is_forecast
        target.currency = fy_row.currency or target.currency
        target.fetched_at = now
        target.last_refresh_attempt = now
        written += 1
    db.flush()
    return written
