import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.models import User
from app.companies.models import Company
from app.db import SessionLocal, get_db
from app.ir_documents.extraction import extract_values_from_pdf
from app.ir_documents.models import (
    DocumentType,
    ExtractionStatus,
    IRDocument,
    PeriodCoverage,
)
from app.ir_documents.queue import queue_position, wake_worker
from app.ir_documents.schemas import IRDocumentOut, IRDocumentUpdate
from app.ir_documents.storage import (
    delete_file,
    ensure_root,
    storage_path_for,
    write_bytes,
)
from app.portfolios.models import Portfolio
from app.values.models import CompanyValue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/companies", tags=["ir-documents"])

MAX_PDF_SIZE = 50 * 1024 * 1024  # 50 MB


def _get_owned_company(db: Session, user: User, company_id: UUID) -> Company:
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    portfolio = db.query(Portfolio).filter(Portfolio.id == company.portfolio_id).one_or_none()
    if not portfolio or portfolio.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company


def _post_extraction_web_fallback(
    db: Session,
    doc: IRDocument,
    results: dict[str, dict],
    company: Company,
    source_link: str,
) -> tuple[int, dict[str, dict]]:
    """Für jeden Key in results mit value=None: Claude-Web-Recherche versuchen
    und bei Erfolg die CompanyValue-Row mit dem Web-Wert auffüllen.

    Source-Name wird zu 'PDF leer → Claude-Recherche: <quelle>' damit der
    User im Drilldown sieht dass der Wert nicht aus dem PDF kam.

    Returns:
        (count, fallback_results) — count ist Anzahl ausgefuellter Werte,
        fallback_results ist {key: {value, source, fallback_via_web=True}}
        damit der Caller das extraction_results-JSON enrichen kann fuer
        die Frontend-Counter (sonst zeigt das UI '6/7' obwohl DB '7/7' hat).
    """
    from app.config import settings
    if not settings.anthropic_api_key:
        return 0
    from app.llm.claude import research_value, validate_claude_value
    from app.values.currency_keys import CURRENCY_KEYS
    from app.values.models import SourceType, ValueDefinition

    # Cap pro PDF: maximal 5 Web-Recherchen, damit ein Dokument mit vielen
    # leeren Keys nicht 7 × Anthropic-Calls (~$0.50) und 1-2min Latenz frisst.
    MAX_WEB_FALLBACKS_PER_PDF = 5
    period_type = doc.period_coverage.value
    period_year = doc.period_year
    filled = 0
    attempted = 0
    fallback_results: dict[str, dict] = {}

    # Priority: wichtige Keys zuerst (im Cap-Limit). net_income > fcf > net_debt
    # > sbc > buyback_volume > dividends > shares_outstanding. Alle nicht-
    # priorisierten Keys danach in Default-Reihenfolge.
    KEY_PRIORITY = ["net_income", "fcf", "net_debt", "sbc", "buyback_volume", "dividends", "shares_outstanding"]
    sorted_items = sorted(
        results.items(),
        key=lambda kv: (KEY_PRIORITY.index(kv[0]) if kv[0] in KEY_PRIORITY else len(KEY_PRIORITY))
    )
    # Adjusted-relevante Keys: fuer diese triggern wir Web-Fallback auch dann,
    # wenn 'value' (Reported) da ist aber 'value_adjusted' fehlt — der Dual-
    # Web-Call holt Adjusted automatisch mit, kostet 0 zusaetzliche Calls
    # gegenueber 'nur Reported'.
    ADJUSTED_RELEVANT = {"net_income", "ebitda", "fcf"}
    for key, info in sorted_items:
        if attempted >= MAX_WEB_FALLBACKS_PER_PDF:
            logger.info("Web-fallback cap (%d) reached for doc %s, skipping further keys",
                        MAX_WEB_FALLBACKS_PER_PDF, doc.id)
            break
        has_reported = info.get("value") is not None
        has_adjusted = info.get("value_adjusted") is not None
        needs_adjusted_lookup = key in ADJUSTED_RELEVANT and not has_adjusted
        if has_reported and not needs_adjusted_lookup:
            continue  # alles da, nichts nachfuellen
        vd = db.query(ValueDefinition).filter(ValueDefinition.key == key).one_or_none()
        if vd is None or vd.source_type != SourceType.API:
            continue
        # Idempotenz: wenn vorher schon ein Web-Wert geschrieben wurde
        # (source_name beginnt mit 'PDF leer'), nicht erneut Claude triggern
        # bei Re-Extraktion. Spart Cost bei iterativen User-Tests.
        existing_web = (
            db.query(CompanyValue)
            .filter(
                CompanyValue.company_id == company.id,
                CompanyValue.value_key == key,
                CompanyValue.period_type == period_type,
                CompanyValue.period_year == period_year,
                CompanyValue.is_forecast.is_(False),
                CompanyValue.from_ir_pdf.is_(False),
                CompanyValue.numeric_value.isnot(None),
                CompanyValue.source_name.like("PDF leer%"),
            )
            .one_or_none()
        )
        if existing_web is not None:
            # Skip nur wenn der existing Web-Wert auch alle Adjusted-Felder
            # abdeckt (oder Key kein Adjusted braucht). Sonst Claude erneut
            # rufen damit Adjusted nachgeholt wird.
            adj_complete = (key not in ADJUSTED_RELEVANT) or (existing_web.numeric_value_adjusted is not None)
            if adj_complete:
                logger.info("Web-fallback %s/%s/%s/%s: skip Claude-Call — schon ein Web-Fallback-Wert vorhanden (%s), aber merge in extraction_results",
                            company.ticker, key, period_type, period_year, existing_web.numeric_value)
                fallback_results[key] = {
                    "value": str(existing_web.numeric_value),
                    "value_adjusted": str(existing_web.numeric_value_adjusted) if existing_web.numeric_value_adjusted is not None else None,
                    "adjustments_note": existing_web.adjustments_note,
                    "adjustments_source": existing_web.adjustments_source,
                    "currency": existing_web.currency,
                    "source": (existing_web.source_name or "")[:512],
                    "fallback_via_web": True,
                }
                continue
            else:
                logger.info("Web-fallback %s/%s/%s/%s: existing Web-Wert hat kein Adjusted — Claude-Call fuer Adjusted-Nachhol",
                            company.ticker, key, period_type, period_year)

        label = f"{vd.label_en} ({vd.label_de})"
        attempted += 1
        from app.llm.research import research_value_dual
        try:
            dual = research_value_dual(
                company.name, company.ticker, label, company.currency,
                period_type=period_type, period_year=period_year, value_key=key,
            )
            web_val = dual.value
            source = dual.source
            url = dual.url
        except Exception as e:
            logger.warning("Post-extraction web fallback %s/%s/%s/%s failed: %s",
                           company.ticker, key, period_type, period_year, e)
            continue

        if web_val is None:
            logger.info("Web-fallback %s/%s/%s/%s: NO VALUE (Claude+Gemini lieferten beide nichts)",
                        company.ticker, key, period_type, period_year)
            continue
        # dual.value ist bereits validiert von beiden Providern. Kein zusaetzlicher
        # validate_claude_value-Call noetig.
        # Sign-norm für ALWAYS_POSITIVE_KEYS (sbc/buyback/dividends/shares):
        # Claude liefert manchmal negative Cash-Outflow-Werte (Cashflow-Konvention).
        from app.ir_documents.extraction import ALWAYS_POSITIVE_KEYS
        if key in ALWAYS_POSITIVE_KEYS and web_val < 0:
            web_val = abs(web_val)
        # Adjusted-Variante aus Dual-Research (nur fuer ni/ebitda/fcf relevant).
        web_val_adjusted = dual.value_adjusted
        web_adj_note = dual.adjustments_note
        web_adj_source = dual.adjustments_source
        if web_val_adjusted is not None and key in ALWAYS_POSITIVE_KEYS and web_val_adjusted < 0:
            web_val_adjusted = abs(web_val_adjusted)

        existing = (
            db.query(CompanyValue)
            .filter(
                CompanyValue.company_id == company.id,
                CompanyValue.value_key == key,
                CompanyValue.period_type == period_type,
                CompanyValue.period_year == period_year,
                CompanyValue.is_forecast.is_(False),
            )
            .one_or_none()
        )
        if existing and existing.manually_overridden:
            continue
        original_reason = (info.get("reason") or "")[:80]
        new_source = f"PDF leer ({original_reason}) → Claude-Recherche: {source}" if source else "PDF leer → Claude-Recherche"
        currency = company.currency if key in CURRENCY_KEYS else None
        now = datetime.now(timezone.utc)
        # SAVEPOINT pro Iteration: bei UniqueViolation (z.B. Race-Condition mit
        # einer parallel eingefuegten Row die der existing-SELECT nicht gesehen
        # hat) rollback NUR diesen Eintrag und versuche den Update-Pfad.
        # Sonst killt ein Conflict bei einem Key alle vorherigen Web-Fallbacks
        # in derselben Transaction.
        try:
            with db.begin_nested():
                if existing:
                    # ADJUSTED-ONLY-MODUS: PDF lieferte Reported (existing.numeric_value
                    # ist da), wir holen nur Adjusted nach. Reported NICHT ueberschreiben.
                    if has_reported and existing.numeric_value is not None and web_val_adjusted is not None:
                        existing.numeric_value_adjusted = web_val_adjusted
                        existing.adjustments_note = (web_adj_note or "")[:4000] or None
                        existing.adjustments_source = (web_adj_source or "")[:2048] or None
                        # source_name behalten — Reported-Source bleibt fuehrend.
                        # Optional ergänzen: '+ Web-Adjusted'
                        if existing.source_name and " + Web-Adjusted" not in existing.source_name:
                            existing.source_name = (existing.source_name + " + Web-Adjusted")[:1900]
                        existing.fetched_at = now
                    else:
                        # Standardpfad: Reported aus Web (PDF lieferte gar nichts).
                        existing.numeric_value = web_val
                        existing.source_name = new_source[:1900]
                        existing.source_link = url or existing.source_link
                        existing.currency = currency or existing.currency
                        existing.fetched_at = now
                        existing.from_ir_pdf = False
                        if web_val_adjusted is not None:
                            existing.numeric_value_adjusted = web_val_adjusted
                            existing.adjustments_note = (web_adj_note or "")[:4000] or None
                            existing.adjustments_source = (web_adj_source or "")[:2048] or None
                else:
                    db.add(CompanyValue(
                        id=uuid4(),
                        company_id=company.id,
                        value_key=key,
                        period_type=period_type,
                        period_year=period_year,
                        numeric_value=web_val,
                        numeric_value_adjusted=web_val_adjusted,
                        adjustments_note=(web_adj_note or "")[:4000] or None,
                        adjustments_source=(web_adj_source or "")[:2048] or None,
                        source_name=new_source[:1900],
                        source_link=url or source_link,
                        currency=currency,
                        fetched_at=now,
                        from_ir_pdf=False,
                    ))
                    db.flush()  # zwingt IntegrityError im SAVEPOINT
        except IntegrityError as ie:
            logger.warning("Web-fallback %s/%s/%s/%s: SELECT verfehlte existing Row, retry "
                           "via Reload + Update (Race-Condition?): %s",
                           company.ticker, key, period_type, period_year, str(ie)[:120])
            # SAVEPOINT wurde gerollbackt. Reload existing direkt und update.
            db.expire_all()
            retry_existing = (
                db.query(CompanyValue)
                .filter(
                    CompanyValue.company_id == company.id,
                    CompanyValue.value_key == key,
                    CompanyValue.period_type == period_type,
                    CompanyValue.period_year == period_year,
                    CompanyValue.is_forecast.is_(False),
                )
                .one_or_none()
            )
            if retry_existing is None:
                logger.error("Web-fallback %s/%s/%s/%s: konnte conflicting Row auch "
                             "nach Reload nicht finden — skip.",
                             company.ticker, key, period_type, period_year)
                continue
            if retry_existing.manually_overridden:
                continue
            retry_existing.numeric_value = web_val
            retry_existing.source_name = new_source[:1900]
            retry_existing.source_link = url or retry_existing.source_link
            retry_existing.currency = currency or retry_existing.currency
            retry_existing.fetched_at = now
            retry_existing.from_ir_pdf = False
            if web_val_adjusted is not None:
                retry_existing.numeric_value_adjusted = web_val_adjusted
                retry_existing.adjustments_note = (web_adj_note or "")[:4000] or None
                retry_existing.adjustments_source = (web_adj_source or "")[:2048] or None
        filled += 1
        # Resultat ans extraction_results-Mapping zurueckgeben damit der
        # Caller das JSON enrichen kann (UI-Counter zeigt sonst PDF-only).
        # Bei Adjusted-only-Modus (PDF hatte Reported, wir holten nur Adj):
        # Reported-Value aus dem PDF-info-dict behalten, NICHT durch web_val
        # ueberschreiben (web_val ist hier eh aus dem gleichen Search-Run
        # aber Source-of-Truth bleibt PDF fuer Reported).
        if has_reported and info.get("value") is not None:
            fallback_results[key] = {
                "value": str(info.get("value")),
                "value_adjusted": str(web_val_adjusted) if web_val_adjusted is not None else None,
                "adjustments_note": web_adj_note,
                "adjustments_source": web_adj_source,
                "currency": info.get("currency") or currency,
                "source": (info.get("source") or "PDF") + " + Web-Adjusted",
                "page": info.get("page"),
                "quote": info.get("quote"),
                "period_basis": info.get("period_basis"),
                "fallback_via_web": True,
            }
        else:
            fallback_results[key] = {
                "value": str(web_val),
                "value_adjusted": str(web_val_adjusted) if web_val_adjusted is not None else None,
                "adjustments_note": web_adj_note,
                "adjustments_source": web_adj_source,
                "currency": currency,
                "source": new_source[:1900],
                "fallback_via_web": True,
            }
        logger.info("Web-fallback filled %s/%s/%s/%s = %s adj=%s (src=%s)",
                    company.ticker, key, period_type, period_year,
                    web_val if not has_reported else "(PDF-Reported)",
                    web_val_adjusted, source)
    return filled, fallback_results


