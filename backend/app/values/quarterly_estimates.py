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

    try:
        v, s, u, _p, content = research_value(
            company.name, company.ticker, label, company.currency,
            period_type=quarter, period_year=period_year, value_key=key,
            prev_fy_val=prev_fy_val,
            q_actuals=q_actuals_for_prompt or None,
        )
    except Exception as e:
        logger.warning("Q-Estimate Claude-Call failed %s/%s/%s/FY%s: %s",
                       company.ticker, key, quarter, period_year, e)
        return None, None, None, None, None, None
    if v is None:
        return None, None, None, None, None, None

    adj_val: Decimal | None = None
    adj_src: str | None = None
    adj_note: str | None = None
    if content and key in {"net_income", "ebitda", "fcf"}:
        adj_val, adj_src, adj_note = extract_research_value_adjusted(content)

    raw_src = (s or "KI-Einschätzung")[:3800]
    src_name = f"Claude-Q-Estimate ({quarter} FY{period_year}): {float(v):,.0f} {company.currency} | {raw_src}"[:3900]
    return v, src_name, u, adj_val, adj_note, adj_src


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
