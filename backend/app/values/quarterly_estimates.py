"""Per-Quartal-Estimate-Modus fuer Forward-Year FY-Estimates.

Statt einen einzelnen Claude-Call fuer den gesamten FY zu machen, splittet
diese Pipeline pro Key + FY in Q1/Q2/Q3/Q4-Calls auf:

  - Q-Actual aus DB (10-Q-PDF) vorhanden? -> uebernehmen, kein Claude-Call
  - Manual-Override auf Q-Row vorhanden? -> uebernehmen
  - Sonst: fokussierter Claude-Call NUR fuer dieses eine Quartal

Aggregation auf FY-Ebene:
  - Sum-Keys (NI, EBITDA, FCF, SBC, Buyback, Dividends): FY = Sigma Q1+Q2+Q3+Q4
  - Point-in-Time (Net Debt): FY = Q4-Endstand (Bilanz-Snapshot)

Vorteile:
  - Mathematische FY-Konsistenz (kein LLM-Sum-Drift)
  - Saubere Trennung Actuals vs Estimates pro Q
  - Backtests (alle 4 Q-Actuals vorhanden): 0 Claude-Calls
  - Drilldown-UI: Q-Tabelle aus strukturierten DB-Rows statt fragilem Regex-Parser
"""
from __future__ import annotations

import logging
from decimal import Decimal
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.values.currency_keys import CURRENCY_KEYS
from app.values.models import CompanyValue, SourceType, ValueDefinition

if TYPE_CHECKING:
    from app.companies.models import Company

logger = logging.getLogger(__name__)


# Keys die per Quartal estimated werden (statt FY-monolith).
# Shares Outstanding ist KEIN Estimate-Wert — bleibt Live-Snapshot.
# Balance-Sheet-Keys (cash_and_equivalents, st_investments, st_debt, lt_debt)
# sind bewusst NICHT hier: sie sind instant-facts (period_days=0), EDGAR-Q-
# Standalone-Search kann sie nicht matchen, und Q1-Q3-Werte werden im UI eh
# nicht angezeigt. Sie laufen durch den Fallback-Pfad in _try_web_guidance
# als Single-FY-Call (period_type="FY") → 1 Call statt 4.
# net_debt bleibt drin weil bestehende Nutzung + FY-1-Backfill davon abhaengt.
QUARTERLY_ESTIMATE_KEYS = frozenset({
    # Income Statement
    "net_income", "revenue", "ebitda", "eps_diluted",
    # Cashflow
    "fcf", "operating_cash_flow", "capex", "sbc",
    "buyback_volume", "dividends",
    # Balance Sheet (point-in-time — bleibt fuer historische Kompatibilitaet)
    "net_debt",
})

# Cumulative: FY = Sigma Q1+Q2+Q3+Q4 (Income/Cashflow-Werte).
# eps_diluted ist per-Q reported und Annual != exakt Sigma(Q) wegen
# Weighted-Average-Diluted-Shares (Buybacks veraendern Denominator);
# fuer die Detail-Page ist die Sigma-Approximation akzeptabel.
SUMMABLE_QUARTERLY_KEYS = frozenset({
    "net_income", "revenue", "ebitda", "eps_diluted",
    "fcf", "operating_cash_flow", "capex", "sbc",
    "buyback_volume", "dividends",
})

# Point-in-Time: FY = Q4-Endstand (Bilanz-Snapshot). Aktuell nur net_debt.
POINT_IN_TIME_QUARTERLY_KEYS = frozenset({"net_debt"})

QUARTERS: tuple[str, str, str, str] = ("Q1", "Q2", "Q3", "Q4")


def _get_q_row(
    db: Session,
    company_id: UUID,
    key: str,
    period_year: int,
    quarter: str,
) -> CompanyValue | None:
    """Findet die existierende Q-Row (jede is_forecast Variante).
    Bevorzugt nicht-forecast (= Actual) wenn beide existieren."""
    rows = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == quarter,
            CompanyValue.period_year == period_year,
        )
        .order_by(CompanyValue.is_forecast.asc())
        .all()
    )
    return rows[0] if rows else None


def _upsert_q_estimate(
    db: Session,
    company_id: UUID,
    key: str,
    period_year: int,
    quarter: str,
    value: Decimal,
    source_name: str,
    source_link: str | None,
    currency: str | None,
    value_adjusted: Decimal | None = None,
    adjustments_note: str | None = None,
    adjustments_source: str | None = None,
) -> CompanyValue | None:
    """Persistiert einen Q-Estimate. Aktualisiert existierende Forecast-Row oder
    legt eine neue an. Actuals (is_forecast=False) und Manual-Overrides werden
    NIE ueberschrieben."""
    existing = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == quarter,
            CompanyValue.period_year == period_year,
            CompanyValue.is_forecast.is_(True),
        )
        .one_or_none()
    )
    actuals_blocking = (
        db.query(CompanyValue.id)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == quarter,
            CompanyValue.period_year == period_year,
            CompanyValue.is_forecast.is_(False),
            CompanyValue.numeric_value.isnot(None),
        )
        .first()
    )
    if actuals_blocking is not None:
        return None
    if existing and existing.manually_overridden:
        return existing
    # PDF-Q-Guidance (z.B. Management-Outlook fuer Q4 aus Q3-PDF) ist
    # authoritativ — wird nicht stumm durch Claude-Web-Estimate ueberschrieben.
    # (Future: Challenge-Mechanismus analog _try_web_guidance bei Divergenz.)
    if existing and existing.from_ir_pdf and existing.numeric_value is not None:
        return existing
    # CapEx Sign-Normalisierung — siehe _upsert_q_actual_from_provider.
    if key == "capex" and value is not None:
        value = abs(value)
    if key == "capex" and value_adjusted is not None:
        value_adjusted = abs(value_adjusted)
    now = datetime.now(timezone.utc)
    try:
        with db.begin_nested():
            if existing:
                existing.numeric_value = value
                existing.numeric_value_adjusted = value_adjusted
                existing.adjustments_note = (adjustments_note or "")[:4000] or None
                existing.adjustments_source = (adjustments_source or "")[:2048] or None
                existing.source_name = source_name[:4000]
                existing.source_link = source_link
                existing.currency = currency
                existing.fetched_at = now
                existing.from_ir_pdf = False
                existing.is_forecast = True
                existing.primary_method = "web_guidance"
                return existing
            cv = CompanyValue(
                id=uuid4(),
                company_id=company_id,
                value_key=key,
                period_type=quarter,
                period_year=period_year,
                numeric_value=value,
                numeric_value_adjusted=value_adjusted,
                adjustments_note=(adjustments_note or "")[:4000] or None,
                adjustments_source=(adjustments_source or "")[:2048] or None,
                source_name=source_name[:4000],
                source_link=source_link,
                currency=currency,
                fetched_at=now,
                is_forecast=True,
                primary_method="web_guidance",
            )
            db.add(cv)
            db.flush()
            return cv
    except IntegrityError as ie:
        logger.warning("Q-Estimate upsert %s/%s/%s/%s IntegrityError: %s",
                       company_id, key, quarter, period_year, str(ie)[:120])
        return None


