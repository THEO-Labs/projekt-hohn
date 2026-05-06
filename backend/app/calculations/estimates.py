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


def _gather_flow_cumulative(
    db: Session,
    company_id: UUID,
    key: str,
    period_year: int,
) -> tuple[Decimal | None, list[str], bool]:
    """Aggregiert die verfuegbaren Quartals-Werte zu einer Cumulative-Zahl.

    Returns (value, quarters_used_label, used_ytd).

    Wenn EIN Quartal YTD-Format hat, nehmen wir das hoechste-Q-YTD direkt
    (enthaelt schon die fruehen Quartale). Sonst summieren wir standalone.
    """
    ytd_entries: list[tuple[str, Decimal]] = []
    standalone_entries: list[tuple[str, Decimal]] = []
    for q in QUARTERS:
        v = _value_at(db, company_id, key, q, period_year)
        if v is None:
            continue
        basis = _period_basis(db, company_id, key, q, period_year)
        is_ytd = bool(basis and "YTD" in basis.upper())
        if is_ytd:
            ytd_entries.append((q, v))
        else:
            standalone_entries.append((q, v))

    if ytd_entries:
        last_q, last_v = ytd_entries[-1]
        return last_v, [f"{last_q} (YTD)"], True
    if standalone_entries:
        total = sum((v for _, v in standalone_entries), Decimal("0"))
        return total, [q for q, _ in standalone_entries], False
    return None, [], False


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

    # FLOW key
    cum_target, qs_target, ytd_t = _gather_flow_cumulative(db, company_id, key, target_fy_year)

    if cum_target is None:
        # Keine Quartalsdaten → FY-Fallback wenn moeglich.
        if prev_fy_val is not None:
            return _fy_fallback(
                prev_fy_val, target_fy_year, prev_fy, key,
                method="fy_fallback",
                reason="Keine Quartalsberichte hochgeladen — Annahme: keine Veraenderung ggue. Vorjahr.",
            )
        return None

    if prev_fy_val is None or prev_fy_val == 0:
        # Kein FY-Basiswert → wir koennten nicht skalieren. Nehmen den
        # Q-Cumulative selbst als Untergrenze, aber das ist nur ehrlich wenn
        # Q3-YTD vorliegt (≈ 75 % des Jahres). Sonst None.
        return None

    cum_prev, qs_prev, ytd_p = _gather_flow_cumulative(db, company_id, key, prev_fy)

    if cum_prev is None:
        # Keine Vorjahres-Q-Daten → kein Faktor moeglich, FY-Fallback.
        return _fy_fallback(
            prev_fy_val, target_fy_year, prev_fy, key,
            method="fy_fallback",
            reason=f"Q{prev_fy}-Daten fehlen — kein Wachstumsfaktor moeglich.",
        )

    if cum_prev == 0:
        return _fy_fallback(
            prev_fy_val, target_fy_year, prev_fy, key,
            method="fy_fallback",
            reason=f"Vorjahres-Cumulative-Q ist 0 — kein Wachstumsfaktor moeglich.",
        )

    # Sanity-Gates: Faktor-Pfad nur wenn Signal robust.
    # 1) Sign-flip — Q swing von negativ zu positiv (oder vice versa) wuerde
    #    den FY-Estimate mit falschem Vorzeichen versehen.
    if (cum_target * cum_prev) < 0:
        return _fy_fallback(
            prev_fy_val, target_fy_year, prev_fy, key,
            method="fy_fallback",
            reason=f"Q-Werte haben Vorzeichenwechsel zwischen {prev_fy}/{target_fy_year} — Faktor unzuverlaessig.",
        )
    # 2) Near-zero Nenner — kleine prev-Q-Zahl macht den Faktor instabil.
    if abs(cum_prev) < abs(prev_fy_val) * _NEAR_ZERO_FRACTION:
        return _fy_fallback(
            prev_fy_val, target_fy_year, prev_fy, key,
            method="fy_fallback",
            reason=f"Vorjahres-Cumulative-Q < 1 % des FY{prev_fy}-Werts — Faktor unzuverlaessig.",
        )

    factor = cum_target / cum_prev
    estimate = prev_fy_val * factor
    qs_label = "+".join(qs_target) if qs_target == qs_prev else f"{'+'.join(qs_target)} vs {'+'.join(qs_prev)}"
    delta_pct = (factor - Decimal("1")) * Decimal("100")
    ytd_note = " (YTD)" if (ytd_t or ytd_p) else ""

    explanation = (
        f"Schaetzung FY{target_fy_year} = FY{prev_fy} × Faktor.{ytd_note}  "
        f"FY{prev_fy} ({key}) = {prev_fy_val:,.0f}.  "
        f"{qs_label} {target_fy_year} = {cum_target:,.0f}, "
        f"{qs_label} {prev_fy} = {cum_prev:,.0f}, "
        f"Faktor = {factor:.4f} ({delta_pct:+.2f} % YoY).  "
        f"Resultat = {estimate:,.0f}."
    )

    return EstimateResult(
        value=estimate,
        method="flow_factor",
        explanation=explanation,
        quarters_used=qs_target,
        factor=factor,
    )