def _run_extraction_job(doc_id: UUID, company_id: UUID) -> None:
    """Background job: pull doc + company from DB, run Claude PDF extraction,
    persist values + status. Uses its own DB session."""
    db = SessionLocal()
    try:
        doc = db.query(IRDocument).filter(IRDocument.id == doc_id).one_or_none()
        if doc is None:
            return
        company = db.query(Company).filter(Company.id == company_id).one_or_none()
        if company is None:
            return

        doc.extraction_status = ExtractionStatus.EXTRACTING
        db.commit()

        try:
            results, guidance_fy, raw = extract_values_from_pdf(
                Path(doc.storage_path),
                company_name=company.name,
                document_type=doc.document_type.value,
                period_coverage=doc.period_coverage.value,
                period_year=doc.period_year,
            )
        except Exception as e:
            logger.exception("PDF extraction failed for doc %s: %s", doc_id, e)
            doc.extraction_status = ExtractionStatus.FAILED
            doc.extraction_error = str(e)[:500]
            db.commit()
            return

        # Persist as company_values where extraction succeeded.
        # Cumulative-style storage: each successful key writes a CompanyValue with from_ir_pdf=True.
        # Period-coverage maps directly to period_type (FY or Q1/Q2/...).
        json_safe_results: dict[str, dict] = {}
        for key, info in results.items():
            json_safe_results[key] = {
                k: (str(v) if isinstance(v, Decimal) else v)
                for k, v in info.items()
            }
        period_type = doc.period_coverage.value  # "FY" or "Q1" etc.
        period_year = doc.period_year
        source_link = f"/api/companies/{company_id}/ir-documents/{doc_id}/download"
        # Stammdaten-Anker-Konvention: shares_outstanding aus einem Annual Report
        # ist semantisch ein 31.12.N-Snapshot (= Anfang FY (N+1)). Wir speichern
        # es deshalb unter period_year=(N+1), damit market_cap/stock_price/shares
        # einheitlich den FY-Anker des Folgejahres referenzieren.
        is_annual_report = (
            period_type == "FY"
            and doc.document_type.value in ("ANNUAL_REPORT", "FORM_10K", "FORM_20F")
        )
        # Quartalsberichte fuer shares: bleiben period_year=N (Q-Snapshot).
        # Aufraeumen: alte 'falsch' platzierte shares-Row aus frueheren AR-Extracts
        # (= End-of-FY-N unter period_year=N) loeschen, damit der neue Anker-Wert
        # (period_year=N+1) eindeutig wird und market_cap_calc-Logik konsistent ist.
        if (
            period_type == "FY"
            and doc.document_type.value in ("ANNUAL_REPORT", "FORM_10K", "FORM_20F")
        ):
            db.query(CompanyValue).filter(
                CompanyValue.company_id == company_id,
                CompanyValue.value_key == "shares_outstanding",
                CompanyValue.period_type == "FY",
                CompanyValue.period_year == period_year,
                CompanyValue.from_ir_pdf.is_(True),
                CompanyValue.manually_overridden.is_(False),
            ).delete(synchronize_session=False)

        for key, info in results.items():
            value = info.get("value")
            page = info.get("page")
            currency = info.get("currency")
            reason = info.get("reason") or "im Bericht nicht gefunden"
            now = datetime.now(timezone.utc)

            # shares_outstanding aus Annual Report -> Anker fuer FY (N+1).
            target_period_year = period_year
            target_period_type = period_type
            if key == "shares_outstanding" and is_annual_report:
                target_period_year = period_year + 1

            existing = (
                db.query(CompanyValue)
                .filter(
                    CompanyValue.company_id == company_id,
                    CompanyValue.value_key == key,
                    CompanyValue.period_type == target_period_type,
                    CompanyValue.period_year == target_period_year,
                    CompanyValue.is_forecast.is_(False),
                )
                .one_or_none()
            )
            if existing and existing.manually_overridden:
                continue  # never overwrite manual

            # Adjusted/Underlying-Werte aus PDF (nur fuer NI/EBITDA/FCF).
            value_adjusted = info.get("value_adjusted") if isinstance(info.get("value_adjusted"), Decimal) else None
            adjustments_note = info.get("adjustments_note")
            adjustments_source = info.get("adjustments_source")
            if isinstance(value, Decimal):
                source_name = f"PDF: {doc.display_name}" + (f" (S.{page})" if page else "")
                if existing:
                    existing.numeric_value = value
                    existing.source_name = source_name
                    existing.source_link = source_link
                    existing.currency = currency
                    existing.fetched_at = now
                    existing.from_ir_pdf = True
                    if value_adjusted is not None:
                        existing.numeric_value_adjusted = value_adjusted
                        existing.adjustments_note = (adjustments_note or "")[:4000] or None
                        existing.adjustments_source = (adjustments_source or "")[:2048] or None
                else:
                    db.add(CompanyValue(
                        id=uuid4(),
                        company_id=company_id,
                        value_key=key,
                        period_type=target_period_type,
                        period_year=target_period_year,
                        numeric_value=value,
                        numeric_value_adjusted=value_adjusted,
                        adjustments_note=(adjustments_note or "")[:4000] or None,
                        adjustments_source=(adjustments_source or "")[:2048] or None,
                        source_name=source_name,
                        source_link=source_link,
                        currency=currency,
                        fetched_at=now,
                        from_ir_pdf=True,
                    ))
            else:
                # Claude tried but found no value — record an explanatory placeholder
                # so the user sees "PDF analyzed, value not found: <reason>" instead
                # of an empty cell.
                source_name = f"PDF: {doc.display_name} — kein Wert: {reason[:120]}"
                if existing:
                    if existing.from_ir_pdf:
                        # Only overwrite previous PDF-source entries, not API-fetched values
                        existing.numeric_value = None
                        existing.source_name = source_name
                        existing.source_link = source_link
                        existing.fetched_at = now
                else:
                    db.add(CompanyValue(
                        id=uuid4(),
                        company_id=company_id,
                        value_key=key,
                        period_type=target_period_type,
                        period_year=target_period_year,
                        numeric_value=None,
                        source_name=source_name,
                        source_link=source_link,
                        currency=currency,
                        fetched_at=now,
                        from_ir_pdf=True,
                    ))

        # Guidance-Werte als FY-Forecast-Rows mappen.
        #  - Q-Berichte: Guidance gilt für FY der gleichen period_year (Q1 2026 → FY2026)
        #  - Annual Report: 'Outlook for next year' → FY = period_year + 1 (AR2025 → FY2026)
        guidance_target_fy: int | None = None
        if period_type in ("Q1", "Q2", "Q3", "Q4", "H1", "H2"):
            guidance_target_fy = period_year
        elif period_type == "FY" and doc.document_type.value in (
            "ANNUAL_REPORT", "FORM_10K", "FORM_20F",
        ):
            guidance_target_fy = period_year + 1
        if guidance_target_fy is not None and guidance_fy:
            _persist_guidance_as_fy_forecast(
                db, company_id=company_id, doc=doc,
                fy_target=guidance_target_fy,
                guidance=guidance_fy,
                source_link=source_link,
            )
            json_safe_results["_guidance_fy"] = {
                "target_fy": guidance_target_fy,
                "values": {
                    k: {kk: (str(vv) if isinstance(vv, Decimal) else vv) for kk, vv in v.items()}
                    for k, v in guidance_fy.items()
                },
            }

        # Auto-Web-Fallback: für jeden None-Key in den Results probiert Claude
        # Web-Recherche und füllt die Row falls erfolgreich. Source wird zu
        # 'PDF leer (<reason>) → Claude-Recherche: <quelle>'.
        # Web-Fallback-Werte werden danach ins json_safe_results gemerged,
        # damit der UI-Counter (zeigt ueber extraction_results) auch die
        # Web-Fallback-Erfolge mitzaehlt — sonst sieht User '6/7' obwohl
        # die DB tatsaechlich '7/7' hat.
        try:
            filled, fallback_results = _post_extraction_web_fallback(
                db, doc=doc, results=results, company=company, source_link=source_link,
            )
            if filled:
                logger.info("Post-extraction web-fallback filled %d values for doc %s",
                            filled, doc_id)
                # Merge Web-Fallback-Werte in das JSON, damit das Frontend
                # die Web-Werte als 'extracted' zaehlt und im UI sichtbar werden.
                for fb_key, fb_info in fallback_results.items():
                    json_safe_results[fb_key] = fb_info
                db.commit()
        except Exception as e:
            logger.exception("Post-extraction web-fallback failed for doc %s: %s", doc_id, e)
            db.rollback()

        # Silent-DONE-Schutz: wenn beide Pässe kein JSON parsen konnten, bleibt
        # results={} und wir hätten 0 Werte mit status=DONE — irrefuehrend.
        # Stattdessen FAILED setzen damit User sieht dass die Extraktion broken war.
        if not results:
            doc.extraction_status = ExtractionStatus.FAILED
            doc.extracted_at = datetime.now(timezone.utc)
            doc.extraction_error = (
                "JSON parse failed in both extraction passes — Claude lieferte keine "
                "valide JSON-Antwort. PDF eventuell beschaedigt, zu komplex oder "
                "Claude-API-Quirk. Re-Extract nochmal versuchen."
            )
            db.commit()
            return
        doc.extraction_status = ExtractionStatus.DONE
        doc.extracted_at = datetime.now(timezone.utc)
        doc.extraction_results = json_safe_results
        doc.extraction_error = None
        db.commit()

        # Trigger downstream recalc — same FY (fcf_yield, ni_growth etc.)
        # plus +1 FY because ni_growth(N+1) and net_debt_change(N+1) depend on N.
        if period_type == "FY":
            from app.values.routes import _fy_year_has_data, _run_and_persist_calculations
            try:
                _run_and_persist_calculations(db, company_id, "FY", period_year)
                if _fy_year_has_data(db, company_id, period_year + 1):
                    _run_and_persist_calculations(db, company_id, "FY", period_year + 1)
                db.commit()
            except Exception as e:
                logger.exception("Post-PDF recalc failed for doc %s: %s", doc_id, e)
                db.rollback()
        elif period_type in ("Q1", "Q2", "Q3", "Q4"):
            # Quarterly upload → refresh the running-FY estimate so factor-based
            # values pick up the new quarter immediately without manual click.
            try:
                _trigger_estimate_refresh_for_running_fy(db, company_id, period_year)
                db.commit()
            except Exception as e:
                logger.exception("Post-Q-PDF estimate refresh failed for doc %s: %s", doc_id, e)
                db.rollback()
    finally:
        db.close()


