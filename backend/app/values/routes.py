import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.auth.deps import current_user
from app.auth.models import User
from app.calculations.engine import (
    CALCULATED_KEYS,
    FY_CALC_KEYS,
    HOHN_KEYS,
    STAMMDATEN_CALC_KEYS,
    calculate_cumulative,
    calculate_fy,
    calculate_stammdaten,
)
from app.calculations.lock import (
    annual_report_years,
    is_hohn_locked,
    is_us_company,
    quarter_years,
    quarter_years_in_progress,
)
from app.companies.models import Company
from app.db import get_db
from app.portfolios.models import Portfolio
from app.providers.registry import get_providers
from app.values.always_current import ALWAYS_CURRENT_KEYS
from app.values.currency_keys import CURRENCY_KEYS
from app.values.models import CompanyValue, SourceType, ValueDefinition
from app.values.progress import cleanup_old_jobs, finish_job, get_job, mark_success, set_phase, start_job, update_job
from app.values.schemas import CompanyValueOut, OverrideRequest, RefreshRequest, ValueDefinitionOut

catalog_router = APIRouter(prefix="/api/value-definitions", tags=["values"])
values_router = APIRouter(prefix="/api/companies", tags=["values"])


def _load_value_map(
    db: Session,
    company_id: UUID,
    period_type: str,
    period_year: int | None,
) -> tuple[list[CompanyValue], dict[str, Decimal | None]]:
    q = db.query(CompanyValue).filter(
        CompanyValue.company_id == company_id,
        CompanyValue.period_type == period_type,
    )
    if period_year is not None:
        q = q.filter(CompanyValue.period_year == period_year)
    else:
        q = q.filter(CompanyValue.period_year.is_(None))
    rows = q.all()
    values = {row.value_key: row.numeric_value for row in rows if row.numeric_value is not None}
    return rows, values


def _persist_calc_results(
    db: Session,
    company_id: UUID,
    period_type: str,
    period_year: int | None,
    existing_rows: list[CompanyValue],
    calc_results: dict[str, Decimal | None],
    allowed_keys: set[str],
    company_currency: str | None,
    source_name_override: str | None = None,
    is_forecast_override: bool | None = None,
) -> list[CompanyValue]:
    by_key = {row.value_key: row for row in existing_rows}
    updated: list[CompanyValue] = []
    default_source = source_name_override or "Calculated"

    for key, value in calc_results.items():
        if key not in allowed_keys:
            continue

        existing = by_key.get(key)
        if value is None and existing is None:
            continue

        calc_currency = company_currency if key in CURRENCY_KEYS else None

        if existing:
            if existing.manually_overridden:
                # Defensive: a calculated key should never be manually_overridden
                # (override_company_value blocks that). Skip rather than silently
                # reset the flag.
                continue
            existing.numeric_value = value
            existing.source_name = default_source
            existing.source_link = None
            existing.fetched_at = datetime.now(timezone.utc)
            existing.from_ir_pdf = False
            if is_forecast_override is not None:
                existing.is_forecast = is_forecast_override
            if calc_currency and not existing.currency:
                existing.currency = calc_currency
            updated.append(existing)
        else:
            cv = CompanyValue(
                id=uuid4(),
                company_id=company_id,
                value_key=key,
                period_type=period_type,
                period_year=period_year,
                numeric_value=value,
                source_name=default_source,
                source_link=None,
                fetched_at=datetime.now(timezone.utc),
                currency=calc_currency,
                is_forecast=bool(is_forecast_override) if is_forecast_override is not None else False,
            )
            db.add(cv)
            updated.append(cv)
    return updated


def _run_and_persist_calculations(
    db: Session,
    company_id: UUID,
    period_type: str,
    period_year: int | None,
) -> list[CompanyValue]:
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    company_currency = company.currency if company else None

    snapshot_rows, stammdaten = _load_value_map(db, company_id, "SNAPSHOT", None)

    stammdaten_calc = calculate_stammdaten(stammdaten)
    for key, val in stammdaten_calc.items():
        if val is not None and key not in stammdaten:
            stammdaten[key] = val

    updated: list[CompanyValue] = []
    updated += _persist_calc_results(
        db, company_id, "SNAPSHOT", None,
        snapshot_rows, stammdaten_calc, STAMMDATEN_CALC_KEYS, company_currency,
    )

    if period_type == "FY" and period_year is not None:
        hohn_locked = company is not None and is_hohn_locked(db, company, period_year)
        if hohn_locked:
            db.query(CompanyValue).filter(
                CompanyValue.company_id == company_id,
                CompanyValue.period_type == "FY",
                CompanyValue.period_year == period_year,
                CompanyValue.value_key.in_(HOHN_KEYS),
            ).delete(synchronize_session=False)
            db.flush()

        current_rows, current = _load_value_map(db, company_id, "FY", period_year)
        _prev_rows, previous = _load_value_map(db, company_id, "FY", period_year - 1)

        if current.get("stock_price") is not None and current.get("shares_outstanding") is not None:
            fy_stammdaten_calc = calculate_stammdaten(current)
            updated += _persist_calc_results(
                db, company_id, "FY", period_year,
                current_rows, fy_stammdaten_calc, STAMMDATEN_CALC_KEYS, company_currency,
            )
            current_rows, current = _load_value_map(db, company_id, "FY", period_year)

        # End-of-FY market cap = start-of-FY+1 market cap (our anchor convention).
        # Used to compute realised total shareholder return (`actual_return`).
        # If the FY+1 anchor is missing AND that FY-end is in the past, try
        # to fetch it on demand so actual_return doesn't show "Inputs fehlen"
        # for an already-completed FY.
        _next_rows, next_year = _load_value_map(db, company_id, "FY", period_year + 1)
        next_mcap = next_year.get("market_cap")
        if next_mcap is None:
            from datetime import date as _date_today
            company = db.query(Company).filter(Company.id == company_id).one_or_none()
            fy_end_in_past = False
            if company is not None and company.fiscal_year_end_month and company.fiscal_year_end_day:
                try:
                    fy_end = _date_today(period_year, company.fiscal_year_end_month, company.fiscal_year_end_day)
                    fy_end_in_past = fy_end <= _date_today.today()
                except ValueError:
                    fy_end_in_past = False
            if fy_end_in_past and company is not None:
                try:
                    _fetch_and_store_historical_mcap(db, company.ticker, company_id, period_year + 1)
                    db.flush()
                    _next_rows, next_year = _load_value_map(db, company_id, "FY", period_year + 1)
                    next_mcap = next_year.get("market_cap")
                except Exception as e:
                    logger.warning("Auto-fetch FY+1 anchor for actual_return failed %s/%s: %s",
                                   company_id, period_year + 1, e)
        fy_calc = calculate_fy(current, previous, stammdaten, next_year_market_cap=next_mcap)

        allowed_fy_keys = FY_CALC_KEYS - HOHN_KEYS if hohn_locked else FY_CALC_KEYS

        # Forecast-Year-Erkennung: wenn fuer dieses FY die Kern-Inputs als
        # Forecast in DB stehen (z.B. net_income aus Web-Guidance), ueberlagern
        # wir die VALUATION-Multiples (pe_ratio, ev_ebitda, fcf_yield) mit
        # Werten aus dem Vorjahr (FY[N-1] = Ist-Werte). User-Anforderung:
        # "im Estimate-Mode soll Bewertung den Stand FY[N-1] zeigen, nicht
        # Forward-Multiple mit Forecast-NI".
        # actual_return bleibt ausgenommen — der ist nur fuer abgeschlossene FY
        # sinnvoll und braucht weiter den next_year_market_cap-Anker.
        is_forecast_year = any(
            r.is_forecast and r.value_key in ("net_income", "ebitda", "fcf", "net_debt", "market_cap")
            for r in current_rows
        )
        if is_forecast_year and previous:
            # Trailing-Bewertung: prev-Werte + prev-MCap (FY[N-1]-Ende = current.market_cap = FY[N]-Anker)
            prev_mcap = previous.get("market_cap")
            prev_ni = previous.get("net_income")
            prev_ebitda = previous.get("ebitda")
            prev_net_debt = previous.get("net_debt")
            prev_fcf = previous.get("fcf")
            valuation_trailing: dict[str, Decimal | None] = {
                "pe_ratio": None,
                "ev_ebitda": None,
                "fcf_yield": None,
            }
            if prev_mcap is not None and prev_ni is not None and prev_ni > 0:
                valuation_trailing["pe_ratio"] = prev_mcap / prev_ni
            if prev_mcap is not None and prev_ebitda is not None and prev_ebitda > 0:
                ev = prev_mcap + (prev_net_debt if prev_net_debt is not None else Decimal("0"))
                valuation_trailing["ev_ebitda"] = ev / prev_ebitda
            if prev_mcap is not None and prev_fcf is not None and prev_mcap != 0:
                valuation_trailing["fcf_yield"] = prev_fcf / prev_mcap * Decimal("100")
            valuation_keys = {"pe_ratio", "ev_ebitda", "fcf_yield"}
            updated += _persist_calc_results(
                db, company_id, "FY", period_year,
                current_rows, valuation_trailing, valuation_keys, company_currency,
                source_name_override=f"Bewertung Stand FY{period_year - 1} (Trailing — Forecast-Year hat keinen Ist-Basis)",
                is_forecast_override=True,
            )
            # WICHTIG: VALUATION-Keys aus dem nachfolgenden fy_calc-Persist
            # ausschliessen, sonst wuerde der zweite _persist_calc_results-Call
            # die gerade gespeicherten Trailing-Werte mit Forward-Multiples
            # (oder None) ueberschreiben. allowed_fy_keys ohne valuation_keys.
            allowed_fy_keys = allowed_fy_keys - valuation_keys

        updated += _persist_calc_results(
            db, company_id, "FY", period_year,
            current_rows, fy_calc, allowed_fy_keys, company_currency,
        )

    return updated


