"""Factor-based FY estimates for the running fiscal year.

Logik (aligned mit dem aktuellen FY-Flow):

1. **Wenn ein Quartalsbericht (Q1/Q2/Q3) fuer das laufende FY hochgeladen ist**:
   - **FLOW keys** (NI, FCF, SBC, Buyback, Dividends): Wachstumsfaktor aus
     dem aktuellsten verfuegbaren Quartal.
       factor    = cum_quarters_N / cum_quarters_N-1   (gleiche Q's beider Jahre)
       FY_N e    = FY_N-1 × factor
     YTD vs standalone wird via period_basis-Metadata sauber unterschieden.
   - **BALANCE keys** (Net Debt, Shares Outstanding): Snapshot des
     aktuellsten verfuegbaren Quartals (Punkt-in-Zeit).

2. **Wenn KEINE Quartalsberichte fuer das laufende FY**:
   - **FLOW + BALANCE**: estimate = FY_N-1 (no-growth-Annahme).

3. **Wenn nicht mal FY_N-1 vorliegt**: None — User muss den Vorjahres-AR
   hochladen oder Claude-Recherche triggern.

Q4 wird ignoriert (Q4-Daten = FY-Daten, deshalb kein separater Upload).
"""
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.ir_documents.models import ExtractionStatus, IRDocument, PeriodCoverage
from app.values.models import CompanyValue


FLOW_KEYS = frozenset({
    "net_income",
    "fcf",
    "sbc",
    "buyback_volume",
    "dividends",
})

BALANCE_KEYS = frozenset({
    "net_debt",
    "shares_outstanding",
})

ESTIMABLE_KEYS = frozenset(FLOW_KEYS | BALANCE_KEYS)

# Q4 NICHT in der Liste — Q4-Daten sind im Annual Report enthalten,
# kein separater Upload-Slot in der UI. Quartal-Estimates fuer das
# laufende FY ziehen also nur Q1/Q2/Q3 heran.
QUARTERS = ("Q1", "Q2", "Q3")

# Schwelle: wenn der Vorjahres-Cumulative-Q kleiner als diese Fraktion
# des Vorjahres-FY-Werts ist, blasen winzige Aenderungen den Faktor
# unrealistisch auf → wir verzichten auf den Faktor-Pfad und fallen auf
# den FY-Fallback zurueck.
_NEAR_ZERO_FRACTION = Decimal("0.01")


@dataclass
class EstimateResult:
    value: Decimal
    method: str  # "flow_factor" | "balance_snapshot" | "fy_fallback"
    explanation: str
    quarters_used: list[str] = field(default_factory=list)
    factor: Decimal | None = None


def _value_at(
    db: Session,
    company_id: UUID,
    key: str,
    period_type: str,
    period_year: int,
) -> Decimal | None:
    row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == period_type,
            CompanyValue.period_year == period_year,
        )
        .one_or_none()
    )
    return row.numeric_value if row and row.numeric_value is not None else None


def _period_basis(
    db: Session,
    company_id: UUID,
    key: str,
    quarter: str,
    period_year: int,
) -> str | None:
    """Liest period_basis (YTD vs standalone) aus dem IRDocument-extraction_results,
    damit wir Q1+Q2+Q3 nicht doppelt zaehlen falls Q3 selbst YTD ist."""
    try:
        doc = (
            db.query(IRDocument)
            .filter(
                IRDocument.company_id == company_id,
                IRDocument.period_year == period_year,
                IRDocument.period_coverage == PeriodCoverage(quarter),
                IRDocument.extraction_status == ExtractionStatus.DONE,
            )
            .order_by(IRDocument.uploaded_at.desc())
            .first()
        )
    except (ValueError, KeyError):
        return None
    if doc is None or not doc.extraction_results:
        return None
    entry = doc.extraction_results.get(key)
    if not isinstance(entry, dict):
        return None
    return entry.get("period_basis")


def _is_ytd(basis: str | None) -> bool:
    return bool(basis and "YTD" in basis.upper())


def _matched_quarter_pair(
    db: Session,
    company_id: UUID,
    key: str,
    target_year: int,
    prev_year: int,
) -> tuple[str, Decimal, Decimal, bool] | None:
    """Findet das spaeteste gemeinsame Quartal beider Jahre und gibt
    (quarter, target_value, prev_value, ytd_note) zurueck.

    Apples-to-apples-Regel: die period_basis (YTD vs standalone) muss
    bei target und prev passen, sonst skippen wir das Quartal und probieren
    ein frueheres. Q1 ist Sonderfall — YTD-Q1 = standalone-Q1 (beides
    3-Monatsfenster), also egal welcher Basis-Tag dranklebt.
    """
    for q in reversed(QUARTERS):  # Q3 → Q2 → Q1
        tv = _value_at(db, company_id, key, q, target_year)
        pv = _value_at(db, company_id, key, q, prev_year)
        if tv is None or pv is None:
            continue
        if q == "Q1":
            return (q, tv, pv, False)
        target_basis = _period_basis(db, company_id, key, q, target_year)
        prev_basis = _period_basis(db, company_id, key, q, prev_year)
        if _is_ytd(target_basis) == _is_ytd(prev_basis):
            return (q, tv, pv, _is_ytd(target_basis))
        # Basis-Mismatch (z.B. target standalone, prev YTD) → frueher probieren
    return None