def _estimate_single_quarter(
    db: Session,
    company: "Company",
    key: str,
    period_year: int,
    quarter: str,
    known_q_values: dict[str, Decimal],
    prev_fy_val: Decimal | None,
) -> tuple[Decimal | None, str | None, str | None, Decimal | None, str | None, str | None]:
    """Claude-Call fuer einen einzelnen Q-Wert. Returns
    (value, source_name, source_url, value_adjusted, adj_note, adj_source)."""
    from app.llm.claude import research_value
    from app.llm.claude import extract_research_value_adjusted

    vd = db.query(ValueDefinition).filter(ValueDefinition.key == key).one_or_none()
    if vd is None:
        return None, None, None, None, None, None
    label = f"{vd.label_en} ({vd.label_de})"

    q_actuals_for_prompt = {q: v for q, v in known_q_values.items() if q != quarter}

    # Saisonalitaets-Anker: Q1-Q4-Verteilung des Vorjahres aus DB.
    # Wird in Claude-Prompt injiziert damit Q-Saisonalitaet (z.B. MSFT-Q4 = staerkstes,
    # AVGO-Q4 = staerkstes, Retail-Q4 = Holiday-Peak) nicht ignoriert wird.
    # Greift nur fuer summable Keys (NI/EBITDA/FCF/SBC/Buyback/Dividends).
    q_pattern_prev_fy: dict[str, Decimal] | None = None
    if key in SUMMABLE_QUARTERLY_KEYS:
        prev_fy = period_year - 1
        prev_q_rows = (
            db.query(CompanyValue)
            .filter(
                CompanyValue.company_id == company.id,
                CompanyValue.value_key == key,
                CompanyValue.period_type.in_(("Q1", "Q2", "Q3", "Q4")),
                CompanyValue.period_year == prev_fy,
                CompanyValue.is_forecast.is_(False),
            )
            .all()
        )
        if len(prev_q_rows) >= 3:
            q_pattern_prev_fy = {
                r.period_type: r.numeric_value
                for r in prev_q_rows
                if r.numeric_value is not None
            }

    try:
        v, s, u, _p, content = research_value(
            company.name, company.ticker, label, company.currency,
            period_type=quarter, period_year=period_year, value_key=key,
            prev_fy_val=prev_fy_val,
            q_actuals=q_actuals_for_prompt or None,
            q_pattern_prev_fy=q_pattern_prev_fy,
        )
    except Exception as e:
        logger.warning("Q-Estimate Claude-Call failed %s/%s/%s/FY%s: %s",
                       company.ticker, key, quarter, period_year, e)
        return None, None, None, None, None, None
    if v is None:
        return None, None, None, None, None, None

    # 0-Value Reject: Fuer Metriken die immer positiv sein muessen (Revenue,
    # NI, EBITDA, OCF, FCF, EPS) ist v=0 fast immer ein Extraction-Fehler
    # (Claude fand nix und lieferte "0" statt zu schaetzen). Zurueckweisen
    # damit Fallback-Chain greift. Ausnahme: buyback_volume und sbc koennen
    # legitim 0 sein (Firma hat kein Programm).
    _NEVER_ZERO_KEYS = frozenset({
        "revenue", "net_income", "ebitda", "eps_diluted",
        "operating_cash_flow", "fcf", "capex", "dividends",
    })
    if v == 0 and key in _NEVER_ZERO_KEYS:
        logger.warning(
            "Q-Estimate 0-Value Reject %s/%s/%s/FY%s: Claude lieferte 0 fuer "
            "non-zero-Metrik. Wahrscheinlich Extraction-Fehler.",
            company.ticker, key, quarter, period_year,
        )
        return None, None, None, None, None, None

    # Skalierungs-Sanity-Reject (Billion-vs-Trillion-Bug):
    # Wenn Claude einen Q-Wert > 10x FY-1-Aggregat liefert (= 2.5x FY-Total in
    # einem einzelnen Q), ist das fast immer ein Unit-Misinterpretation-Bug.
    # Beispiel Alphabet FY2026 EBITDA Q2: Claude lieferte $41.5T (= 276x FY-1
    # EBITDA $150B). Sollte gerejected werden.
    if prev_fy_val is not None and prev_fy_val != 0:
        ratio = abs(v) / abs(prev_fy_val)
        if ratio > Decimal("10"):
            logger.warning(
                "Q-Estimate Skalierungs-Reject %s/%s/%s/FY%s: v=%.0f > 10x "
                "prev_fy_val=%.0f (ratio=%.1fx, vermutlich Unit-Bug Billion vs Trillion)",
                company.ticker, key, quarter, period_year,
                float(v), float(prev_fy_val), float(ratio),
            )
            return None, None, None, None, None, None
    elif q_actuals_for_prompt:
        # Fallback ohne prev_fy_val: gegen die anderen Q-Actuals checken
        max_known = max(
            (abs(qv) for qv in q_actuals_for_prompt.values() if qv is not None),
            default=None,
        )
        if max_known is not None and max_known > 0 and abs(v) > Decimal("50") * max_known:
            logger.warning(
                "Q-Estimate Skalierungs-Reject %s/%s/%s/FY%s: v=%.0f > 50x "
                "max_q_actual=%.0f (vermutlich Unit-Bug Billion vs Trillion)",
                company.ticker, key, quarter, period_year,
                float(v), float(max_known),
            )
            return None, None, None, None, None, None

    # Auto-Korrektur Kumulativ-Bug (Per-Q-Aggregation): Claude muss bei q_actuals-
    # Calls neben WERT auch KONTEXT_FY_TOTAL_FALLS_BEKANNT liefern. Wenn WERT
    # praktisch identisch zum FY-Total ist (Toleranz 2%), hat das Modell den
    # FY-Total ins WERT-Feld geschrieben statt den Standalone-Q-Wert.
    # In dem Fall korrigieren wir mathematisch: WERT_korr = FY_Total - Sigma(known Q).
    correction_note = ""
    if (
        key in SUMMABLE_QUARTERLY_KEYS
        and q_actuals_for_prompt
        and content
    ):
        try:
            from app.llm.claude import extract_kontext_fy_total
            fy_total_hint = extract_kontext_fy_total(content)
            if fy_total_hint is not None and abs(fy_total_hint) > 0:
                rel_diff = abs(abs(v) - abs(fy_total_hint)) / abs(fy_total_hint)
                if rel_diff <= Decimal("0.02"):
                    known_sum = sum(
                        (abs(qv) for qv in q_actuals_for_prompt.values() if qv is not None),
                        Decimal("0"),
                    )
                    corrected = fy_total_hint - known_sum
                    if corrected > 0:
                        logger.warning(
                            "Q-Estimate Auto-Correct %s/%s/%s/FY%s: WERT %.0f == FY-Total %.0f "
                            "-> korrigiert auf %.0f (FY-Total - Sigma Q-Actuals %.0f)",
                            company.ticker, key, quarter, period_year,
                            float(v), float(fy_total_hint), float(corrected), float(known_sum),
                        )
                        correction_note = (
                            f" [AUTO-KORRIGIERT: Claude lieferte initial FY-Total "
                            f"{float(fy_total_hint):,.0f}, korrigiert zu Standalone-{quarter} "
                            f"= FY-Total - Sigma(Q-Actuals)]"
                        )
                        v = corrected
                    else:
                        logger.warning(
                            "Q-Estimate Auto-Correct skipped %s/%s/%s/FY%s: FY-Total - Sigma "
                            "negativ (%.0f)", company.ticker, key, quarter, period_year,
                            float(corrected),
                        )
        except Exception as exc:
            logger.debug("Q-Estimate kontext-fy-total parse skipped: %s", exc)

    # YTD-Auto-Detection: Claude ignoriert teilweise die YTD-Warning im Prompt
    # (haeufig bei SBC / FCF / Dividends) und liefert den kumulativen Betrag aus
    # dem 10-Q (Six/Nine Months Ended). Zwei Trigger:
    #   Trigger 1 (Text): content enthaelt YTD-Marker + v >= 1.35 x prior_sum
    #   Trigger 2 (Value): v >= 1.7 x prior_sum UND standalone_impl/prior_avg
    #     im engen Range [0.6, 1.6] (mathematisch sehr klarer YTD-Fall auch
    #     ohne Text-Marker im content).
    if (
        key in SUMMABLE_QUARTERLY_KEYS
        and q_actuals_for_prompt
        and quarter in ("Q2", "Q3", "Q4")
    ):
        content_lower = (content or "").lower()
        ytd_markers = (
            "six months ended", "nine months ended",
            "three months ended and", "six-month period", "nine-month period",
            "first six months", "first nine months",
            "6 months ended", "9 months ended",
            "for the six months", "for the nine months",
            "half-year", "half year", "first half",
            "year to date", "year-to-date", " ytd",
            "cumulative", "cumulative for the",
            "kumulativ", "sechs monate", "neun monate",
        )
        matched = [m for m in ytd_markers if m in content_lower]

        prior_quarters = {"Q2": ("Q1",), "Q3": ("Q1", "Q2"), "Q4": ("Q1", "Q2", "Q3")}[quarter]
        all_prior_known = all(q in q_actuals_for_prompt for q in prior_quarters)
        if all_prior_known:
            prior_sum = sum(
                (q_actuals_for_prompt[q] for q in prior_quarters),
                Decimal("0"),
            )
            if prior_sum != 0 and v != 0 and (v > 0) == (prior_sum > 0):
                ratio_to_prior = abs(v) / abs(prior_sum)
                standalone_impl = v - prior_sum
                is_same_sign = standalone_impl != 0 and (standalone_impl > 0) == (v > 0)
                prior_avg = abs(prior_sum) / Decimal(len(prior_quarters))
                ratio_to_avg = (
                    abs(standalone_impl) / prior_avg if (is_same_sign and prior_avg > 0) else Decimal("0")
                )

                # Trigger 1: Text-Marker + moderater Value-Ratio
                trigger_text = matched and ratio_to_prior >= Decimal("1.35")
                # Trigger 2: sehr klarer Value-Fall auch ohne Text-Marker.
                # ratio_to_prior >= 1.7 (echt YTD ist typisch ~2.0x fuer Q2,
                # ~1.5x fuer Q3) UND standalone im engen Plausi-Range um
                # prior_avg — sonst False-Positive Gefahr (z.B. Wachstums-Q).
                trigger_value = (
                    ratio_to_prior >= Decimal("1.7")
                    and Decimal("0.6") <= ratio_to_avg <= Decimal("1.6")
                )

                if (trigger_text or trigger_value) and is_same_sign:
                    if Decimal("0.3") <= ratio_to_avg <= Decimal("3.0"):
                        reason = "TEXT+" if trigger_text else ""
                        reason += "VALUE" if trigger_value else ""
                        marker_txt = ",".join(matched)[:60] if matched else "(no text marker)"
                        logger.warning(
                            "Q-Estimate YTD-Auto-Detect %s/%s/%s/FY%s [%s]: v=%.0f "
                            "-> Standalone=%.0f (prior_sum=%.0f, ratio_to_prior=%.2f, "
                            "ratio_to_avg=%.2f, marker=%s)",
                            company.ticker, key, quarter, period_year, reason,
                            float(v), float(standalone_impl), float(prior_sum),
                            float(ratio_to_prior), float(ratio_to_avg), marker_txt,
                        )
                        correction_note += (
                            f" [YTD-KORRIGIERT via {reason}: v_orig={float(v):,.0f}, "
                            f"standalone={float(standalone_impl):,.0f}]"
                        )
                        v = standalone_impl

    adj_val: Decimal | None = None
    adj_src: str | None = None
    adj_note: str | None = None
    if content and key in {
        "net_income", "ebitda", "fcf",
        "revenue", "eps_diluted", "operating_cash_flow", "capex",
    }:
        adj_val, adj_src, adj_note = extract_research_value_adjusted(content)

    raw_src = (s or "KI-Einschätzung")[:3800]
    src_name = (
        f"Claude-Q-Estimate ({quarter} FY{period_year}): {float(v):,.0f} "
        f"{company.currency}{correction_note} | {raw_src}"
    )[:3900]
    return v, src_name, u, adj_val, adj_note, adj_src


