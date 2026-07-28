"""XBRL-Provider-Anker fuer abgeschlossene Geschaeftsjahre.

Der FY-Refresh laeuft ueber die Two-Stage-LLM-Pipeline (Websuche). Fuer
bereits abgeschlossene Jahre sind die strukturierten XBRL-Provider
(EDGAR fuer US, ESEF fuer EU) die verlaesslichere Quelle: maschinen-
lesbare Filings schlagen LLM-Recherche. Dieses Modul ueberschreibt nach
dem Two-Stage-Apply die FY-Zeile mit dem Provider-Wert, sofern die
Provider-Kette einen liefert.

Invarianten wie im Refresh-Schreibpfad (routes._process_one_key):
Sign-Normalisierung via persistence.normalize_sign, Currency-Konflikt
blockt den Write (nur last_refresh_attempt wird gestempelt),
manually_overridden/from_ir_pdf-Zeilen bleiben unangetastet.
"""
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.providers.registry import get_providers
from app.values.models import CompanyValue
from app.values.persistence import currency_conflict, normalize_sign

logger = logging.getLogger(__name__)


def _fy_is_closed(company, year: int) -> bool:
    """True wenn das Geschaeftsjahr `year` bereits beendet ist.

    Mit FY-Ende aus den Stammdaten wenn vorhanden (z.B. Apple: Ende
    September), sonst Kalenderjahr-Heuristik (year < aktuelles Jahr).
    """
    today = date.today()
    m = getattr(company, "fiscal_year_end_month", None)
    d = getattr(company, "fiscal_year_end_day", None)
    if m and d:
        try:
            fy_end = date(year, m, d)
        except ValueError:
            # 29.02. in Nicht-Schaltjahr — konservativ auf den 28. runden
            fy_end = date(year, m, 28)
        return fy_end < today
    return year < today.year


def _fetch_from_chain(company, key: str, year: int):
    """Provider-Kette wie routes._try_providers: kaskadierende kwargs fuer
    die unterschiedlichen fetch-Signaturen (ESEF braucht isin, EDGAR das
    FY-Ende, Yahoo nur die Minimal-Signatur)."""
    fy_end_month = getattr(company, "fiscal_year_end_month", None)
    fy_end_day = getattr(company, "fiscal_year_end_day", None)
    isin = getattr(company, "isin", None)
    for provider in get_providers(key):
        try:
            result = None
            for kwargs in (
                {"fy_end_month": fy_end_month, "fy_end_day": fy_end_day, "isin": isin},
                {"fy_end_month": fy_end_month, "fy_end_day": fy_end_day},
                {},
            ):
                try:
                    result = provider.fetch(company.ticker, key, "FY", year, **kwargs)
                    break
                except TypeError:
                    continue
            if result is not None:
                return result
        except Exception as e:
            logger.warning(
                "Provider-Anker fetch failed for %s/%s/FY%s via %s: %s",
                company.ticker, key, year, getattr(provider, "name", provider), e,
            )
    return None


def anchor_fy_with_provider(db, company, key: str, year: int) -> bool:
    """Ueberschreibt die FY-Zeile in company_values mit dem strukturierten
    Provider-Wert (XBRL schlaegt LLM als FY-Anker).

    Nur fuer abgeschlossene Geschaeftsjahre; laufende Jahre gehoeren der
    Estimate-/Two-Stage-Pipeline. Rueckgabe True wenn geschrieben wurde.
    """
    if not _fy_is_closed(company, year):
        return False

    rows = (
        db.query(CompanyValue)
        .filter(
            CompanyValue.company_id == company.id,
            CompanyValue.value_key == key,
            CompanyValue.period_type == "FY",
            CompanyValue.period_year == year,
        )
        .all()
    )
    # Actual-Zeile bevorzugen falls parallel noch eine Alt-Forecast-Zeile
    # aus der Zeit existiert, als das Jahr noch lief.
    row = next((r for r in rows if not r.is_forecast), rows[0] if rows else None)

    # Manual-Override und PDF-Werte sind authoritative — nicht anfassen,
    # auch keinen Provider-Call verschwenden.
    if row is not None and (row.manually_overridden or row.from_ir_pdf):
        return False

    result = _fetch_from_chain(company, key, year)
    if result is None or not isinstance(result.value, Decimal):
        return False

    now = datetime.now(timezone.utc)
    if row is not None and currency_conflict(key, row.currency, result.currency):
        # Harter Reject wie im Refresh-Pfad: alter Wert bleibt, Versuch
        # wird gestempelt damit das Frontend Stale anzeigen kann.
        logger.warning(
            "Provider-Anker currency mismatch BLOCKED %s/%s/FY%s: existing=%s new=%s (source=%s)",
            company.ticker, key, year, row.currency, result.currency, result.source_name,
        )
        row.last_refresh_attempt = now
        db.flush()
        return False

    if row is None and currency_conflict(
        key, getattr(company, "currency", None), result.currency
    ):
        # Neuanlage: keine bestehende Zeile als Referenz, aber die Company-
        # Waehrung. Weicht der Provider davon ab, wird NICHT geschrieben —
        # sonst landet z.B. ein USD-Wert kommentarlos in einer EUR-Firma.
        logger.warning(
            "Provider-Anker currency mismatch on create BLOCKED %s/%s/FY%s: company=%s new=%s (source=%s)",
            company.ticker, key, year, company.currency, result.currency, result.source_name,
        )
        return False

    value = normalize_sign(
        key, result.value,
        context=f"provider-anchor {company.ticker}/FY{year} source={result.source_name}",
    )

    if row is None:
        row = CompanyValue(
            id=uuid4(), company_id=company.id, value_key=key,
            period_type="FY", period_year=year,
        )
        db.add(row)

    row.numeric_value = value
    row.text_value = None
    # Currency nur setzen wenn der Provider eine liefert — sonst bestehendes
    # Label (z.B. aus dem Two-Stage-Write) erhalten.
    if result.currency is not None:
        row.currency = result.currency
    row.source_name = result.source_name
    row.source_link = result.source_link
    row.primary_method = "provider"
    row.is_forecast = False
    row.from_ir_pdf = False
    row.manually_overridden = False
    row.fetched_at = now
    row.last_refresh_attempt = now
    db.flush()
    logger.info(
        "Provider-Anker wrote %s/%s/FY%s = %s (source=%s)",
        company.ticker, key, year, value, result.source_name,
    )
    return True