def _get_owned_company(db: Session, user: User, company_id: UUID) -> Company:
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    portfolio = db.query(Portfolio).filter(Portfolio.id == company.portfolio_id).one_or_none()
    if not portfolio or portfolio.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company


@catalog_router.get("", response_model=list[ValueDefinitionOut])
def list_value_definitions(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[ValueDefinition]:
    return (
        db.query(ValueDefinition)
        .order_by(ValueDefinition.sort_order)
        .all()
    )


@values_router.get("/{company_id}/values", response_model=list[CompanyValueOut])
def list_company_values(
    company_id: UUID,
    period_type: str = Query(default="SNAPSHOT"),
    period_year: int | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[CompanyValue]:
    _get_owned_company(db, user, company_id)
    q = db.query(CompanyValue).filter(
        CompanyValue.company_id == company_id,
        CompanyValue.period_type == period_type,
    )
    if period_year is not None:
        q = q.filter(CompanyValue.period_year == period_year)
    return q.all()


def _try_web_guidance(
    db: Session,
    company,
    company_id: UUID,
    key: str,
    target_fy: int,
):
    """Web-Recherche für FY-Guidance via Dual-Provider (Claude + Gemini, Mittel).
    Faellt auf Claude-only zurueck wenn GEMINI_API_KEY nicht gesetzt ist.
    Returns ProviderResult oder None."""
    from app.config import settings
    if not settings.anthropic_api_key:
        return None
    from app.llm.research import research_value_dual
    from app.providers.base import ProviderResult

    vd = db.query(ValueDefinition).filter(ValueDefinition.key == key).one_or_none()
    if vd is None or vd.source_type != SourceType.API:
        return None

    label = f"{vd.label_en} ({vd.label_de})"
    # FY[N-1]-Anker fuer Konsens-Sanity (YoY-Cap) und Currency-Cross-Check.
    prev_fy = target_fy - 1
    prev_row = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == prev_fy,
        )
        .one_or_none()
    )
    prev_fy_val = prev_row.numeric_value if prev_row and prev_row.numeric_value is not None else None
    try:
        dual = research_value_dual(
            company.name, company.ticker, label, company.currency,
            period_type="FY", period_year=target_fy, value_key=key,
            prev_fy_val=prev_fy_val,
        )
    except Exception as e:
        logger.warning("Web-Guidance dual research failed for %s/%s/FY%s: %s",
                       company.ticker, key, target_fy, e)
        return None

    if dual.value is None:
        logger.info("Web-Guidance %s/%s/FY%s: keine Provider lieferte einen Wert",
                    company.ticker, key, target_fy)
        return None

    return ProviderResult(
        value=dual.value,
        source_name=f"Web-Guidance: {dual.source}" if dual.source else "Web-Guidance (Dual-Recherche)",
        source_link=dual.url,
        currency=company.currency if key in CURRENCY_KEYS else None,
        extras={
            "is_forecast": True,
            "guidance_method": "web_research",
            "providers_responded": dual.providers_responded,
            "divergent": dual.divergent,
            "claude_value": str(dual.claude.value) if dual.claude.value is not None else None,
            "claude_source": dual.claude.source,
            "gemini_value": str(dual.gemini.value) if dual.gemini.value is not None else None,
            "gemini_source": dual.gemini.source,
            # Adjusted/Non-GAAP-Variante (nur fuer ni/ebitda/fcf relevant).
            "value_adjusted": str(dual.value_adjusted) if dual.value_adjusted is not None else None,
            "adjustments_note": dual.adjustments_note,
            "adjustments_source": dual.adjustments_source,
        },
    )


def _try_factor_estimate(
    db: Session,
    company,
    company_id: UUID,
    key: str,
    target_fy: int,
):
    """Q-Faktor-Estimate als Proxy. Returns ProviderResult oder None."""
    from app.calculations.estimates import compute_estimate
    from app.providers.base import ProviderResult

    try:
        est = compute_estimate(db, company_id, key, target_fy, currency=company.currency)
    except Exception as e:
        logger.warning("Estimate failed for %s/%s/FY%s: %s", company.ticker, key, target_fy, e)
        return None
    if est is None:
        return None

    if est.method == "flow_factor" and est.factor is not None:
        source_label = f"Proxy (Q-Faktor): FY{target_fy - 1} × Faktor {est.factor:.4f}"
    elif est.method == "balance_snapshot":
        qs = ",".join(est.quarters_used) if est.quarters_used else "?"
        source_label = f"Proxy (Bilanz-Snapshot {qs})"
    elif est.method == "fy_fallback":
        source_label = f"Proxy (FY{target_fy - 1}-Wert, no-growth)"
    else:
        source_label = f"Proxy ({est.method})"

    return ProviderResult(
        value=est.value,
        source_name=source_label,
        source_link=None,
        currency=company.currency if key in CURRENCY_KEYS else None,
        extras={"is_forecast": True, "estimate": est.explanation, "estimate_method": est.method},
    )


def _try_providers(ticker: str, key: str, payload, fy_end_month, fy_end_day):
    for provider in get_providers(key):
        try:
            try:
                result = provider.fetch(
                    ticker, key, payload.period_type, payload.period_year,
                    fy_end_month=fy_end_month, fy_end_day=fy_end_day,
                )
            except TypeError:
                result = provider.fetch(ticker, key, payload.period_type, payload.period_year)
            if result is not None:
                return result
        except Exception as e:
            logger.warning("Provider fetch failed for %s/%s: %s", ticker, key, e)
    return None