def _latest_quarter_value(
    db: Session,
    company_id: UUID,
    key: str,
    period_year: int,
) -> tuple[Decimal | None, str | None]:
    """Holt den letzten verfuegbaren Quartals-Wert (Q3 → Q2 → Q1).
    Returns (value, quarter_label) oder (None, None)."""
    for q in reversed(QUARTERS):
        v = _value_at(db, company_id, key, q, period_year)
        if v is not None:
            return v, q
    return None, None


def _fy_fallback(
    prev_fy_val: Decimal,
    target_fy_year: int,
    prev_fy: int,
    key: str,
    method: str,
    reason: str,
) -> EstimateResult:
    """FY[N-1]-Wert als Estimate ohne Wachstumsannahme."""
    return EstimateResult(
        value=prev_fy_val,
        method=method,
        explanation=(
            f"Schaetzung FY{target_fy_year} = FY{prev_fy}-Wert ({key}) = {prev_fy_val:,.0f}. "
            f"{reason}"
        ),
    )


def compute_estimate(
    db: Session,
    company_id: UUID,
    key: str,
    target_fy_year: int,
) -> EstimateResult | None:
    """Estimate fuer FY[target_fy_year] berechnen.

    Logik:
    - BALANCE keys → letzter Q-Snapshot (target_fy_year), sonst FY[N-1]-Fallback
    - FLOW keys → Q-Faktor-Methode (target_fy_year vs prev_fy gleiches Q),
                  sonst FY[N-1]-Fallback
    Returns None wenn weder Q-Daten noch FY[N-1] vorliegen.
    """
    if key not in ESTIMABLE_KEYS:
        return None

    prev_fy = target_fy_year - 1
    prev_fy_val = _value_at(db, company_id, key, "FY", prev_fy)

    if key in BALANCE_KEYS:
        snap, snap_q = _latest_quarter_value(db, company_id, key, target_fy_year)
        if snap is not None:
            return EstimateResult(
                value=snap,
                method="balance_snapshot",
                explanation=(
                    f"Bilanz-Snapshot {snap_q} {target_fy_year} = {snap:,.0f} "
                    f"(Punkt-in-Zeit; bei Bilanzposten keine Faktor-Hochrechnung)."
                ),
                quarters_used=[snap_q] if snap_q else [],
            )
        if prev_fy_val is not None:
            return _fy_fallback(
                prev_fy_val, target_fy_year, prev_fy, key,
                method="fy_fallback",
                reason="Keine Quartalsberichte hochgeladen — Annahme: keine Veraenderung ggue. Vorjahr.",
            )
        return None

    # FLOW key — Apples-to-apples-Vergleich des spaetesten gemeinsamen Quartals.
    pair = _matched_quarter_pair(db, company_id, key, target_fy_year, prev_fy)

    if pair is None:
        # Kein gemeinsames Quartal → FY-Fallback wenn moeglich.
        if prev_fy_val is not None:
            return _fy_fallback(
                prev_fy_val, target_fy_year, prev_fy, key,
                method="fy_fallback",
                reason=(
                    "Keine gemeinsamen Quartalsberichte fuer Vergleich verfuegbar — "
                    "Annahme: keine Veraenderung ggue. Vorjahr."
                ),
            )
        return None

    if prev_fy_val is None or prev_fy_val == 0:
        return None

    q, target_v, prev_v, is_ytd_pair = pair

    if prev_v == 0:
        return _fy_fallback(
            prev_fy_val, target_fy_year, prev_fy, key,
            method="fy_fallback",
            reason=f"{q} {prev_fy} ist 0 — kein Wachstumsfaktor moeglich.",
        )

    # Sanity-Gates: Faktor-Pfad nur wenn Signal robust.
    if (target_v * prev_v) < 0:
        return _fy_fallback(
            prev_fy_val, target_fy_year, prev_fy, key,
            method="fy_fallback",
            reason=(
                f"{q} swingt zwischen {prev_fy} ({prev_v:,.0f}) und {target_fy_year} "
                f"({target_v:,.0f}) im Vorzeichen — Faktor unzuverlaessig."
            ),
        )
    if abs(prev_v) < abs(prev_fy_val) * _NEAR_ZERO_FRACTION:
        return _fy_fallback(
            prev_fy_val, target_fy_year, prev_fy, key,
            method="fy_fallback",
            reason=(
                f"{q} {prev_fy} = {prev_v:,.0f} ist < 1 % des FY{prev_fy}-Werts "
                f"({prev_fy_val:,.0f}) — Faktor unzuverlaessig."
            ),
        )

    factor = target_v / prev_v
    estimate = prev_fy_val * factor
    delta_pct = (factor - Decimal("1")) * Decimal("100")
    ytd_note = " (YTD)" if is_ytd_pair else ""

    explanation = (
        f"Schaetzung FY{target_fy_year} = FY{prev_fy} × Faktor (gleiches Quartal{ytd_note}).  "
        f"FY{prev_fy} ({key}) = {prev_fy_val:,.0f}.  "
        f"{q} {target_fy_year} = {target_v:,.0f}, {q} {prev_fy} = {prev_v:,.0f}, "
        f"Faktor = {factor:.4f} ({delta_pct:+.2f} % YoY).  "
        f"Resultat = {estimate:,.0f}."
    )

    return EstimateResult(
        value=estimate,
        method="flow_factor",
        explanation=explanation,
        quarters_used=[f"{q} (YTD)"] if is_ytd_pair else [q],
        factor=factor,
    )
