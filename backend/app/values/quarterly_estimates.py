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
QUARTERLY_ESTIMATE_KEYS = frozenset({
    "net_income", "ebitda", "fcf", "sbc",
    "buyback_volume", "dividends", "net_debt",
})

# Cumulative: FY = Sigma Q1+Q2+Q3+Q4 (Income/Cashflow-Werte)
SUMMABLE_QUARTERLY_KEYS = frozenset({
    "net_income", "ebitda", "fcf", "sbc",
    "buyback_volume", "dividends",
})

# Point-in-Time: FY = Q4-Endstand (Bilanz-Snapshot)
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

    adj_val: Decimal | None = None
    adj_src: str | None = None
    adj_note: str | None = None
    if content and key in {"net_income", "ebitda", "fcf"}:
        adj_val, adj_src, adj_note = extract_research_value_adjusted(content)

    raw_src = (s or "KI-Einschätzung")[:3800]
    src_name = (
        f"Claude-Q-Estimate ({quarter} FY{period_year}): {float(v):,.0f} "
        f"{company.currency}{correction_note} | {raw_src}"
    )[:3900]
    return v, src_name, u, adj_val, adj_note, adj_src


# Keys die der EDGAR-Q-Fallback unterstuetzt (Income-Statement-Standalone-Q).
# Net Debt fehlt bewusst: Q-Bilanz braucht 5-Komponenten-Aggregation, separate Iteration.
EDGAR_QUARTERLY_SUPPORTED = frozenset({
    "net_income", "ebitda", "fcf", "sbc", "buyback_volume", "dividends",
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
    if existing_actual is not None:
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
    now = datetime.now(timezone.utc)
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
    if len(existing_qs) >= 4:
        return  # Saisonalitaets-Datenbasis komplett

    currency = company.currency if key in CURRENCY_KEYS else None
    fy_end_month = getattr(company, "fiscal_year_end_month", None)
    fy_end_day = getattr(company, "fiscal_year_end_day", None)
    if not fy_end_month or not fy_end_day:
        return

    # Q1-Q3 via EDGAR-Backfill
    provider = _get_edgar_provider()
    new_q_values: dict[str, Decimal] = dict(existing_qs)
    for q in ("Q1", "Q2", "Q3"):
        if q in new_q_values:
            continue
        try:
            res = provider.fetch_quarterly(
                company.ticker, key, prev_fy, q,
                fy_end_month=fy_end_month, fy_end_day=fy_end_day,
            )
        except Exception as exc:
            logger.warning("EDGAR FY-1 backfill %s/%s/%s/FY%s exception: %s",
                           company.ticker, key, q, prev_fy, exc)
            continue
        if res is None or res.value is None:
            continue
        v = Decimal(str(res.value))
        if key in {"buyback_volume", "dividends"} and v < 0:
            v = abs(v)
        _upsert_q_actual_from_provider(
            db, company.id, key, prev_fy, q,
            value=v,
            source_name=f"{res.source_name} (FY-1 Backfill fuer Saisonalitaets-Anker)",
            source_link=res.source_link,
            currency=currency,
        )
        new_q_values[q] = v
        logger.info("EDGAR FY-1 backfill %s/%s/%s/FY%s = %.0f via XBRL",
                    company.ticker, key, q, prev_fy, float(v))

    # Q4 implizit aus FY-Total minus Sigma(Q1-Q3) — falls FY-Total + alle 3 Q da
    if "Q4" in new_q_values:
        return
    if not all(q in new_q_values for q in ("Q1", "Q2", "Q3")):
        return
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
    if fy_total_row is None or fy_total_row.numeric_value is None:
        return
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