# Keys die der EDGAR-Q-Fallback unterstuetzt (Income-Statement + Cashflow
# Standalone-Q). Balance-Sheet-Keys (net_debt, cash_and_equivalents, st_debt,
# lt_debt, st_investments) fehlen bewusst: die sind instant-facts und werden
# separat als Q4-Snapshot behandelt.
EDGAR_QUARTERLY_SUPPORTED = frozenset({
    # Original 6 (Q-Standalone via period-length filter)
    "net_income", "ebitda", "fcf", "sbc", "buyback_volume", "dividends",
    # Detail-Page additions
    "revenue", "eps_diluted", "operating_cash_flow", "capex",
})

# Provider-Singleton damit ticker->CIK Map und Facts-Cache reuse zwischen Calls.
_edgar_provider_singleton = None


def _get_edgar_provider():
    global _edgar_provider_singleton
    if _edgar_provider_singleton is None:
        from app.providers.edgar import EdgarProvider
        _edgar_provider_singleton = EdgarProvider()
    return _edgar_provider_singleton


def _upsert_q_actual_from_provider(
    db: Session,
    company_id: UUID,
    key: str,
    period_year: int,
    quarter: str,
    value: Decimal,
    source_name: str,
    source_link: str | None,
    currency: str | None,
) -> CompanyValue | None:
    """Persistiert einen Q-Actual vom Provider (EDGAR). Setzt is_forecast=False.
    Schreibt nie ueber existierende Actuals (auch nicht aus PDF), Manual-Overrides
    oder PDF-Guidance. Forecast-Rows werden ueberschrieben."""
    existing_actual = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == quarter,
            CompanyValue.period_year == period_year,
            CompanyValue.is_forecast.is_(False),
            CompanyValue.numeric_value.isnot(None),
        )
        .first()
    )
    # "calculated" (implied Q4) darf ueberschrieben werden — nach einem
    # Q1-Q3-Refetch muss der Sigma-Rest neu berechnet werden. Echte Actuals
    # (provider/pdf/manual) bleiben unantastbar.
    is_implied_q4_incoming = source_name.startswith("Implied Q4")
    if existing_actual is not None:
        can_overwrite = (
            is_implied_q4_incoming
            and existing_actual.primary_method == "calculated"
            and not getattr(existing_actual, "manually_overridden", False)
        )
        if not can_overwrite:
            return existing_actual
        now = datetime.now(timezone.utc)
        value_updated = abs(value) if key == "capex" and value is not None else value
        existing_actual.numeric_value = value_updated
        existing_actual.source_name = source_name[:4000]
        existing_actual.source_link = source_link
        existing_actual.currency = currency
        existing_actual.fetched_at = now
        existing_actual.primary_method = "calculated"
        logger.info("Q4-Implied recomputed %s/%s/%s/FY%s = %.0f",
                    company_id, key, quarter, period_year, float(value_updated))
        return existing_actual
    existing_forecast = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == quarter,
            CompanyValue.period_year == period_year,
            CompanyValue.is_forecast.is_(True),
        )
        .one_or_none()
    )
    if existing_forecast and existing_forecast.manually_overridden:
        return existing_forecast
    if existing_forecast and existing_forecast.from_ir_pdf and existing_forecast.numeric_value is not None:
        return existing_forecast
    # CapEx Sign-Normalisierung: EDGAR liefert PaymentsToAcquirePropertyPlantAndEquipment
    # als positiven Betrag (Cash-Outflow), 10-Q Filings zeigen es aber in Klammern.
    # Wir speichern IMMER als positiven Absolutwert damit Frontend und
    # Downstream-Berechnungen (fcf = ocf - capex) konsistent sind.
    if key == "capex" and value is not None:
        value = abs(value)
    now = datetime.now(timezone.utc)
    # Q4-Implied vs echter Provider-Fetch: erkennen wir am source_name.
    is_implied_q4 = source_name.startswith("Implied Q4")
    method = "calculated" if is_implied_q4 else "provider"
    try:
        with db.begin_nested():
            if existing_forecast:
                existing_forecast.numeric_value = value
                existing_forecast.source_name = source_name[:4000]
                existing_forecast.source_link = source_link
                existing_forecast.currency = currency
                existing_forecast.fetched_at = now
                existing_forecast.is_forecast = False
                existing_forecast.from_ir_pdf = False
                existing_forecast.primary_method = method
                return existing_forecast
            cv = CompanyValue(
                id=uuid4(),
                company_id=company_id,
                value_key=key,
                period_type=quarter,
                period_year=period_year,
                numeric_value=value,
                source_name=source_name[:4000],
                source_link=source_link,
                currency=currency,
                fetched_at=now,
                is_forecast=False,
                primary_method=method,
            )
            db.add(cv)
            db.flush()
            return cv
    except IntegrityError as ie:
        logger.warning("EDGAR Q-Actual upsert %s/%s/%s/%s IntegrityError: %s",
                       company_id, key, quarter, period_year, str(ie)[:120])
        return None