def _process_one_key(
    db: Session,
    key: str,
    ticker: str,
    company,
    company_id: UUID,
    payload,
    updated: list,
) -> bool:
    """Returns True if a value was actually written/updated, False if skipped
    (manual override / PDF value / no provider result).

    Source-Strategie:
      - Stammdaten (stock_price/shares/market_cap): immer Yahoo
      - Laufende FY + estimable key: Q-Faktor-Estimate aus Quartals-PDFs
      - US-Filer (ISIN US...): EDGAR + Yahoo Provider-Chain
      - Non-US: keine Provider — der Wert kommt ausschliesslich aus dem
        Annual-Report-PDF (oder bleibt fehlend, dann muss der User explizit
        "Mit Claude recherchieren" klicken)
    Claude wird NIE automatisch aufgerufen.
    """
    effective_period_type = "SNAPSHOT" if key in ALWAYS_CURRENT_KEYS else payload.period_type
    effective_period_year = None if key in ALWAYS_CURRENT_KEYS else payload.period_year

    from datetime import date as _date_today
    from app.calculations.estimates import ESTIMABLE_KEYS

    is_stammdaten = key in ALWAYS_CURRENT_KEYS
    is_running_fy = (
        effective_period_type == "FY"
        and effective_period_year is not None
        and effective_period_year >= _date_today.today().year
    )

    # Priority-Lookup: erst actuals (is_forecast=False), dann forecast.
    # Actuals trumpfen Forecast/Guidance, Manual und PDF blockieren Estimate.
    def _query_for(is_forecast_val: bool):
        q = db.query(CompanyValue).filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == effective_period_type,
            CompanyValue.is_forecast.is_(is_forecast_val),
        )
        if effective_period_year is not None:
            q = q.filter(CompanyValue.period_year == effective_period_year)
        else:
            q = q.filter(CompanyValue.period_year.is_(None))
        return q.one_or_none()

    actuals_existing = _query_for(False)
    forecast_existing = _query_for(True) if is_running_fy else None

    # PDF-Werte sind authoritative — werden nie überschrieben.
    # Manual-Overrides bei FY actuals werden bei Refresh AUSDRUECKLICH
    # überschrieben (User-Wahl: 'Werte berechnen' = neu berechnen, Manual war
    # nur temporaer). Frontend zeigt "Manuell überschreiben"-Button am Cell-
    # Tooltip damit User weiss dass Override fluechtig ist.
    if actuals_existing and actuals_existing.from_ir_pdf and actuals_existing.numeric_value is not None:
        updated.append(actuals_existing)
        return False

    # Pre-existing für den Update-Pfad: bei is_running_fy nutzen wir die
    # forecast-Row (falls vorhanden), sonst die actuals-Row.
    pre_existing = forecast_existing if is_running_fy else actuals_existing
    result = None

    # PDF-Guidance / Manual-Override für Forecast: primary numeric_value
    # bleibt unangetastet (User-Wahl/PDF respektieren). ABER: wir berechnen
    # trotzdem Web+Q-Faktor parallel weiter, damit forecast_alternates für
    # das Drilldown-Tooltip aktuell bleibt. Erst NACH dem ESTIMATE-Block
    # wird entschieden ob primary geupdated wird.
    forecast_locked = bool(
        forecast_existing
        and (forecast_existing.manually_overridden
             or (forecast_existing.from_ir_pdf and forecast_existing.numeric_value is not None))
    )

    # Fallback-Kette pro Cell:
    #   1. Provider-Chain (EDGAR/Yahoo) — schnell wenn US oder Stammdaten
    #   2. Estimate-Mode (laufendes FY + ESTIMABLE): BEIDE Methoden parallel
    #      rechnen — Web-Recherche und Q-Faktor-Proxy. Primary = Web (mit Q-Faktor
    #      Fallback wenn Web leer), zweite Methode wandert in forecast_alternates.
    #   3. Sonst: Web-Recherche als universeller Fallback für FY-Werte.
    forecast_alternates: list[dict] | None = None
    if is_stammdaten or is_us_company(company):
        result = _try_providers(
            ticker, key, payload,
            getattr(company, "fiscal_year_end_month", None),
            getattr(company, "fiscal_year_end_day", None),
        )
        # Wenn Provider erfolgreich war + wir sind im Estimate-Mode + ESTIMABLE
        # Key, rechne trotzdem Q-Faktor + Web fuer den Drilldown-Vergleich
        # (sonst sehen US-Filer keine alternativen Methoden im Tooltip).
        if (
            result is not None
            and is_running_fy
            and key in ESTIMABLE_KEYS
            and not is_stammdaten
        ):
            web_alt_result = _try_web_guidance(db, company, company_id, key, effective_period_year)
            proxy_alt_result = _try_factor_estimate(db, company, company_id, key, effective_period_year)
            alts: list[dict] = []
            if proxy_alt_result is not None and isinstance(proxy_alt_result.value, Decimal):
                extras = proxy_alt_result.extras or {}
                alts.append({
                    "method": "q_factor_proxy",
                    "value": str(proxy_alt_result.value),
                    "currency": proxy_alt_result.currency,
                    "source": proxy_alt_result.source_name,
                    "explanation": extras.get("estimate"),
                })
            # Web-Alt IMMER schreiben — entweder echter Web-Wert oder Notfall-
            # Fallback (Proxy/Primary-Wert mit Markierung). 'Web fehlte' darf
            # nie sichtbar werden im UI.
            if web_alt_result is not None and isinstance(web_alt_result.value, Decimal):
                alts.append({
                    "method": "web_guidance",
                    "value": str(web_alt_result.value),
                    "currency": web_alt_result.currency,
                    "source": web_alt_result.source_name,
                })
            elif proxy_alt_result is not None and isinstance(proxy_alt_result.value, Decimal):
                alts.append({
                    "method": "web_guidance",
                    "value": str(proxy_alt_result.value),
                    "currency": proxy_alt_result.currency,
                    "source": f"Notfall-Fallback (Web-Recherche fehlte trotz Retry): {proxy_alt_result.source_name}",
                    "fallback_from": "q_factor_proxy",
                })
            elif isinstance(result.value, Decimal):
                # Letzter Fallback: nutze den primaeren result-Wert (z.B. PDF-Guidance).
                alts.append({
                    "method": "web_guidance",
                    "value": str(result.value),
                    "currency": result.currency,
                    "source": f"Notfall-Fallback (Web+Proxy fehlten): {result.source_name}",
                    "fallback_from": "primary_result",
                })
            if alts:
                forecast_alternates = alts

    if result is None and is_running_fy and key in ESTIMABLE_KEYS:
        web_result = _try_web_guidance(db, company, company_id, key, effective_period_year)
        proxy_result = _try_factor_estimate(db, company, company_id, key, effective_period_year)

        def _to_alt(r, method: str) -> dict:
            if r is not None and isinstance(r.value, Decimal):
                extras = r.extras or {}
                return {
                    "method": method,
                    "value": str(r.value),
                    "currency": r.currency,
                    "source": r.source_name,
                    "explanation": extras.get("estimate"),
                }
            return {
                "method": method,
                "value": None,
                "currency": None,
                "source": None,
                "error_reason": (
                    "Claude-Recherche lieferte keinen Wert."
                    if method == "web_guidance"
                    else "Q-Faktor-Proxy nicht möglich (z.B. fehlende Q-Daten)."
                ),
            }

        # Beide Alternates IMMER schreiben damit der UI-Vergleich (dual-row) und
        # Drilldown an einem vorhersehbaren Ort stehen — egal welche Methode
        # letztendlich primary wird (Web, Proxy, oder PDF/Manual via forecast_locked).
        proxy_alt = _to_alt(proxy_result, "q_factor_proxy")
        web_alt_obj: dict
        if web_result is not None and isinstance(web_result.value, Decimal):
            web_alt_obj = {
                "method": "web_guidance",
                "value": str(web_result.value),
                "currency": web_result.currency,
                "source": web_result.source_name,
            }
        elif proxy_result is not None and isinstance(proxy_result.value, Decimal):
            # Web hat trotz Retry nichts geliefert — Notfall-Fallback: Web-Slot
            # bekommt den Proxy-Wert damit die Web-Row im UI nie leer ist.
            # Source-Label markiert klar dass Web fehlschlug.
            web_alt_obj = {
                "method": "web_guidance",
                "value": str(proxy_result.value),
                "currency": proxy_result.currency,
                "source": (
                    f"Notfall-Fallback (Web-Recherche fehlte trotz Retry): "
                    f"{proxy_result.source_name}"
                ),
                "fallback_from": "q_factor_proxy",
            }
        else:
            # Auch Proxy fehlt — wirklich kein Wert moeglich. Sehr selten.
            web_alt_obj = {
                "method": "web_guidance",
                "value": None,
                "currency": None,
                "source": None,
                "error_reason": (
                    "Web-Recherche und Q-Faktor-Proxy haben beide nichts geliefert. "
                    "Vorjahres-AR pruefen ob FY[N-1]-Wert vorhanden ist."
                ),
            }
        forecast_alternates = [proxy_alt, web_alt_obj]

        if web_result is not None:
            result = web_result
        elif proxy_result is not None:
            result = proxy_result
        # else: result bleibt None — primary bleibt unangetastet (forecast_locked-Pfad)

    if result is None and not is_stammdaten:
        # Universeller Fallback für historische FY-Werte (z.B. PDF leer, Non-US).
        # Skip wenn Estimate-Branch oben schon Web-Recherche versucht hat —
        # sonst doppelter Anthropic-Call ohne neuen Erkenntnis.
        already_tried_web = is_running_fy and key in ESTIMABLE_KEYS
        target_fy = effective_period_year if effective_period_year is not None else 0
        if target_fy > 0 and not already_tried_web:
            result = _try_web_guidance(db, company, company_id, key, target_fy)

    # Forecast-Lock: primary numeric_value bleibt wie er war (Manual/PDF),
    # aber alternates werden upgedatet damit das Tooltip-Drilldown frisch ist.
    if forecast_locked and forecast_existing is not None:
        if forecast_alternates:
            forecast_existing.forecast_alternates = forecast_alternates
            db.flush()
        updated.append(forecast_existing)
        return True

    if result is None:
        # Refresh hat nichts geliefert. Aber: wir markieren die last_refresh_attempt
        # damit das Frontend einen Stale-Indikator zeigen kann (Daten in DB sind
        # alt, letzter Refresh produzierte nichts neues).
        if pre_existing is not None:
            pre_existing.last_refresh_attempt = datetime.now(timezone.utc)
            db.flush()
            updated.append(pre_existing)
        return False

    # `pre_existing` from the up-front guard is a fresh query result; reuse it
    # so we don't double-query. It can have changed under us only if a
    # concurrent writer mutated the row, which the SAVEPOINT branch below
    # handles via IntegrityError.
    existing = pre_existing

    numeric_value: Decimal | None = None
    text_value: str | None = None
    if isinstance(result.value, Decimal):
        numeric_value = result.value
    elif result.value is not None:
        text_value = str(result.value)

    # Sign normalisation last-mile: every persistence path (PDF, EDGAR, Yahoo,
    # Claude-research, factor-estimate) goes through here, so applying abs()
    # here covers ALL sources at once instead of patching each provider.
    from app.ir_documents.extraction import ALWAYS_POSITIVE_KEYS
    if numeric_value is not None and key in ALWAYS_POSITIVE_KEYS and numeric_value < 0:
        logger.info("Sign-normalising %s/%s/%s: %s -> %s (source=%s)",
                    ticker, key, effective_period_year, numeric_value, abs(numeric_value),
                    result.source_name)
        numeric_value = abs(numeric_value)

    if (
        existing
        and existing.currency
        and result.currency
        and existing.currency != result.currency
        and key in CURRENCY_KEYS
    ):
        # Currency-Mismatch HARTER REJECT statt silent overwrite. Sonst mischen
        # sich USD/EUR/GBP-Werte in FY-Cross-Year-Aggregaten und produzieren
        # falsche Yields/Hohn-Renditen. Lieber alten Wert behalten + last_refresh
        # markieren damit User nachvollziehen kann dass Refresh nicht durchging.
        logger.warning(
            "Currency mismatch BLOCKED %s/%s/%s: existing=%s new=%s (source=%s) — "
            "alter Wert bleibt erhalten, Refresh-Versuch markiert",
            ticker, key, effective_period_year, existing.currency, result.currency, result.source_name,
        )
        existing.last_refresh_attempt = datetime.now(timezone.utc)
        db.flush()
        updated.append(existing)
        return False

    is_forecast_flag = bool((result.extras or {}).get("is_forecast", False)) if result.extras else False

    # Primary-Method-Marker setzen: explizites Field statt fragile
    # Source-Name-Heuristik im Frontend.
    extras = result.extras or {}
    if extras.get("guidance_method") == "web_research":
        primary_method = "web_guidance"
    elif extras.get("estimate_method") in ("flow_factor", "balance_snapshot", "fy_fallback"):
        primary_method = "q_factor_proxy"
    else:
        primary_method = "provider"

    # Adjusted/Non-GAAP-Variante aus extras parsen (kommt von Dual-Web-Research).
    raw_adj = extras.get("value_adjusted")
    value_adjusted: Decimal | None = None
    if raw_adj is not None:
        try:
            value_adjusted = Decimal(str(raw_adj))
        except Exception:
            value_adjusted = None
    adjustments_note_val = extras.get("adjustments_note")
    adjustments_source_val = extras.get("adjustments_source")

    def _apply_update(target: CompanyValue) -> None:
        target.numeric_value = numeric_value
        target.text_value = text_value
        target.currency = result.currency
        target.source_name = result.source_name
        target.source_link = result.source_link
        now = datetime.now(timezone.utc)
        target.fetched_at = now
        target.last_refresh_attempt = now
        target.from_ir_pdf = False
        target.is_forecast = is_forecast_flag
        target.primary_method = primary_method
        # Manual-Override-Flag zuruecksetzen wenn Refresh erfolgreich war
        # (User-Override war temporaer).
        target.manually_overridden = False
        # Adjusted-Werte nur ueberschreiben wenn der Run Adjusted geliefert hat
        # — sonst alten Adjusted-Wert (z.B. aus AR-PDF) erhalten.
        if value_adjusted is not None:
            target.numeric_value_adjusted = value_adjusted
            target.adjustments_note = (adjustments_note_val or "")[:4000] or None
            target.adjustments_source = (adjustments_source_val or "")[:2048] or None
        # forecast_alternates nur überschreiben wenn dieser Run sie explizit
        # berechnet hat (Estimate-Pfad). Sonst bleiben vorhandene Alternates
        # erhalten — Provider-Pfad darf den Drilldown-Tooltip nicht clobbern.
        if forecast_alternates is not None:
            target.forecast_alternates = forecast_alternates

    try:
        if existing:
            _apply_update(existing)
            updated.append(existing)
            db.flush()
            return True
        now = datetime.now(timezone.utc)
        cv = CompanyValue(
            id=uuid4(),
            company_id=company_id,
            value_key=key,
            period_type=effective_period_type,
            period_year=effective_period_year,
            numeric_value=numeric_value,
            numeric_value_adjusted=value_adjusted,
            adjustments_note=(adjustments_note_val or "")[:4000] or None,
            adjustments_source=(adjustments_source_val or "")[:2048] or None,
            text_value=text_value,
            currency=result.currency,
            source_name=result.source_name,
            source_link=result.source_link,
            fetched_at=now,
            last_refresh_attempt=now,
            is_forecast=is_forecast_flag,
            forecast_alternates=forecast_alternates,
            primary_method=primary_method,
        )
        try:
            with db.begin_nested():
                db.add(cv)
                db.flush()
            updated.append(cv)
            return True
        except IntegrityError:
            # Concurrent insert from another request — re-query and update.
            # Filter MUST include is_forecast — sonst kollidiert ein
            # Forecast-Row mit einem Actuals-Row im Result.
            eq2 = (
                db.query(CompanyValue)
                .filter(
                    CompanyValue.company_id == company_id,
                    CompanyValue.value_key == key,
                    CompanyValue.period_type == effective_period_type,
                    CompanyValue.is_forecast.is_(is_forecast_flag),
                )
            )
            if effective_period_year is not None:
                eq2 = eq2.filter(CompanyValue.period_year == effective_period_year)
            else:
                eq2 = eq2.filter(CompanyValue.period_year.is_(None))
            row = eq2.one_or_none()
            if row is None:
                raise
            if row.manually_overridden or (row.from_ir_pdf and row.numeric_value is not None):
                updated.append(row)
                return False
            _apply_update(row)
            updated.append(row)
            db.flush()
            return True
    except Exception as e:
        logger.error("DB save failed for key=%s company=%s: %s", key, ticker, e)
        db.rollback()
        return False


