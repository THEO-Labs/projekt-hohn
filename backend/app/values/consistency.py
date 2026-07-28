"""Cross-Metrik-Konsistenz: deterministische Checks nach jedem Refresh.

Prompts koennen Konsistenz nur fordern — hier wird sie geprueft (Flags)
bzw. hergestellt (net_debt aus Komponenten). Flags werden bei jedem Lauf
neu gesetzt und geloescht, wenn der Check wieder besteht.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

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

_NET_DEBT_COMPONENTS = ("st_debt", "lt_debt", "cash_and_equivalents", "st_investments")


def _rows_for_year(db: Session, company_id: UUID, year: int) -> list[CompanyValue]:
    return (
        db.query(CompanyValue)
        .filter(CompanyValue.company_id == company_id, CompanyValue.period_year == year)
        .all()
    )


def _value_of(rows: list[CompanyValue], key: str, period_type: str) -> Decimal | None:
    for r in rows:
        if r.value_key == key and r.period_type == period_type:
            return r.numeric_value
    return None


def _row_of(rows: list[CompanyValue], key: str, period_type: str) -> CompanyValue | None:
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
                period_type=pt, period_year=year,
            )
            db.add(target)
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
        target = existing or CompanyValue(
            id=uuid4(), company_id=company_id, value_key="operating_cash_flow",
            period_type=pt, period_year=year,
        )
        if existing is None:
            db.add(target)
        target.numeric_value = derived
        target.source_name = (
            f"Derived (identity): fcf {fcf_row.numeric_value} + capex "
            f"{abs(capex_row.numeric_value)} = {derived}"
        )[:4096]
        target.primary_method = "calculated"
        target.is_forecast = bool(fcf_row.is_forecast or capex_row.is_forecast)
        target.currency = fcf_row.currency or target.currency
        target.fetched_at = now
        target.last_refresh_attempt = now
        written += 1
    db.flush()
    return written