def _try_edgar_q_actuals(
    db: Session,
    company: "Company",
    key: str,
    target_fy: int,
    q_values: dict[str, Decimal],
    q_sources: dict[str, str],
    q_origin: dict[str, str],
    currency: str | None,
) -> None:
    """EDGAR-Q-Fallback fuer US-Filer: Standalone-Q-Actuals aus 10-Q-XBRL.
    Mutiert q_values/q_sources/q_origin direkt, persistiert via _upsert_q_actual_from_provider."""
    from app.calculations.lock import is_us_company
    if not is_us_company(company):
        return
    if key not in EDGAR_QUARTERLY_SUPPORTED:
        return
    fy_end_month = getattr(company, "fiscal_year_end_month", None)
    fy_end_day = getattr(company, "fiscal_year_end_day", None)
    if not fy_end_month or not fy_end_day:
        return
    provider = _get_edgar_provider()
    for q in ("Q1", "Q2", "Q3"):
        if q in q_values:
            continue
        try:
            res = provider.fetch_quarterly(
                company.ticker, key, target_fy, q,
                fy_end_month=fy_end_month, fy_end_day=fy_end_day,
            )
        except Exception as exc:
            logger.warning("EDGAR Q-Fetch %s/%s/%s/FY%s exception: %s",
                           company.ticker, key, q, target_fy, exc)
            continue
        if res is None or res.value is None:
            continue
        v = Decimal(str(res.value))
        # Sign normalisation: buyback/dividend werden positiv gespeichert (Outflow).
        if key in {"buyback_volume", "dividends"} and v < 0:
            v = abs(v)
        q_values[q] = v
        q_sources[q] = f"{q}: actual lt. {res.source_name[:70]}"
        q_origin[q] = "actual"
        _upsert_q_actual_from_provider(
            db, company.id, key, target_fy, q,
            value=v,
            source_name=res.source_name,
            source_link=res.source_link,
            currency=currency,
        )
        logger.info("EDGAR Q-Actual %s/%s/%s/FY%s = %.0f via XBRL",
                    company.ticker, key, q, target_fy, float(v))