_PREV_YEAR_GROWTH_KEYS = (
    "net_income",
    "fcf",
    "net_debt",
    "sbc",
    "buyback_volume",
    "dividends",
)


def _upsert_fy_value(
    db: Session,
    company_id: UUID,
    value_key: str,
    period_year: int,
    value: Decimal,
    source_name: str,
    source_link: str | None,
    currency: str | None,
) -> None:
    existing = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == value_key,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == period_year,
        )
        .one_or_none()
    )
    now = datetime.now(timezone.utc)
    if existing:
        if existing.manually_overridden:
            return
        if existing.from_ir_pdf and existing.numeric_value is not None:
            return  # PDF value wins over Yahoo historical helper
        existing.numeric_value = value
        existing.source_name = source_name
        existing.source_link = source_link
        existing.currency = currency
        existing.fetched_at = now
        existing.from_ir_pdf = False
    else:
        db.add(CompanyValue(
            id=uuid4(),
            company_id=company_id,
            value_key=value_key,
            period_type="FY",
            period_year=period_year,
            numeric_value=value,
            source_name=source_name,
            source_link=source_link,
            currency=currency,
            fetched_at=now,
        ))


def _ensure_company_fy_end(db: Session, company: Company) -> tuple[int, int]:
    """Return (fy_end_month, fy_end_day). If missing on company, auto-detect via
    Yahoo and persist. Falls back to (12, 31) if detection fails."""
    if company.fiscal_year_end_month and company.fiscal_year_end_day:
        return (company.fiscal_year_end_month, company.fiscal_year_end_day)
    providers = get_providers("market_cap")
    provider = next((p for p in providers if hasattr(p, "detect_fiscal_year_end")), None)
    if provider is None:
        return (12, 31)
    detected = None
    try:
        detected = provider.detect_fiscal_year_end(company.ticker)
    except Exception as e:
        logger.warning("FY-end detect failed for %s: %s", company.ticker, e)
    if (
        detected is None
        or not isinstance(detected, tuple)
        or len(detected) != 2
        or not all(isinstance(v, int) for v in detected)
        or not (1 <= detected[0] <= 12 and 1 <= detected[1] <= 31)
    ):
        return (12, 31)
    company.fiscal_year_end_month = detected[0]
    company.fiscal_year_end_day = detected[1]
    db.flush()
    return detected


