"""Factor-based FY estimates for the running fiscal year.

Logik (aligned mit dem aktuellen FY-Flow):

1. **Wenn ein Quartalsbericht (Q1/Q2/Q3) für das laufende FY hochgeladen ist**:
   - **FLOW keys** (NI, FCF, SBC, Buyback, Dividends): Wachstumsfaktor aus
     dem aktuellsten verfügbaren Quartal.
       factor    = cum_quarters_N / cum_quarters_N-1   (gleiche Q's beider Jahre)
       FY_N e    = FY_N-1 × factor
     YTD vs standalone wird via period_basis-Metadata sauber unterschieden.
   - **BALANCE keys** (Net Debt, Shares Outstanding): Snapshot des
     aktuellsten verfügbaren Quartals (Punkt-in-Zeit).

2. **Wenn KEINE Quartalsberichte für das laufende FY**:
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
    "ebitda",
})

BALANCE_KEYS = frozenset({
    "net_debt",
    "shares_outstanding",
})

ESTIMABLE_KEYS = frozenset(FLOW_KEYS | BALANCE_KEYS)

# Q4 NICHT in der Liste — Q4-Daten sind im Annual Report enthalten,
# kein separater Upload-Slot in der UI. Quartal-Estimates für das
# laufende FY ziehen also nur Q1/Q2/Q3 heran.
QUARTERS = ("Q1", "Q2", "Q3")

# Schwelle: wenn der Vorjahres-Cumulative-Q kleiner als diese Fraktion
# des Vorjahres-FY-Werts ist, blasen winzige Änderungen den Faktor
# unrealistisch auf → wir verzichten auf den Faktor-Pfad und fallen auf
# den FY-Fallback zurück.
# 5% statt 1% — bei FCF-Saisonalität entstehen sonst trotzdem extreme Faktoren
# (z.B. Q3-YTD = 5% von FY[N-1] aber target_v hoch -> 20x explosion).
_NEAR_ZERO_FRACTION = Decimal("0.05")

# Hard-Cap für den Faktor — saisonale Q1-FCF-Werte (Working-Capital-Aufbau,
# typisch negativ bei Industrials) produzieren mathematisch korrekte aber
# wirtschaftlich unsinnige Faktoren wenn man sie auf einen positiven
# FY-Cashflow skaliert. Über 5x in beiden Richtungen → FY-Fallback.
_MAX_FACTOR = Decimal("5")


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
    damit wir Q1+Q2+Q3 nicht doppelt zählen falls Q3 selbst YTD ist."""
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
    """Findet das späteste gemeinsame Quartal beider Jahre und gibt
    (quarter, target_value, prev_value, ytd_note) zurück.

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
        # Wenn beide period_basis fehlen (z.B. alte Extraktionen ohne YTD-Tag),
        # nehmen wir YTD an — das ist bei Q2/Q3-Reports der dominante Standard
        # (>95% der IFRS+GAAP Q-Reports sind YTD-basiert). Sonst Basis-Match
        # erforderlich.
        if target_basis is None and prev_basis is None:
            return (q, tv, pv, True)  # assume YTD
        if target_basis is None or prev_basis is None:
            # nur einer hat None — ambivalent, lieber frueheres Q probieren
            continue
        if _is_ytd(target_basis) == _is_ytd(prev_basis):
            return (q, tv, pv, _is_ytd(target_basis))
        # Basis-Mismatch (z.B. target standalone, prev YTD) → frueher probieren
    return None


def _latest_quarter_value(
    db: Session,
    company_id: UUID,
    key: str,
    period_year: int,
) -> tuple[Decimal | None, str | None, int | None]:
    """Holt den letzten verfügbaren Quartals-Wert.
    Reihenfolge: target_year Q3→Q2→Q1, dann prev_year Q3→Q2→Q1.
    Für BALANCE-Snapshots ist auch ein Vorjahres-Q3 besser als FY[N-2].
    Returns (value, quarter_label, year) oder (None, None, None)."""
    for year in (period_year, period_year - 1):
        for q in reversed(QUARTERS):
            v = _value_at(db, company_id, key, q, year)
            if v is not None:
                return v, q, year
    return None, None, None


def _fy_fallback(
    prev_fy_val: Decimal,
    target_fy_year: int,
    prev_fy: int,
    key: str,
    method: str,
    reason: str,
    currency: str | None = None,
) -> EstimateResult:
    """FY[N-1]-Wert als Estimate ohne Wachstumsannahme."""
    cur = f" {currency}" if currency else ""
    return EstimateResult(
        value=prev_fy_val,
        method=method,
        explanation=(
            f"Schätzung FY{target_fy_year} = FY{prev_fy}-Wert ({key}) = {prev_fy_val:,.0f}{cur}. "
            f"{reason}"
        ),
    )


def compute_estimate(
    db: Session,
    company_id: UUID,
    key: str,
    target_fy_year: int,
    *,
    currency: str | None = None,
) -> EstimateResult | None:
    """Estimate für FY[target_fy_year] berechnen.

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
        snap, snap_q, snap_year = _latest_quarter_value(db, company_id, key, target_fy_year)
        if snap is not None:
            note = "" if snap_year == target_fy_year else f" (jüngster verfügbarer Q-Snapshot — kein FY{target_fy_year}-Q-Report hochgeladen)"
            # Caveat für shares_outstanding: Quartals-GuV liefert oft Diluted
            # Weighted Average Shares (Periode), nicht den year-end Punkt-in-Zeit-
            # Bestand. Frontend kann das via source_name lesen und beim Drilldown
            # transparent machen.
            extra = ""
            if key == "shares_outstanding":
                extra = (" Hinweis: Q-GuV-Wert ist meist 'Diluted Weighted Average' "
                         "der Periode, nicht der year-end Bestand — für Market-Cap-"
                         "Berechnung kann das leicht abweichen.")
            return EstimateResult(
                value=snap,
                method="balance_snapshot",
                explanation=(
                    f"Bilanz-Snapshot {snap_q} {snap_year} = {snap:,.0f}{note} "
                    f"(Punkt-in-Zeit; bei Bilanzposten keine Faktor-Hochrechnung).{extra}"
                ),
                quarters_used=[f"{snap_q} {snap_year}"] if snap_q else [],
            )
        if prev_fy_val is not None:
            return _fy_fallback(
                prev_fy_val, target_fy_year, prev_fy, key, currency=currency,
                method="fy_fallback",
                reason=f"Keine Q-Reports für FY{target_fy_year} oder FY{prev_fy} hochgeladen — Annahme: keine Veränderung ggue. Vorjahr.",
            )
        return None

    # FLOW key — Apples-to-apples-Vergleich des spätesten gemeinsamen Quartals.
    pair = _matched_quarter_pair(db, company_id, key, target_fy_year, prev_fy)

    if pair is None:
        # Kein gemeinsames Quartal → FY-Fallback wenn möglich.
        # Hinweis: Die UI sperrt diesen Pfad bereits per getEstimateLockReason
        # wenn FY[target] gar keine Q-Reports hat — der Fallback hier greift
        # nur als Defense-in-Depth (z.B. fuer US-Filer ueber Provider-Daten,
        # oder wenn ein Q-Report zwar hochgeladen ist aber den key nicht ausweist).
        if prev_fy_val is not None:
            target_qs = [q for q in QUARTERS if _value_at(db, company_id, key, q, target_fy_year) is not None]
            prev_qs = [q for q in QUARTERS if _value_at(db, company_id, key, q, prev_fy) is not None]
            missing = (
                f"FY{target_fy_year} hat {key} in {target_qs or 'keinem Quartal'}, "
                f"FY{prev_fy} hat {key} in {prev_qs or 'keinem Quartal'}. "
                f"Manche Firmen berichten {key} nur jaehrlich, nicht pro Quartal. "
                f"Faktor-Methode braucht den Wert im gleichen Quartal beider Jahre."
            )
            return _fy_fallback(
                prev_fy_val, target_fy_year, prev_fy, key, currency=currency,
                method="fy_fallback",
                reason=f"{missing} Annahme: keine Veränderung ggue. Vorjahr.",
            )
        return None

    if prev_fy_val is None:
        # Kein FY-Basis vorhanden → kein Estimate moeglich. Caller (UI) sieht None
        # und zeigt 'Wert nicht gefunden' mit Hint dass Vorjahres-AR fehlt.
        return None
    if prev_fy_val == 0:
        # FY[N-1] ist 0 — Faktor-Multiplikation wuerde immer 0 ergeben.
        # Geben wir 0 zurueck mit klarer Erklaerung statt None (User-Anforderung:
        # immer einen Wert + immer eine Begruendung).
        cur = f" {currency}" if currency else ""
        return EstimateResult(
            value=Decimal("0"),
            method="fy_fallback",
            explanation=(
                f"Schaetzung FY{target_fy_year} = 0{cur}. "
                f"FY{prev_fy} ({key}) war 0 — Faktor-Hochrechnung nicht moeglich. "
                f"Bei Buyback/Dividend kann das legitim sein (kein Programm aktiv); "
                f"sonst Vorjahres-AR pruefen ob der Wert wirklich 0 war."
            ),
        )

    q, target_v, prev_v, is_ytd_pair = pair

    if prev_v == 0:
        return _fy_fallback(
            prev_fy_val, target_fy_year, prev_fy, key, currency=currency,
            method="fy_fallback",
            reason=f"{q} {prev_fy} ist 0 — kein Wachstumsfaktor möglich.",
        )

    # Target = 0: Faktor wuerde 0 ergeben → estimate = 0. Das ist meist falsch
    # (Buyback/Dividend/Capex sind unregelmaessig pro Quartal — ein Q ohne
    # Aktivität heisst nicht dass FY auch 0 ist). Lieber FY-Fallback.
    if target_v == 0:
        return _fy_fallback(
            prev_fy_val, target_fy_year, prev_fy, key, currency=currency,
            method="fy_fallback",
            reason=(
                f"{q} {target_fy_year} ist 0 (z.B. Buyback/Dividend nicht in dem Quartal). "
                f"Faktor-Berechnung wuerde Estimate auf 0 setzen — unrealistisch für "
                f"unregelmaessige Cash-Outflows."
            ),
        )

    # Sanity-Gates: Faktor-Pfad nur wenn Signal robust.
    if (target_v * prev_v) < 0:
        return _fy_fallback(
            prev_fy_val, target_fy_year, prev_fy, key, currency=currency,
            method="fy_fallback",
            reason=(
                f"{q} swingt zwischen {prev_fy} ({prev_v:,.0f}) und {target_fy_year} "
                f"({target_v:,.0f}) im Vorzeichen — Faktor unzuverlaessig."
            ),
        )
    # Seasonality-Gate: wenn Q-Werte ein anderes Vorzeichen haben als die
    # FY-Basis (z.B. negatives Q1-FCF bei industriellen Working-Capital-
    # Aufbau, aber positives FY-FCF), würde der "positive Faktor × positive
    # FY = inflated estimate"-Effekt zuschlagen. → FY-Fallback.
    if (target_v * prev_fy_val) < 0 or (prev_v * prev_fy_val) < 0:
        return _fy_fallback(
            prev_fy_val, target_fy_year, prev_fy, key, currency=currency,
            method="fy_fallback",
            reason=(
                f"{q}-Werte ({target_v:,.0f} / {prev_v:,.0f}) haben anderes Vorzeichen "
                f"als FY{prev_fy} ({prev_fy_val:,.0f}) — saisonale Verzerrung, "
                f"Faktor würde Estimate übertreiben."
            ),
        )
    if abs(prev_v) < abs(prev_fy_val) * _NEAR_ZERO_FRACTION:
        return _fy_fallback(
            prev_fy_val, target_fy_year, prev_fy, key, currency=currency,
            method="fy_fallback",
            reason=(
                f"{q} {prev_fy} = {prev_v:,.0f} ist < 1 % des FY{prev_fy}-Werts "
                f"({prev_fy_val:,.0f}) — Faktor unzuverlaessig."
            ),
        )

    factor = target_v / prev_v

    # Hard-Cap: extreme Faktoren entstehen oft aus saisonal verzerrten
    # Q-Werten (z.B. Q1 FCF negativ in beiden Jahren bei Industrials).
    if abs(factor) > _MAX_FACTOR:
        return _fy_fallback(
            prev_fy_val, target_fy_year, prev_fy, key, currency=currency,
            method="fy_fallback",
            reason=(
                f"Faktor {q} {target_fy_year}/{q} {prev_fy} = {factor:.2f} liegt über "
                f"+/-{_MAX_FACTOR} (vermutlich Saisonalität) — Faktor zu unzuverlaessig, "
                f"FY-Fallback statt extrapolieren."
            ),
        )

    estimate = prev_fy_val * factor
    delta_pct = (factor - Decimal("1")) * Decimal("100")
    ytd_note = " (YTD)" if is_ytd_pair else ""
    cur = f" {currency}" if currency else ""

    # Caveat wenn nur Q1-Datapoint verwendet — bei stark saisonalen Industrials
    # (z.B. Aircraft-Hersteller mit Q4-lastigen Deliveries) ist Q1-Faktor wenig
    # repräsentativ für das ganze Jahr. User-Hinweis im Drilldown.
    confidence_note = ""
    if q == "Q1" and not is_ytd_pair:
        confidence_note = (
            "  HINWEIS: Estimate basiert auf einem einzelnen Q1-Datapoint — bei "
            "saisonalen Geschäftsmodellen (z.B. Industrials mit Q4-lastigen "
            "Deliveries oder Retail mit Q4-Weihnachtsgeschaeft) limited aussagekraeftig. "
            "Mit Q2/Q3-Daten wird der Estimate robuster."
        )

    explanation = (
        f"Schätzung FY{target_fy_year} = FY{prev_fy} × Faktor (gleiches Quartal{ytd_note}).  "
        f"Werte in Originalwährung{f' ({currency})' if currency else ''} — UI rechnet ggf. in Anzeige-Währung um.  "
        f"FY{prev_fy} ({key}) = {prev_fy_val:,.0f}{cur}.  "
        f"{q} {target_fy_year} = {target_v:,.0f}{cur}, {q} {prev_fy} = {prev_v:,.0f}{cur}, "
        f"Faktor = {factor:.4f} ({delta_pct:+.2f} % YoY).  "
        f"Resultat = {estimate:,.0f}{cur}.{confidence_note}"
    )

    return EstimateResult(
        value=estimate,
        method="flow_factor",
        explanation=explanation,
        quarters_used=[f"{q} (YTD)"] if is_ytd_pair else [q],
        factor=factor,
    )