def _ensure_prev_fy_q_actuals(
    db: Session,
    company: "Company",
    key: str,
    prev_fy: int,
) -> None:
    """Stellt sicher dass FY-1 Q-Actuals (Q1-Q4) in DB sind, fuer Saisonalitaets-
    Anker in Per-Q-Claude-Calls. Bei US-Filern: EDGAR-XBRL fuer Q1-Q3, Q4
    implizit aus FY-Total minus Sigma(Q1-Q3). Bei non-US: skip — Saisonalitaets-
    Anker greift erst wenn User Vorjahres-Q-Daten via Manual oder PDF eingeflegt
    hat (oder ESEF-Q kommt in Zukunft).

    No-op wenn alle 4 Q schon da sind oder kein FY-Total fuer prev_fy."""
    if key not in SUMMABLE_QUARTERLY_KEYS:
        return
    from app.calculations.lock import is_us_company
    if not is_us_company(company):
        return
    if key not in EDGAR_QUARTERLY_SUPPORTED:
        return

    # Bestand pruefen
    existing = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type.in_(("Q1", "Q2", "Q3", "Q4")),
            CompanyValue.period_year == prev_fy,
            CompanyValue.is_forecast.is_(False),
            CompanyValue.numeric_value.isnot(None),
        )
        .all()
    )
    existing_qs = {r.period_type: r.numeric_value for r in existing}
    # Merken welche existierenden Q4 als "calculated" (implied) markiert sind —
    # die muessen wir nach Q1-Q3-Refetch potentiell neu berechnen. Wenn Q4
    # provider/pdf/manual: skip (echte Actual).
    q4_existing_calculated = any(
        r.period_type == "Q4" and r.primary_method == "calculated"
        for r in existing
    )
    if len(existing_qs) >= 4 and not q4_existing_calculated:
        return  # Alle 4 Q sind Real-Actuals -> nichts zu tun

    currency = company.currency if key in CURRENCY_KEYS else None
    fy_end_month = getattr(company, "fiscal_year_end_month", None)
    fy_end_day = getattr(company, "fiscal_year_end_day", None)
    if not fy_end_month or not fy_end_day:
        return

    # Q1-Q3 via EDGAR-Backfill, mit Claude-Fallback wenn EDGAR nichts liefert
    # (typisch bei OCF/CapEx die als YTD-cumulative statt Q-standalone
    # gefiled werden, oder bei EPS wo Alphabet ein anderes XBRL-Concept
    # nutzt als unser CONCEPT_MAP).
    provider = _get_edgar_provider()
    new_q_values: dict[str, Decimal] = dict(existing_qs)
    for q in ("Q1", "Q2", "Q3"):
        if q in new_q_values:
            continue
        v: Decimal | None = None
        src_name: str | None = None
        src_link: str | None = None
        primary: str = "provider"
        try:
            res = provider.fetch_quarterly(
                company.ticker, key, prev_fy, q,
                fy_end_month=fy_end_month, fy_end_day=fy_end_day,
            )
        except Exception as exc:
            logger.warning("EDGAR FY-1 backfill %s/%s/%s/FY%s exception: %s",
                           company.ticker, key, q, prev_fy, exc)
            res = None
        if res is not None and res.value is not None:
            v = Decimal(str(res.value))
            src_name = f"{res.source_name} (FY-1 Backfill fuer Saisonalitaets-Anker)"
            src_link = res.source_link
        else:
            # Claude-Fallback fuer Vorjahres-Q: is_forecast=False, weil das
            # historische Actuals sind die Claude aus dem 10-Q/8-K herausliest.
            v_c, src_c, url_c = _claude_fetch_historical_q(company, key, prev_fy, q)
            if v_c is not None:
                v = v_c
                src_name = f"Claude Historical Q: {src_c} (FY-1 Backfill)" if src_c else "Claude Historical Q (FY-1 Backfill)"
                src_link = url_c
                primary = "web_guidance"
        if v is None:
            continue
        if key in {"buyback_volume", "dividends"} and v < 0:
            v = abs(v)
        _upsert_q_actual_from_provider(
            db, company.id, key, prev_fy, q,
            value=v,
            source_name=src_name or "FY-1 Backfill",
            source_link=src_link,
            currency=currency,
        )
        # Override primary_method for Claude-sourced actuals.
        if primary == "web_guidance":
            row = (
                db.query(CompanyValue)
                .filter(
                    CompanyValue.company_id == company.id,
                    CompanyValue.value_key == key,
                    CompanyValue.period_type == q,
                    CompanyValue.period_year == prev_fy,
                )
                .one_or_none()
            )
            if row:
                row.primary_method = "web_guidance"
        new_q_values[q] = v
        logger.info("FY-1 backfill %s/%s/%s/FY%s = %.0f (%s)",
                    company.ticker, key, q, prev_fy, float(v), primary)

    # Q4 implizit aus FY-Total minus Sigma(Q1-Q3) — falls FY-Total + alle 3 Q da.
    # Wenn Q4 bereits als "calculated" (implied) existiert, muss er nach Q1-Q3-
    # Refetch neu berechnet werden — sonst bleibt der Sigma-Rest inkonsistent.
    if not all(q in new_q_values for q in ("Q1", "Q2", "Q3")):
        return
    if "Q4" in new_q_values and not q4_existing_calculated:
        return  # Q4 ist echter Actual (provider/pdf/manual) -> nie ueberschreiben
    fy_total_row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == prev_fy,
            CompanyValue.is_forecast.is_(False),
            CompanyValue.numeric_value.isnot(None),
        )
        .order_by(CompanyValue.fetched_at.desc())
        .first()
    )
    # Falls kein FY-Total in DB: aus EDGAR 10-K holen. Notwendig fuer die
    # neuen Keys (revenue, ocf, capex, eps_diluted) die noch nie ein FY hatten.
    if fy_total_row is None or fy_total_row.numeric_value is None:
        try:
            fy_res = provider.fetch(
                company.ticker, key,
                period_type="FY", period_year=prev_fy,
                fy_end_month=fy_end_month, fy_end_day=fy_end_day,
            )
        except Exception as exc:
            logger.warning("EDGAR FY-1 fetch %s/%s/FY%s exception: %s",
                           company.ticker, key, prev_fy, exc)
            fy_res = None
        fy_val: Decimal | None = None
        fy_src_name: str | None = None
        fy_src_link: str | None = None
        fy_method: str = "provider"
        if fy_res is not None and fy_res.value is not None:
            fy_val = Decimal(str(fy_res.value))
            fy_src_name = fy_res.source_name or "SEC EDGAR (FY-1 Backfill)"
            fy_src_link = fy_res.source_link
        else:
            v_c, s_c, u_c = _claude_fetch_historical_fy(company, key, prev_fy)
            if v_c is not None:
                fy_val = v_c
                fy_src_name = f"Claude Historical FY: {s_c}" if s_c else "Claude Historical FY (FY-1 Backfill)"
                fy_src_link = u_c
                fy_method = "web_guidance"
        if fy_val is None:
            return
        if key in {"buyback_volume", "dividends"} and fy_val < 0:
            fy_val = abs(fy_val)
        now = datetime.now(timezone.utc)
        new_fy = CompanyValue(
            id=uuid4(),
            company_id=company.id,
            value_key=key,
            period_type="FY",
            period_year=prev_fy,
            numeric_value=fy_val,
            source_name=(fy_src_name or "FY-1 Backfill")[:4000],
            source_link=fy_src_link,
            currency=currency,
            fetched_at=now,
            is_forecast=False,
            primary_method=fy_method,
        )
        db.add(new_fy)
        try:
            db.flush()
        except IntegrityError as ie:
            logger.warning("FY-1 FY-Row upsert %s/%s/FY%s IntegrityError: %s",
                           company.ticker, key, prev_fy, str(ie)[:120])
            db.rollback()
            return
        logger.info("FY-1 FY-Row backfill %s/%s/FY%s = %.0f (%s)",
                    company.ticker, key, prev_fy, float(fy_val), fy_method)
        fy_total = fy_val
    else:
        fy_total = fy_total_row.numeric_value
    q123_sum = sum(new_q_values[q] for q in ("Q1", "Q2", "Q3"))
    q4_implied = fy_total - q123_sum
    # Plausibility: Q4 sollte gleiches Vorzeichen wie FY-Total haben
    # und vom Betrag her im Range der anderen Q liegen.
    if (fy_total > 0 and q4_implied <= 0) or (fy_total < 0 and q4_implied >= 0):
        logger.warning("FY-1 Q4-Implied implausibel %s/%s/FY%s: fy=%s q123=%s q4_impl=%s",
                       company.ticker, key, prev_fy, fy_total, q123_sum, q4_implied)
        return
    _upsert_q_actual_from_provider(
        db, company.id, key, prev_fy, "Q4",
        value=q4_implied,
        source_name=f"Implied Q4 = FY{prev_fy}-Total ({float(fy_total):,.0f}) minus Sigma(Q1-Q3)",
        source_link=None,
        currency=currency,
    )
    logger.info("FY-1 Q4-implied backfill %s/%s/FY%s = %.0f",
                company.ticker, key, prev_fy, float(q4_implied))


BALANCE_SHEET_FY_KEYS = frozenset({
    "cash_and_equivalents", "st_investments", "st_debt", "lt_debt", "net_debt",
})


ADJUSTED_RELEVANT_KEYS = frozenset({
    "net_income", "ebitda", "fcf",
    "revenue", "eps_diluted", "operating_cash_flow", "capex",
})