def _fetch_and_store_historical_mcap(
    db: Session,
    ticker: str,
    company_id: UUID,
    period_year: int,
) -> None:
    """Fetch + store stammdaten for the START of FY `period_year`, i.e. anchored
    to the FY-end of (period_year-1). For a Dec-FY company this is 31.12.(N-1);
    for a Jun-FY company (e.g. Microsoft) it's 30.06.(N-1). The resulting
    market_cap, stock_price, shares_outstanding represent what the investor saw
    when entering the fiscal year. Stored as period_type=FY, period_year=N.
    Best-effort — failures logged."""
    providers = get_providers("market_cap")
    provider = next((p for p in providers if hasattr(p, "fetch_historical_market_cap")), None)
    if provider is None:
        return
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if company is None:
        return
    fy_month, fy_day = _ensure_company_fy_end(db, company)
    anchor_year = period_year - 1
    try:
        result = provider.fetch_historical_market_cap(ticker, anchor_year, fy_month, fy_day)
    except Exception as e:
        logger.warning("Historical MCap fetch failed %s/FY%s (anchor %s): %s", ticker, period_year, anchor_year, e)
        return
    if result is None or not isinstance(result.value, Decimal):
        return

    # The actual close picked by Yahoo is the last trading day on-or-before
    # (anchor_year, fy_month, fy_day). Economically that close = price the
    # investor saw at start of the next trading day, i.e. day-1 of FY=period_year.
    # We display that day-1 date in the source label so the UI reads cleanly.
    from datetime import date as _date, timedelta as _td
    try:
        fy_start_label = (_date(anchor_year, fy_month, fy_day) + _td(days=1)).strftime("%d.%m.%Y")
    except ValueError:
        fy_start_label = f"01.01.{period_year}"
    extras = getattr(result, "extras", None) or {}
    stock_price = extras.get("stock_price") if isinstance(extras, dict) else None
    shares = extras.get("shares_outstanding") if isinstance(extras, dict) else None
    shares_source = extras.get("shares_source") if isinstance(extras, dict) else None
    shares_label = shares_source or "current Shares"
    anchor_note = f"Anfang FY{period_year} = {fy_start_label}"

    _upsert_fy_value(db, company_id, "market_cap", period_year, result.value,
                     f"Yahoo (Close {anchor_note} × {shares_label})", result.source_link, result.currency)
    if isinstance(stock_price, Decimal):
        _upsert_fy_value(db, company_id, "stock_price", period_year, stock_price,
                         f"Yahoo (Adj Close {anchor_note})", result.source_link, result.currency)
    if isinstance(shares, Decimal):
        _upsert_fy_value(db, company_id, "shares_outstanding", period_year, shares,
                         f"Yahoo ({shares_label}, {anchor_note})", result.source_link, None)


def _ensure_previous_year_inputs(
    db: Session,
    ticker: str,
    company,
    company_id: UUID,
    period_year: int,
) -> None:
    """Fetch previous-FY per-input values if they are not yet persisted.
    Required so ni_growth and net_debt_change can be computed for period_year."""
    prev_year = period_year - 1

    existing = db.query(CompanyValue).filter(
        CompanyValue.company_id == company_id,
        CompanyValue.period_type == "FY",
        CompanyValue.period_year == prev_year,
        CompanyValue.value_key.in_(_PREV_YEAR_GROWTH_KEYS),
    ).all()
    existing_keys = {r.value_key for r in existing if r.numeric_value is not None}
    missing = [k for k in _PREV_YEAR_GROWTH_KEYS if k not in existing_keys]
    if not missing:
        return

    class _PrevPayload:
        period_type = "FY"
        period_year = prev_year

    prev_payload = _PrevPayload()
    dummy_updated: list = []
    for key in missing:
        try:
            _process_one_key(
                db=db,
                key=key,
                ticker=ticker,
                company=company,
                company_id=company_id,
                payload=prev_payload,
                updated=dummy_updated,
            )
        except Exception as e:
            logger.warning("Prev-year prefetch failed for %s/%s FY%s: %s", ticker, key, prev_year, e)
            db.rollback()