def _persist_guidance_as_fy_forecast(
    db,
    *,
    company_id,
    doc,
    fy_target: int,
    guidance: dict[str, dict],
    source_link: str,
) -> None:
    """Schreibt Guidance-Werte aus einem Q-PDF als FY-Forecast-Rows.

    is_forecast=True, from_ir_pdf=True, source_name='Guidance from Q1 2026 (S.X)'.
    Späterer Q-Upload überschreibt (gleiche Row), späterer AR-Upload
    überschreibt mit Actuals (is_forecast=False).
    Manuelle Overrides werden nicht angefasst.
    """
    now = datetime.now(timezone.utc)
    written = 0
    for key, info in guidance.items():
        value = info.get("value")
        if not isinstance(value, Decimal):
            continue
        page = info.get("page")
        currency = info.get("currency")

        existing = (
            db.query(CompanyValue)
            .filter(
                CompanyValue.company_id == company_id,
                CompanyValue.value_key == key,
                CompanyValue.period_type == "FY",
                CompanyValue.period_year == fy_target,
                CompanyValue.is_forecast.is_(True),
            )
            .one_or_none()
        )
        if existing and existing.manually_overridden:
            continue
        # Wenn ein NICHT-forecast Wert (Actuals aus AR) schon existiert, NICHT
        # mit Guidance überschreiben — Actuals trumpfen Guidance.
        actuals_exists = (
            db.query(CompanyValue.id)
            .filter(
                CompanyValue.company_id == company_id,
                CompanyValue.value_key == key,
                CompanyValue.period_type == "FY",
                CompanyValue.period_year == fy_target,
                CompanyValue.is_forecast.is_(False),
            )
            .first()
            is not None
        )
        if actuals_exists:
            continue

        source_name = f"Guidance from {doc.display_name}" + (f" (S.{page})" if page else "")
        try:
            with db.begin_nested():
                if existing:
                    existing.numeric_value = value
                    existing.source_name = source_name
                    existing.source_link = source_link
                    existing.currency = currency
                    existing.fetched_at = now
                    existing.from_ir_pdf = True
                    existing.is_forecast = True
                else:
                    db.add(CompanyValue(
                        id=uuid4(),
                        company_id=company_id,
                        value_key=key,
                        period_type="FY",
                        period_year=fy_target,
                        numeric_value=value,
                        source_name=source_name,
                        source_link=source_link,
                        currency=currency,
                        fetched_at=now,
                        from_ir_pdf=True,
                        is_forecast=True,
                    ))
                    db.flush()
            written += 1
        except IntegrityError as ie:
            logger.warning(
                "Guidance-Persist %s/%s/FY%s: UniqueViolation (race?), retry via reload+update: %s",
                doc.display_name, key, fy_target, str(ie)[:160],
            )
            db.expire_all()
            retry = (
                db.query(CompanyValue)
                .filter(
                    CompanyValue.company_id == company_id,
                    CompanyValue.value_key == key,
                    CompanyValue.period_type == "FY",
                    CompanyValue.period_year == fy_target,
                    CompanyValue.is_forecast.is_(True),
                )
                .one_or_none()
            )
            if retry is None or retry.manually_overridden:
                continue
            retry.numeric_value = value
            retry.source_name = source_name
            retry.source_link = source_link
            retry.currency = currency
            retry.fetched_at = now
            retry.from_ir_pdf = True
            written += 1
    if written:
        logger.info("Persisted %d guidance values as FY%s forecast for company=%s",
                    written, fy_target, company_id)