def _ensure_q_adjusted(
    db: Session,
    company: "Company",
    key: str,
    year: int,
    quarter: str,
) -> None:
    """Fills numeric_value_adjusted on an existing Q row when EDGAR gave us
    GAAP-only. Runs a focused Claude call that ONLY researches the Non-GAAP
    version. No-op if adjusted is already set, or if key isn't
    adjusted-relevant, or if no Q row exists.
    """
    if key not in ADJUSTED_RELEVANT_KEYS:
        return
    row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == quarter,
            CompanyValue.period_year == year,
        )
        .one_or_none()
    )
    if row is None or row.numeric_value_adjusted is not None:
        return
    if row.numeric_value is None:
        return
    # Dedizierter Adj-only Prompt (Fix E): kompakter Prompt der explizit nur
    # nach der Non-GAAP-Variante fragt. Der frueher genutzte research_value
    # mit q_actuals={quarter: gaap} hat Claude oft verwirrt (GAAP-Wert war
    # bereits injiziert -> Claude interpretierte "nichts mehr zu suchen").
    from app.llm.claude import research_adjusted_only
    from app.values.models import ValueDefinition

    vd = db.query(ValueDefinition).filter(ValueDefinition.key == key).one_or_none()
    if vd is None:
        return
    label = f"{vd.label_en} ({vd.label_de})"
    # Sibling-Anker: bereits gefundene Adj-Werte anderer Q dieser Firma+Metrik.
    # Erhoeht Trefferquote bei Q4 Forecast oder historischen Q ohne dediziertes
    # 8-K weil Claude die typische Adjustment-Groesse sieht.
    sibling_rows = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type.in_(("Q1", "Q2", "Q3", "Q4")),
            CompanyValue.period_year.in_((year, year - 1)),
            CompanyValue.numeric_value_adjusted.isnot(None),
        )
        .order_by(CompanyValue.period_year.desc(), CompanyValue.period_type)
        .all()
    )
    sibling_adj = {
        f"{r.period_type} FY{r.period_year}": r.numeric_value_adjusted
        for r in sibling_rows
        if not (r.period_type == quarter and r.period_year == year)
    }
    try:
        adj_val, adj_note, adj_src, err = research_adjusted_only(
            company.name, company.ticker, label, key,
            company.currency or "USD",
            period_type=quarter, period_year=year,
            gaap_value=row.numeric_value,
            sibling_adj=sibling_adj or None,
        )
    except Exception as exc:
        logger.warning("Adj-only fetch %s/%s/%s/FY%s failed: %s",
                       company.ticker, key, quarter, year, exc)
        return
    if err:
        logger.info("Adj-only skip %s/%s/%s/FY%s: %s",
                    company.ticker, key, quarter, year, err)
        return
    if adj_val is None:
        return
    row.numeric_value_adjusted = adj_val
    if adj_note:
        row.adjustments_note = adj_note[:4000]
    if adj_src:
        row.adjustments_source = adj_src[:2048]
    logger.info("Adj-only backfill %s/%s/%s/FY%s = %.3f (bridge=%s)",
                company.ticker, key, quarter, year, float(adj_val),
                (adj_note or "")[:80])


def _claude_fetch_historical_q(
    company: "Company",
    key: str,
    year: int,
    quarter: str,
) -> tuple[Decimal | None, str | None, str | None]:
    """Ruft Claude fuer einen VERGANGENEN Standalone-Q-Wert auf. Verwendet
    wenn EDGAR-Q-Fetch nichts liefert (typisch bei OCF/CapEx die als YTD
    statt Standalone gefiled sind, oder EPS mit anderem XBRL-Concept).
    is_forward=False -> Claude soll den Wert aus dem 10-Q/8-K rausziehen,
    nicht schaetzen. Returns (value, source_name, source_link).

    YTD-Auto-Detect (Fix B): Wenn Claude YTD statt Standalone liefert (haeufig
    fuer SBC/FCF/Dividends wo 10-Q Text 'Six Months Ended' formuliert), wird
    v mathematisch korrigiert via v - Sigma(vorherige Q aus DB).
    """
    from app.llm.claude import research_value
    from app.values.models import ValueDefinition
    from app.db import SessionLocal
    _db = SessionLocal()
    try:
        vd = _db.query(ValueDefinition).filter(ValueDefinition.key == key).one_or_none()
        if vd is None:
            return None, None, None
        label = f"{vd.label_en} ({vd.label_de})"
        # Vorherige Q-Actuals fuer YTD-Detect + q_actuals-Injection in den Prompt
        prior_quarters = {"Q1": (), "Q2": ("Q1",), "Q3": ("Q1", "Q2"), "Q4": ("Q1", "Q2", "Q3")}[quarter]
        prior_actuals: dict[str, Decimal] = {}
        if prior_quarters:
            rows = (
                _db.query(CompanyValue)
                .filter(
                    CompanyValue.company_id == company.id,
                    CompanyValue.value_key == key,
                    CompanyValue.period_type.in_(prior_quarters),
                    CompanyValue.period_year == year,
                    CompanyValue.is_forecast.is_(False),
                    CompanyValue.numeric_value.isnot(None),
                )
                .all()
            )
            prior_actuals = {r.period_type: r.numeric_value for r in rows}
    finally:
        _db.close()
    try:
        v, s, u, _p, content = research_value(
            company.name, company.ticker, label, company.currency,
            period_type=quarter, period_year=year, value_key=key,
            q_actuals=prior_actuals or None,
        )
    except Exception as exc:
        logger.warning("Claude historical Q %s/%s/%s/FY%s exception: %s",
                       company.ticker, key, quarter, year, exc)
        return None, None, None
    if v is None:
        return None, s, u
    # YTD-Auto-Detect (Text + Value-Fallback)
    if (
        key in SUMMABLE_QUARTERLY_KEYS
        and prior_actuals
        and len(prior_actuals) == len(prior_quarters)
    ):
        content_lower = (content or "").lower()
        ytd_markers = (
            "six months ended", "nine months ended",
            "three months ended and", "six-month period", "nine-month period",
            "first six months", "first nine months",
            "6 months ended", "9 months ended",
            "for the six months", "for the nine months",
            "half-year", "half year", "first half",
            "year to date", "year-to-date", " ytd",
            "cumulative", "cumulative for the",
            "kumulativ", "sechs monate", "neun monate",
        )
        matched = [m for m in ytd_markers if m in content_lower]
        prior_sum = sum(prior_actuals.values(), Decimal("0"))
        if prior_sum != 0 and v != 0 and (v > 0) == (prior_sum > 0):
            ratio_to_prior = abs(v) / abs(prior_sum)
            standalone_impl = v - prior_sum
            is_same_sign = standalone_impl != 0 and (standalone_impl > 0) == (v > 0)
            prior_avg = abs(prior_sum) / Decimal(len(prior_quarters))
            ratio_to_avg = (
                abs(standalone_impl) / prior_avg if (is_same_sign and prior_avg > 0) else Decimal("0")
            )
            trigger_text = matched and ratio_to_prior >= Decimal("1.35")
            trigger_value = (
                ratio_to_prior >= Decimal("1.7")
                and Decimal("0.6") <= ratio_to_avg <= Decimal("1.6")
            )
            if (trigger_text or trigger_value) and is_same_sign and Decimal("0.3") <= ratio_to_avg <= Decimal("3.0"):
                reason = ("TEXT+" if trigger_text else "") + ("VALUE" if trigger_value else "")
                logger.warning(
                    "Historical-Q YTD-Auto-Detect %s/%s/%s/FY%s [%s]: v=%.0f "
                    "-> Standalone=%.0f (prior_sum=%.0f, ratio_to_prior=%.2f)",
                    company.ticker, key, quarter, year, reason,
                    float(v), float(standalone_impl), float(prior_sum),
                    float(ratio_to_prior),
                )
                s = f"{s or ''} [YTD-KORRIGIERT via {reason}: v_orig={float(v):,.0f} -> standalone={float(standalone_impl):,.0f}]"
                v = standalone_impl
    return v, s, u