@values_router.get("/{company_id}/refresh-status")
def get_refresh_status(
    company_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    _get_owned_company(db, user, company_id)
    job = get_job(company_id)
    if not job:
        return {"status": "idle"}
    return job


@values_router.post("/{company_id}/values/refresh", response_model=list[CompanyValueOut])
def refresh_company_values(
    company_id: UUID,
    payload: RefreshRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[CompanyValue]:
    cleanup_old_jobs()
    company = _get_owned_company(db, user, company_id)
    ticker = company.ticker
    if company.isin:
        providers = get_providers("stock_price")
        if providers and hasattr(providers[0], "resolve_ticker_from_isin"):
            resolved = providers[0].resolve_ticker_from_isin(company.isin)
            if resolved and resolved != ticker:
                ticker = resolved
                company.ticker = ticker
                db.flush()
    updated = []

    start_job(company_id, len(payload.keys))
    try:
        for key in payload.keys:
            update_job(company_id, key)
            try:
                wrote = _process_one_key(
                    db=db,
                    key=key,
                    ticker=ticker,
                    company=company,
                    company_id=company_id,
                    payload=payload,
                    updated=updated,
                )
                if wrote:
                    mark_success(company_id)
            except Exception as e:
                logger.error("Unexpected error processing key=%s for company=%s: %s", key, ticker, e)
                db.rollback()

        db.commit()

        if payload.period_type == "FY" and payload.period_year is not None:
            from datetime import date
            current_calendar_year = date.today().year
            if payload.period_year < current_calendar_year:
                set_phase(company_id, "historical_mcap", f"Historische Market Cap (31.12.{payload.period_year})")
                _fetch_and_store_historical_mcap(db, ticker, company_id, payload.period_year)
                db.commit()

            set_phase(company_id, "prev_year_inputs", f"Vorjahres-Daten holen (FY{payload.period_year - 1})")
            _ensure_previous_year_inputs(db, ticker, company, company_id, payload.period_year)
            db.commit()

        set_phase(company_id, "calculating", "Berechnete Werte aktualisieren")
        _run_and_persist_calculations(db, company_id, payload.period_type, payload.period_year)
        db.commit()
    except Exception:
        finish_job(company_id, status="failed")
        raise
    else:
        finish_job(company_id)

    for cv in updated:
        db.refresh(cv)
    return updated


@values_router.post("/{company_id}/values/calculate", response_model=list[CompanyValueOut])
def calculate_company_values(
    company_id: UUID,
    period_type: str = Query(default="SNAPSHOT"),
    period_year: int | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[CompanyValue]:
    _get_owned_company(db, user, company_id)
    calc_updated = _run_and_persist_calculations(db, company_id, period_type, period_year)
    db.commit()
    for cv in calc_updated:
        db.refresh(cv)
    return calc_updated


@values_router.post("/{company_id}/values/{value_key}/research")
def research_company_value(
    company_id: UUID,
    value_key: str,
    period_type: str = Query(default="FY"),
    period_year: int | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Manueller Trigger für Claude-Web-Recherche zu einer einzelnen Zelle.
    Persistiert das Ergebnis direkt als CompanyValue (manually_overridden=True).
    Returns: { value, source_name, source_url, value_found }.
    """
    from app.config import settings
    from app.llm.claude import research_value, validate_claude_value

    company = _get_owned_company(db, user, company_id)

    if not settings.anthropic_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Claude-API nicht konfiguriert")
    if value_key in CALCULATED_KEYS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Berechnete Werte können nicht recherchiert werden")

    vd = db.query(ValueDefinition).filter(ValueDefinition.key == value_key).one_or_none()
    if vd is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Wert")
    if vd.source_type == SourceType.QUALITATIVE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Qualitative Werte werden nicht via Recherche befuellt")

    eff_period_type = "SNAPSHOT" if value_key in ALWAYS_CURRENT_KEYS else period_type
    eff_period_year = None if value_key in ALWAYS_CURRENT_KEYS else period_year

    label = f"{vd.label_en} ({vd.label_de})"
    research_val, research_source, research_url, _user_prompt, _assistant_response = research_value(
        company.name, company.ticker, label, company.currency,
        period_type=eff_period_type, period_year=eff_period_year, value_key=value_key,
    )
    if research_val is None:
        return {"value": None, "source_name": None, "source_url": None, "value_found": False}

    research_val = validate_claude_value(value_key, research_val)
    if research_val is None:
        return {"value": None, "source_name": None, "source_url": None, "value_found": False}

    # Direkt in CompanyValue persistieren — User hat die Recherche manuell ausgeloest.
    is_running_fy = (
        eff_period_type == "FY"
        and eff_period_year is not None
        and eff_period_year >= datetime.now(timezone.utc).year
    )
    inherit_currency = company.currency if value_key in CURRENCY_KEYS else None
    source_label = f"Claude-Recherche: {research_source}" if research_source else "Claude-Recherche"

    oq = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == value_key,
            CompanyValue.period_type == eff_period_type,
            CompanyValue.is_forecast.is_(is_running_fy),
        )
    )
    if eff_period_year is not None:
        oq = oq.filter(CompanyValue.period_year == eff_period_year)
    else:
        oq = oq.filter(CompanyValue.period_year.is_(None))
    existing = oq.one_or_none()

    if existing:
        existing.numeric_value = research_val
        existing.source_name = source_label
        existing.source_link = research_url
        if inherit_currency and not existing.currency:
            existing.currency = inherit_currency
        existing.manually_overridden = True
        existing.from_ir_pdf = False
        existing.is_forecast = is_running_fy
        existing.fetched_at = datetime.now(timezone.utc)
    else:
        cv = CompanyValue(
            id=uuid4(),
            company_id=company_id,
            value_key=value_key,
            period_type=eff_period_type,
            period_year=eff_period_year,
            numeric_value=research_val,
            source_name=source_label,
            source_link=research_url,
            currency=inherit_currency,
            manually_overridden=True,
            from_ir_pdf=False,
            is_forecast=is_running_fy,
            fetched_at=datetime.now(timezone.utc),
        )
        db.add(cv)

    _recalc_after_override(
        db, company_id, value_key, eff_period_type, eff_period_year, label,
    )
    db.commit()

    return {
        "value": str(research_val),
        "source_name": source_label,
        "source_url": research_url,
        "value_found": True,
    }


@values_router.post("/{company_id}/values/{value_key}/explain")
def explain_company_value(
    company_id: UUID,
    value_key: str,
    period_type: str = Query(default="FY"),
    period_year: int | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Claude-Einordnung zu einem konkreten Finanzwert: ist der Wert normal/
    auffaellig? Falls auffaellig, welche Gruende? Was bedeutet er für die
    Capital-Allocation-Story?
    Wird NICHT persistiert — nur on-demand für User-Drilldown."""
    from app.config import settings
    from app.llm.claude import get_client, claude_limiter, _collect_text, WEB_SEARCH_TOOL

    company = _get_owned_company(db, user, company_id)
    if not settings.anthropic_api_key:
        raise HTTPException(503, "Claude-API nicht konfiguriert")
    if value_key in CALCULATED_KEYS:
        raise HTTPException(400, "Berechnete Werte (Formeln) brauchen keine Claude-Einordnung")
    if period_type != "FY" or period_year is None:
        raise HTTPException(400, "Einordnung nur für FY-Werte mit Jahresangabe")

    # Lookup ohne is_forecast-Filter — sowohl actuals (abgeschlossenes FY)
    # als auch Forecasts (Estimate-Mode FY[N]e) werden eingeordnet.
    cv = (db.query(CompanyValue).filter(
        CompanyValue.company_id == company_id,
        CompanyValue.value_key == value_key,
        CompanyValue.period_type == "FY",
        CompanyValue.period_year == period_year,
    ).order_by(CompanyValue.is_forecast.asc()).first())  # Actuals bevorzugt wenn beide existieren
    if cv is None or cv.numeric_value is None:
        raise HTTPException(404, "Wert nicht vorhanden")
    vd = db.query(ValueDefinition).filter(ValueDefinition.key == value_key).one_or_none()
    if vd is None:
        raise HTTPException(404, "Unbekannter Wert")

    is_forecast_value = bool(cv.is_forecast)

    # Historische Werte: für Forecast-Einordnung wollen wir auch FY[N-1] sehen
    # (das ist der Anker), bei Actuals nur frühere abgeschlossene Jahre.
    historical = (db.query(CompanyValue).filter(
        CompanyValue.company_id == company_id,
        CompanyValue.value_key == value_key,
        CompanyValue.period_type == "FY",
        CompanyValue.period_year < period_year,
        CompanyValue.is_forecast.is_(False),
        CompanyValue.numeric_value.isnot(None),
    ).order_by(CompanyValue.period_year.desc()).limit(5).all())

    history_lines = "\n".join(
        f"  FY{r.period_year}: {float(r.numeric_value):,.0f} {r.currency or ''}"
        for r in reversed(historical)
    ) or "  (keine historischen Werte verfügbar)"

    cur = cv.currency or company.currency or ""
    if is_forecast_value:
        # Forecast-Einordnung: Plausibilitaet vs Konsens, Risiken/Treiber.
        source_block = f"\nDatenquelle Forecast: {cv.source_name or '(unbekannt)'}"
        prompt = (
            f"Du bist Senior Equity Analyst. Schreibe eine kurze Einordnung (3-5 Sätze) "
            f"zu folgender Forecast-Schaetzung:\n\n"
            f"Unternehmen: {company.name} ({company.ticker})\n"
            f"Kennzahl: {vd.label_de} / {vd.label_en} ({value_key})\n"
            f"Zeitraum: FY{period_year}e (Forecast)\n"
            f"Geschaetzter Wert: {float(cv.numeric_value):,.0f} {cur}{source_block}\n\n"
            f"Historische Ist-Werte (Vergleich):\n{history_lines}\n\n"
            f"Beantworte konkret:\n"
            f"1. Ist die FY{period_year}e-Schaetzung plausibel im Vergleich zum "
            f"historischen Trend und zum Sell-Side-Konsens (sofern bekannt)?\n"
            f"2. Welche TREIBER stuetzen den Wert (Management-Guidance, Markt-Konsens, "
            f"sektorspezifische Trends, M&A, Programme)?\n"
            f"3. Welche RISIKEN koennten den Wert nach unten drehen (Konjunktur, "
            f"Margen-Druck, FX, Regulatorik, Capital-Allocation-Aenderungen)?\n\n"
            f"Nutze web_search aktiv um aktuelle Konsens-Schaetzungen, IR-Guidance, "
            f"Earnings-Call-Transcripts und News abzugleichen. "
            f"Praezise und kurz, keine Floskeln. Auf Deutsch."
        )
    else:
        prompt = (
            f"Du bist Senior Equity Analyst. Schreibe eine kurze Einordnung (3-5 Sätze) "
            f"zu folgendem Finanzwert:\n\n"
            f"Unternehmen: {company.name} ({company.ticker})\n"
            f"Kennzahl: {vd.label_de} / {vd.label_en} ({value_key})\n"
            f"Zeitraum: FY{period_year}\n"
            f"Wert: {float(cv.numeric_value):,.0f} {cur}\n\n"
            f"Historische Werte (Vergleich):\n{history_lines}\n\n"
            f"Beantworte konkret:\n"
            f"1. Ist der FY{period_year}-Wert normal/erwartbar im historischen Trend?\n"
            f"2. Falls auffaellig (z.B. starker Anstieg/Rueckgang): welche Gruende? "
            f"(Akquisition, One-Time-Charge, Marktveränderung, Sonderausschüttung, "
            f"Restructuring, neuer Bilanzposten etc.)\n"
            f"3. Was bedeutet der Wert für die langfristige Capital-Allocation-Story "
            f"des Unternehmens?\n\n"
            f"Nutze web_search wenn noetig für aktuelle Begründungen / News-Kontext. "
            f"Praezise und kurz, keine Floskeln. Auf Deutsch."
        )
    try:
        client = get_client()
        response = claude_limiter.call(lambda: client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            tools=[WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": prompt}],
        ))
        # stop_reason loggen: max_tokens deutet auf zu kurzes Budget, end_turn
        # ist normal, tool_use waere unerwartet (wir akzeptieren nur Text).
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "max_tokens":
            logger.warning(
                "Explain-call %s/%s/%s wurde bei max_tokens=4096 abgeschnitten — "
                "Antwort unvollstaendig. Erwaege weitere Erhoehung oder kuerzere Prompt-Struktur.",
                company.ticker, value_key, period_year,
            )
        text = _collect_text(response)
        return {"explanation": text or "(Keine Antwort von Claude erhalten)"}
    except Exception as e:
        logger.warning("Explain-call failed for %s/%s/%s: %s", company.ticker, value_key, period_year, e)
        raise HTTPException(503, f"Claude-Einordnung fehlgeschlagen: {str(e)[:200]}")


@values_router.post("/recalc-all-fy")
def recalc_all_fy(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    refetch_mcap: bool = Query(default=True, description="Re-fetch historical MCap with start-of-FY anchor"),
) -> dict:
    """Re-anchor all historical stammdaten to start-of-FY and re-run
    _run_and_persist_calculations for every (company, FY-year) the user owns.
    Use after changing the MCap-anchor semantics so stored values + Calculated
    rows reflect the new logic. Returns counts."""
    owned_pids = (
        db.query(Portfolio.id).filter(Portfolio.owner_user_id == user.id).all()
    )
    pid_set = {p[0] for p in owned_pids}
    companies = (
        db.query(Company).filter(Company.portfolio_id.in_(pid_set)).all()
    )
    n_companies = 0
    n_years = 0
    n_mcap_refetched = 0
    for c in companies:
        years = (
            db.query(CompanyValue.period_year)
            .filter(
                CompanyValue.company_id == c.id,
                CompanyValue.period_type == "FY",
                CompanyValue.period_year.isnot(None),
            )
            .distinct()
            .all()
        )
        if not years:
            continue
        n_companies += 1
        for (yr,) in sorted(years):
            if refetch_mcap:
                # Wipe previous Yahoo-historical row so the new anchor takes effect
                # without being blocked by the from_ir_pdf=false / non-manual check.
                for k in ("market_cap", "stock_price", "shares_outstanding"):
                    db.query(CompanyValue).filter(
                        CompanyValue.company_id == c.id,
                        CompanyValue.value_key == k,
                        CompanyValue.period_type == "FY",
                        CompanyValue.period_year == yr,
                        CompanyValue.manually_overridden == False,  # noqa: E712
                        CompanyValue.from_ir_pdf == False,          # noqa: E712
                    ).delete(synchronize_session=False)
                try:
                    _fetch_and_store_historical_mcap(db, c.ticker, c.id, yr)
                    n_mcap_refetched += 1
                except Exception as e:
                    logger.warning("recalc-all-fy mcap refetch failed company=%s year=%s: %s", c.id, yr, e)
                    db.rollback()
            try:
                _run_and_persist_calculations(db, c.id, "FY", yr)
                n_years += 1
            except Exception as e:
                logger.warning("recalc-all-fy failed for company=%s year=%s: %s", c.id, yr, e)
                db.rollback()
        try:
            _run_and_persist_calculations(db, c.id, "SNAPSHOT", None)
        except Exception as e:
            logger.warning("recalc-all-fy SNAPSHOT failed for company=%s: %s", c.id, e)
            db.rollback()
    db.commit()
    return {
        "companies_processed": n_companies,
        "fy_rows_recalculated": n_years,
        "mcap_anchors_refetched": n_mcap_refetched,
    }


_CUM_INPUT_KEYS_FY: tuple[str, ...] = (
    "fcf",
    "net_income",
    "sbc",
    "buyback_volume",
    "dividends",
    "net_debt",
)


def _decimal_to_str(v: Decimal | None) -> str | None:
    return str(v) if v is not None else None


def _cell_to_dict(cell: dict) -> dict:
    return {
        "cum": _decimal_to_str(cell.get("cum")),
        "pa_avg": _decimal_to_str(cell.get("pa_avg")),
        "pa_cagr": _decimal_to_str(cell.get("pa_cagr")),
        "missing": cell.get("missing", []),
    }


@values_router.post("/{company_id}/values/historical-stammdaten")
def post_historical_stammdaten(
    company_id: UUID,
    period_year: int = Query(...),
    force: bool = Query(default=False),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Fetch + store historical Stammdaten (market_cap, stock_price,
    shares_outstanding) for the given FY year, anchored to the company's
    fiscal year-end. Auto-detects FY-end on first call. Refetches if stored
    data references a different FY-end-date than the company currently has.
    Always runs _run_and_persist_calculations afterwards."""
    company = _get_owned_company(db, user, company_id)
    from datetime import date
    if period_year >= date.today().year:
        _run_and_persist_calculations(db, company_id, "FY", period_year)
        db.commit()
        return {"stored": False, "reason": "period_year is current or future; using SNAPSHOT MCap"}

    fy_month, fy_day = _ensure_company_fy_end(db, company)
    db.commit()
    expected_marker = f"{fy_day:02d}.{fy_month:02d}.{period_year}"

    existing_mcap = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == "market_cap",
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == period_year,
            CompanyValue.numeric_value.isnot(None),
        )
        .first()
    )
    src = existing_mcap.source_name if existing_mcap else None
    has_old_v1_format = bool(src) and "current Shares" in src and "Shares per" not in src
    needs_refetch = (
        force
        or existing_mcap is None
        or (src and expected_marker not in src)
        or has_old_v1_format
    )
    if needs_refetch:
        _fetch_and_store_historical_mcap(db, company.ticker, company_id, period_year)
        db.commit()
    _run_and_persist_calculations(db, company_id, "FY", period_year)
    db.commit()
    return {"stored": needs_refetch, "period_year": period_year, "fy_end_used": expected_marker}


@values_router.get("/{company_id}/fy-availability")
def get_fy_availability(
    company_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    company = _get_owned_company(db, user, company_id)
    rows = (
        db.query(CompanyValue.period_year, CompanyValue.value_key)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year.isnot(None),
            CompanyValue.numeric_value.isnot(None),
        )
        .all()
    )
    keys_per_year: dict[int, list[str]] = {}
    for year, key in rows:
        keys_per_year.setdefault(year, []).append(key)
    snap_market_cap = (
        db.query(CompanyValue.id)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.period_type == "SNAPSHOT",
            CompanyValue.value_key == "market_cap",
            CompanyValue.numeric_value.isnot(None),
        )
        .first()
        is not None
    )
    return {
        "fy_years_with_data": sorted(keys_per_year.keys()),
        "keys_per_year": {str(y): sorted(set(ks)) for y, ks in keys_per_year.items()},
        "has_snapshot_market_cap": snap_market_cap,
        "is_us": is_us_company(company),
        "annual_report_years": annual_report_years(db, company_id),
        "quarter_years": quarter_years(db, company_id),
        "quarter_years_in_progress": quarter_years_in_progress(db, company_id),
    }


@values_router.get("/{company_id}/values/cumulative")
def get_cumulative_values(
    company_id: UUID,
    from_year: int = Query(...),
    to_year: int = Query(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    _get_owned_company(db, user, company_id)
    if from_year > to_year:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="from_year must be <= to_year")

    pre_year = from_year - 1
    period_years = list(range(from_year, to_year + 1))

    def _has_any_data(year: int) -> bool:
        return (
            db.query(CompanyValue.id)
            .filter(
                CompanyValue.company_id == company_id,
                CompanyValue.period_type == "FY",
                CompanyValue.period_year == year,
            )
            .first()
            is not None
        )

    missing_years = [y for y in [pre_year] + period_years if not _has_any_data(y)]

    year_data: dict[int, dict[str, Decimal | None]] = {}
    for year in period_years:
        _, data = _load_value_map(db, company_id, "FY", year)
        year_data[year] = data
    _, pre_data = _load_value_map(db, company_id, "FY", pre_year)
    _, stammdaten = _load_value_map(db, company_id, "SNAPSHOT", None)

    cum_results = calculate_cumulative(year_data, pre_data, stammdaten)

    per_year_breakdown: dict[int, dict[str, str | None]] = {}
    for year in period_years:
        per_year_breakdown[year] = {
            k: _decimal_to_str(year_data[year].get(k))
            for k in _CUM_INPUT_KEYS_FY
        }
    pre_breakdown = {
        k: _decimal_to_str(pre_data.get(k))
        for k in ("net_income", "net_debt")
    }

    first_year_mcap = year_data.get(from_year, {}).get("market_cap")
    snap_mcap = stammdaten.get("market_cap")
    anchor_mcap = first_year_mcap if first_year_mcap is not None else snap_mcap
    anchor_label = (
        f"Anfang FY{from_year}" if first_year_mcap is not None
        else "SNAPSHOT (Fallback — historischer MCap fehlt)"
    )
    return {
        "from_year": from_year,
        "to_year": to_year,
        "n_years": len(period_years),
        "market_cap": _decimal_to_str(anchor_mcap),
        "market_cap_anchor": anchor_label,
        "snapshot_market_cap": _decimal_to_str(snap_mcap),
        "values": {k: _cell_to_dict(v) for k, v in cum_results.items()},
        "per_year_breakdown": per_year_breakdown,
        "pre_period_year": pre_year,
        "pre_period_breakdown": pre_breakdown,
        "missing_years": missing_years,
    }


_CROSS_YEAR_TRIGGER_INPUTS: frozenset[str] = frozenset({
    "net_income",
    "net_debt",
})


def _existing_fy_years(db: Session, company_id: UUID) -> list[int]:
    rows = (
        db.query(CompanyValue.period_year)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year.isnot(None),
        )
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows})


def _fy_year_has_data(db: Session, company_id: UUID, year: int) -> bool:
    return (
        db.query(CompanyValue.id)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == year,
        )
        .first()
        is not None
    )


def _recalc_after_override(
    db: Session,
    company_id: UUID,
    value_key: str,
    period_type: str,
    period_year: int | None,
    trigger_label: str,
) -> None:
    affected: list[tuple[str, int | None]] = [(period_type, period_year)]

    if value_key == "market_cap":
        for year in _existing_fy_years(db, company_id):
            affected.append(("FY", year))
    elif (
        period_type == "FY"
        and period_year is not None
        and value_key in _CROSS_YEAR_TRIGGER_INPUTS
        and _fy_year_has_data(db, company_id, period_year + 1)
    ):
        affected.append(("FY", period_year + 1))

    for pt, py in affected:
        _run_and_persist_calculations(db, company_id, pt, py)

    db.flush()


@values_router.post("/{company_id}/values/{value_key}/override", response_model=CompanyValueOut)
def override_company_value(
    company_id: UUID,
    value_key: str,
    payload: OverrideRequest,
    period_type: str = Query(default="SNAPSHOT"),
    period_year: int | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CompanyValue:
    company = _get_owned_company(db, user, company_id)

    if value_key in CALCULATED_KEYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{value_key}' ist ein berechneter Wert (Formel) und kann nicht "
                "direkt gesetzt werden. Korrigiere stattdessen die Eingangswerte."
            ),
        )

    effective_period_type = "SNAPSHOT" if value_key in ALWAYS_CURRENT_KEYS else period_type
    effective_period_year = None if value_key in ALWAYS_CURRENT_KEYS else period_year

    oq = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == value_key,
            CompanyValue.period_type == effective_period_type,
        )
    )
    if effective_period_year is not None:
        oq = oq.filter(CompanyValue.period_year == effective_period_year)
    else:
        oq = oq.filter(CompanyValue.period_year.is_(None))
    # Wenn fuer ein Jahr sowohl Forecast als auch Actual existieren, bevorzugt
    # der Override den Actual-Eintrag (is_forecast=False sortiert vorne).
    # Fuer Forecast-only Jahre wird nur der Forecast gefunden.
    existing = oq.order_by(CompanyValue.is_forecast.asc()).first()

    inherit_currency = company.currency if value_key in CURRENCY_KEYS else None

    if existing:
        if payload.numeric_value is not None:
            existing.numeric_value = payload.numeric_value
        if payload.text_value is not None:
            existing.text_value = payload.text_value
        if payload.source_name is not None:
            existing.source_name = payload.source_name
        if inherit_currency and not existing.currency:
            existing.currency = inherit_currency
        existing.manually_overridden = True
        existing.from_ir_pdf = False
        existing.primary_method = "manual"
        existing.fetched_at = datetime.now(timezone.utc)
        result_cv = existing
    else:
        cv = CompanyValue(
            id=uuid4(),
            company_id=company_id,
            value_key=value_key,
            period_type=effective_period_type,
            period_year=effective_period_year,
            numeric_value=payload.numeric_value,
            text_value=payload.text_value,
            source_name=payload.source_name,
            currency=inherit_currency,
            manually_overridden=True,
            from_ir_pdf=False,
            primary_method="manual",
            fetched_at=datetime.now(timezone.utc),
        )
        db.add(cv)
        result_cv = cv

    db.flush()

    vd_for_label = db.query(ValueDefinition).filter(ValueDefinition.key == value_key).one_or_none()
    trigger_label = vd_for_label.label_en if vd_for_label else value_key

    _recalc_after_override(
        db, company_id, value_key, effective_period_type, effective_period_year, trigger_label,
    )
    db.commit()
    db.refresh(result_cv)
    return result_cv
