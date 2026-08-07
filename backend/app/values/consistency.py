"""Cross-Metrik-Konsistenz: deterministische Checks nach jedem Refresh.

Prompts koennen Konsistenz nur fordern — hier wird sie geprueft (Flags)
bzw. hergestellt (net_debt aus Komponenten). Flags werden bei jedem Lauf
neu gesetzt und geloescht, wenn der Check wieder besteht.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.values.models import CompanyValue
from app.values.quarterly_estimates import SUMMABLE_QUARTERLY_KEYS

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


def _rel_diff(a: Decimal, b: Decimal) -> Decimal:
    denom = max(abs(a), abs(b))
    if denom == 0:
        return Decimal("0")
    return abs(a - b) / denom


def validate_cross_metrics(db: Session, company_id: UUID, year: int) -> list[str]:
    """Prueft die Kern-Identitaeten und setzt/loescht consistency_flags.

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