def _trigger_estimate_refresh_for_running_fy(db, company_id, q_period_year: int) -> None:
    """Re-runs the API path for every estimable FY-key for `q_period_year`,
    so the new quarterly value flows into the FY estimate. Only runs if
    q_period_year is the running FY (>= current calendar year). Also recalcs
    FY+1 since cross-year metrics (ni_growth, net_debt_change) depend on
    the updated FY values."""
    from datetime import date as _date_today
    if q_period_year < _date_today.today().year:
        return
    from app.values.routes import _process_one_key, _run_and_persist_calculations, _fy_year_has_data
    from app.calculations.estimates import ESTIMABLE_KEYS
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if company is None:
        return

    class _Payload:
        period_type = "FY"
        period_year = q_period_year

    payload = _Payload()
    updated: list = []
    # Iterate over a sorted view so logging / behaviour is deterministic.
    for k in sorted(ESTIMABLE_KEYS):
        # Per-key SAVEPOINT so one failure doesn't roll back the previously-
        # written keys.
        try:
            with db.begin_nested():
                _process_one_key(db, k, company.ticker, company, company_id, payload, updated)
        except Exception as e:
            logger.warning("Estimate refresh key=%s failed: %s", k, e)
    try:
        _run_and_persist_calculations(db, company_id, "FY", q_period_year)
        # Cross-FY cascade: ni_growth and net_debt_change for FY+1 depend on
        # the (now updated) FY values, so recalc the next year too.
        if _fy_year_has_data(db, company_id, q_period_year + 1):
            _run_and_persist_calculations(db, company_id, "FY", q_period_year + 1)
    except Exception as e:
        logger.warning("Estimate refresh calc failed: %s", e)
        db.rollback()