def _claude_fetch_historical_fy(
    company: "Company",
    key: str,
    year: int,
) -> tuple[Decimal | None, str | None, str | None]:
    """Ruft Claude fuer einen VERGANGENEN FY-Wert auf (fuer FY-Backfill wenn
    EDGAR nichts liefert). Returns (value, source_name, source_link)."""
    from app.llm.claude import research_value
    from app.values.models import ValueDefinition
    from app.db import SessionLocal
    _db = SessionLocal()
    try:
        vd = _db.query(ValueDefinition).filter(ValueDefinition.key == key).one_or_none()
        if vd is None:
            return None, None, None
        label = f"{vd.label_en} ({vd.label_de})"
    finally:
        _db.close()
    try:
        v, s, u, _p, _c = research_value(
            company.name, company.ticker, label, company.currency,
            period_type="FY", period_year=year, value_key=key,
        )
    except Exception as exc:
        logger.warning("Claude historical FY %s/%s/FY%s exception: %s",
                       company.ticker, key, year, exc)
        return None, None, None
    return v, s, u


def _ensure_prev_fy_balance_sheet(
    db: Session,
    company: "Company",
    prev_fy: int,
) -> None:
    """Backfill Balance-Sheet-Snapshot fuer prev_fy aus EDGAR-10-K.

    Balance-Sheet-Keys (cash_and_equivalents, st_investments, st_debt, lt_debt,
    net_debt) sind instant-facts und werden nicht per Q gefetched. Ohne dieses
    Backfill fehlt jeder historische Balance-Sheet-Snapshot (der User sieht
    Bilanz-Zeile 2025 komplett leer). Idempotent.

    No-op wenn:
      - Kompanie nicht US-Filer
      - Alle 5 Keys schon fuer prev_fy in DB
      - Kein Fiscal-Year-End gesetzt
    """
    from app.calculations.lock import is_us_company
    if not is_us_company(company):
        return
    fy_end_month = getattr(company, "fiscal_year_end_month", None)
    fy_end_day = getattr(company, "fiscal_year_end_day", None)
    if not fy_end_month or not fy_end_day:
        return

    existing_rows = {
        r.value_key: r
        for r in db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company.id,
            CompanyValue.value_key.in_(BALANCE_SHEET_FY_KEYS),
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == prev_fy,
            CompanyValue.is_forecast.is_(False),
            CompanyValue.numeric_value.isnot(None),
        )
        .all()
    }
    # Neue Keys die noch gar nicht existieren
    missing = BALANCE_SHEET_FY_KEYS - set(existing_rows.keys())
    # Fix C: Existing web_guidance-Rows opportunistisch versuchen aus EDGAR
    # nachzuziehen (z.B. wenn Concept-Map erweitert wurde). Manual-Overrides
    # oder provider-Rows werden nie ueberschrieben.
    stale = {
        k for k, r in existing_rows.items()
        if r.primary_method == "web_guidance" and not getattr(r, "manually_overridden", False)
    }
    if not missing and not stale:
        return

    provider = _get_edgar_provider()
    now = datetime.now(timezone.utc)
    for key in missing | stale:
        currency = company.currency if key in CURRENCY_KEYS else None
        v: Decimal | None = None
        src_name: str | None = None
        src_link: str | None = None
        method = "provider"
        try:
            res = provider.fetch(
                company.ticker, key,
                period_type="FY", period_year=prev_fy,
                fy_end_month=fy_end_month, fy_end_day=fy_end_day,
            )
        except Exception as exc:
            logger.warning("EDGAR FY-1 BS-fetch %s/%s/FY%s exception: %s",
                           company.ticker, key, prev_fy, exc)
            res = None
        if res is not None and res.value is not None:
            v = Decimal(str(res.value))
            src_name = res.source_name or "SEC EDGAR (FY-1 BS Backfill)"
            src_link = res.source_link
        elif key in missing:
            # Claude-Fallback nur fuer wirklich fehlende Keys — nicht fuer stale
            # web_guidance-Rows (die haben schon einen Claude-Wert).
            v_c, s_c, u_c = _claude_fetch_historical_fy(company, key, prev_fy)
            if v_c is not None:
                v = v_c
                src_name = f"Claude Historical BS: {s_c}" if s_c else "Claude Historical BS (FY-1)"
                src_link = u_c
                method = "web_guidance"
        if v is None:
            continue
        # Stale-Row Upgrade: existing web_guidance -> provider durch update
        if key in stale:
            row = existing_rows[key]
            row.numeric_value = v
            row.source_name = (src_name or "SEC EDGAR (BS-Refetch)")[:4000]
            row.source_link = src_link
            row.currency = currency
            row.fetched_at = now
            row.primary_method = method
            logger.info("FY-1 BS refetch %s/%s/FY%s = %.0f (web_guidance -> %s)",
                        company.ticker, key, prev_fy, float(v), method)
            continue
        cv = CompanyValue(
            id=uuid4(),
            company_id=company.id,
            value_key=key,
            period_type="FY",
            period_year=prev_fy,
            numeric_value=v,
            source_name=src_name[:4000] if src_name else "FY-1 BS Backfill",
            source_link=src_link,
            currency=currency,
            fetched_at=now,
            is_forecast=False,
            primary_method=method,
        )
        db.add(cv)
        try:
            db.flush()
            logger.info("FY-1 BS backfill %s/%s/FY%s = %.0f (%s)",
                        company.ticker, key, prev_fy, float(v), method)
        except IntegrityError as ie:
            logger.warning("FY-1 BS upsert %s/%s/FY%s IntegrityError: %s",
                           company.ticker, key, prev_fy, str(ie)[:120])
            db.rollback()