@router.post("/{company_id}/ir-documents", response_model=IRDocumentOut, status_code=201)
async def upload_ir_document(
    company_id: UUID,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    period_coverage: str = Form(...),
    period_year: int = Form(...),
    display_name: str = Form(...),
    notes: str | None = Form(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> IRDocument:
    _get_owned_company(db, user, company_id)

    try:
        doc_type = DocumentType(document_type)
        period_cov = PeriodCoverage(period_coverage)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid enum value: {e}")

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files allowed")

    contents = await file.read()
    if len(contents) > MAX_PDF_SIZE:
        raise HTTPException(status_code=413, detail=f"PDF exceeds max size of {MAX_PDF_SIZE // 1024 // 1024} MB")
    if len(contents) < 100:
        raise HTTPException(status_code=400, detail="Uploaded file looks empty/corrupted")

    ensure_root()
    doc_id = uuid4()
    path = storage_path_for(company_id, doc_id, file.filename)
    write_bytes(path, contents)

    doc = IRDocument(
        id=doc_id,
        company_id=company_id,
        document_type=doc_type,
        period_coverage=period_cov,
        period_year=period_year,
        display_name=display_name,
        original_filename=file.filename,
        storage_path=str(path),
        size_bytes=len(contents),
        mime_type=file.content_type or "application/pdf",
        uploaded_at=datetime.now(timezone.utc),
        uploaded_by_user_id=user.id,
        extraction_status=ExtractionStatus.PENDING,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    wake_worker()
    return _attach_queue_position(doc)


@router.post("/{company_id}/ir-documents/{doc_id}/extract", response_model=IRDocumentOut)
def trigger_extraction(
    company_id: UUID,
    doc_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> IRDocument:
    _get_owned_company(db, user, company_id)
    doc = db.query(IRDocument).filter(IRDocument.id == doc_id, IRDocument.company_id == company_id).one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    doc.extraction_status = ExtractionStatus.PENDING
    doc.extraction_error = None
    db.commit()
    db.refresh(doc)
    wake_worker()
    return _attach_queue_position(doc)


def _attach_queue_position(doc: IRDocument) -> IRDocument:
    """Compute queue_position once and stick it on the ORM instance for serialisation."""
    setattr(doc, "queue_position", queue_position(doc))
    return doc


@router.get("/{company_id}/ir-documents", response_model=list[IRDocumentOut])
def list_ir_documents(
    company_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[IRDocument]:
    _get_owned_company(db, user, company_id)
    rows = (
        db.query(IRDocument)
        .filter(IRDocument.company_id == company_id)
        .order_by(IRDocument.period_year.desc(), IRDocument.period_coverage, IRDocument.uploaded_at.desc())
        .all()
    )
    return [_attach_queue_position(r) for r in rows]


@router.patch("/{company_id}/ir-documents/{doc_id}", response_model=IRDocumentOut)
def update_ir_document(
    company_id: UUID,
    doc_id: UUID,
    payload: IRDocumentUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> IRDocument:
    _get_owned_company(db, user, company_id)
    doc = db.query(IRDocument).filter(IRDocument.id == doc_id, IRDocument.company_id == company_id).one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    data = payload.model_dump(exclude_unset=True)
    if "document_type" in data and data["document_type"] is not None:
        try:
            doc.document_type = DocumentType(data.pop("document_type"))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid document_type: {e}")
    if "period_coverage" in data and data["period_coverage"] is not None:
        try:
            doc.period_coverage = PeriodCoverage(data.pop("period_coverage"))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid period_coverage: {e}")
    for key, value in data.items():
        if value is not None:
            setattr(doc, key, value)
    db.commit()
    db.refresh(doc)
    return doc


@router.delete("/{company_id}/ir-documents/{doc_id}", status_code=204)
def delete_ir_document(
    company_id: UUID,
    doc_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    _get_owned_company(db, user, company_id)
    doc = db.query(IRDocument).filter(IRDocument.id == doc_id, IRDocument.company_id == company_id).one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.storage_path:
        delete_file(Path(doc.storage_path))
    db.delete(doc)
    db.commit()


@router.get("/{company_id}/ir-documents/{doc_id}/download")
def download_ir_document(
    company_id: UUID,
    doc_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    _get_owned_company(db, user, company_id)
    doc = db.query(IRDocument).filter(IRDocument.id == doc_id, IRDocument.company_id == company_id).one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    path = Path(doc.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")
    return FileResponse(
        path,
        media_type=doc.mime_type or "application/pdf",
        filename=doc.original_filename,
    )