def estimate_fy_via_quarterly_sum(
    db: Session,
    company: "Company",
    key: str,
    target_fy: int,
    prev_fy_val: Decimal | None,
) -> tuple[Decimal | None, str, str | None, Decimal | None, str | None, str | None] | None:
    """Orchestriert pro-Q-Logik fuer ein FY-Estimate.

    Pro Quartal:
      - PDF-Actual oder Manual-Override aus DB -> uebernehmen
      - Sonst Claude-Call fuer dieses Q
    Persistiert jede Q-Row einzeln (Actual oder Forecast).
    Aggregiert FY-Wert:
      - SUMMABLE_QUARTERLY_KEYS: Sigma alle 4 Q
      - POINT_IN_TIME_QUARTERLY_KEYS (net_debt): Q4-Endstand

    Returns (fy_value, fy_source_name, fy_source_link, fy_adj_value, fy_adj_note, fy_adj_source) oder None.
    """
    if key not in QUARTERLY_ESTIMATE_KEYS:
        return None

    # FY-1 Q-Datenbasis sicherstellen (Saisonalitaets-Anker fuer Per-Q-Claude-Calls).
    # No-op wenn nicht-US oder Q-Werte schon komplett da.
    _ensure_prev_fy_q_actuals(db, company, key, target_fy - 1)
    # FY-1 Balance-Sheet-Snapshot sicherstellen (aus EDGAR-10-K instant facts).
    # Balance-Sheet keys sind nicht in QUARTERLY_ESTIMATE_KEYS -> bekaemen sonst
    # nie einen historischen Backfill. Idempotent, no-op wenn schon da.
    _ensure_prev_fy_balance_sheet(db, company, target_fy - 1)
    # FY-1 Q-Actuals Non-GAAP-Backfill (Fix 2). Ohne diesen Call bleibt die
    # Adjusted-Spalte fuer alle historischen Actuals leer weil _ensure_q_adjusted
    # in dem Q-Loop unten nur fuer target_fy laeuft. Idempotent (No-op wenn
    # numeric_value_adjusted schon gesetzt).
    if key in ADJUSTED_RELEVANT_KEYS:
        for q in QUARTERS:
            _ensure_q_adjusted(db, company, key, target_fy - 1, q)

    company_id = company.id
    currency = company.currency if key in CURRENCY_KEYS else None

    q_values: dict[str, Decimal] = {}
    q_adj_values: dict[str, Decimal] = {}
    q_sources: dict[str, str] = {}
    q_origin: dict[str, str] = {}  # "actual"|"manual"|"estimate"
    first_url: str | None = None

    for q in QUARTERS:
        existing = _get_q_row(db, company_id, key, target_fy, q)
        if existing is not None and existing.numeric_value is not None:
            if existing.manually_overridden:
                q_values[q] = existing.numeric_value
                if existing.numeric_value_adjusted is not None:
                    q_adj_values[q] = existing.numeric_value_adjusted
                q_sources[q] = f"{q}: Manual-Override ({float(existing.numeric_value):,.0f})"
                q_origin[q] = "manual"
                continue
            if not existing.is_forecast:
                q_values[q] = existing.numeric_value
                if existing.numeric_value_adjusted is not None:
                    q_adj_values[q] = existing.numeric_value_adjusted
                src_short = (existing.source_name or "PDF")[:60]
                q_sources[q] = f"{q}: actual lt. {src_short}"
                q_origin[q] = "actual"
                continue

    # EDGAR-Q-Fallback (US-Filer): wenn PDF-Actual fehlt, versuche Standalone-Q
    # aus 10-Q-XBRL zu holen, bevor wir Claude raten lassen.
    # Q4-laufendes-FY ist nie im 10-Q -> bleibt Claude.
    _try_edgar_q_actuals(
        db, company, key, target_fy,
        q_values=q_values, q_sources=q_sources, q_origin=q_origin,
        currency=currency,
    )

    # Non-GAAP-Backfill fuer Q-Actuals aus EDGAR/PDF: EDGAR-XBRL liefert nur
    # GAAP-Werte, PDF-Extract oft auch nur GAAP. Ohne diesen Call bleibt die
    # Non-GAAP-Spalte im UI leer fuer alle bereits reporteten Quartale. Nur
    # aufgerufen fuer Keys mit Adjusted-Pendant (net_income, ebitda, fcf,
    # revenue, eps_diluted, operating_cash_flow, capex).
    if key in ADJUSTED_RELEVANT_KEYS:
        for q in QUARTERS:
            if q in q_values:
                _ensure_q_adjusted(db, company, key, target_fy, q)

    for q in QUARTERS:
        if q in q_values:
            continue
        v, src_full, url, adj_v, adj_note, adj_src = _estimate_single_quarter(
            db, company, key, target_fy, q,
            known_q_values=q_values,
            prev_fy_val=prev_fy_val,
        )
        if v is None:
            logger.warning("Q-Estimate %s/%s/%s/FY%s: Claude lieferte keinen Wert",
                           company.ticker, key, q, target_fy)
            continue
        q_values[q] = v
        if adj_v is not None:
            q_adj_values[q] = adj_v
        src_detail = (src_full or "").split(" | ", 1)
        detail_txt = src_detail[1].strip() if len(src_detail) > 1 else ""
        q_sources[q] = f"{q}: Claude-Estimate ({float(v):,.0f})" + (
            f" — Begruendung: {detail_txt[:700]}" if detail_txt else ""
        )
        q_origin[q] = "estimate"
        if first_url is None and url:
            first_url = url
        _upsert_q_estimate(
            db, company_id, key, target_fy, q,
            value=v, source_name=src_full or "Claude-Q-Estimate",
            source_link=url, currency=currency,
            value_adjusted=adj_v, adjustments_note=adj_note, adjustments_source=adj_src,
        )

    if not q_values:
        return None

    if key in SUMMABLE_QUARTERLY_KEYS:
        if len(q_values) < 4:
            logger.info("Q-Estimate FY-Sum %s/%s/FY%s: nur %d/4 Quartale verfuegbar — partial sum",
                        company.ticker, key, target_fy, len(q_values))
        fy_value = sum(q_values.values(), Decimal("0"))
        adj_complete = all(q in q_adj_values for q in q_values.keys())
        fy_adj_value = sum(q_adj_values.values(), Decimal("0")) if adj_complete and q_adj_values else None
        mode_label = "Summe Q1-Q4"
    elif key in POINT_IN_TIME_QUARTERLY_KEYS:
        q4_val = q_values.get("Q4")
        if q4_val is None:
            latest = next((q_values[q] for q in reversed(QUARTERS) if q in q_values), None)
            if latest is None:
                return None
            fy_value = latest
            mode_label = "letztes verfuegbares Q (Q4 nicht da)"
        else:
            fy_value = q4_val
            mode_label = "Q4-Endstand"
        fy_adj_value = q_adj_values.get("Q4")
    else:
        return None

    source_summary = " | ".join(q_sources.get(q, f"{q}: kein Wert") for q in QUARTERS)
    fy_source = (
        f"Per-Q-Aggregation ({mode_label}) FY{target_fy}: {float(fy_value):,.0f} {company.currency or ''} = "
        f"{source_summary}"
    )[:4000]

    adj_summary_parts = []
    for q in QUARTERS:
        if q in q_adj_values:
            adj_summary_parts.append(f"{q}: {float(q_adj_values[q]):,.0f}")
    fy_adj_note = ("Adjusted pro Q: " + " | ".join(adj_summary_parts))[:4000] if adj_summary_parts else None
    fy_adj_source = "Pro-Q-Aggregat aus Claude-Q-Estimates + PDF-Actuals" if q_adj_values else None

    return fy_value, fy_source, first_url, fy_adj_value, fy_adj_note, fy_adj_source
