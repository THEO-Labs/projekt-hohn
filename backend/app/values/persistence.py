"""Zentrale Schreibpfad-Invarianten fuer company_values.

Jeder Pfad, der Werte persistiert (FY-Refresh, Manual-Override, Claude-
Research, Two-Stage-Pipeline, Backfill-Skripte), muss durch diese
Funktionen — sonst haengt die Datenqualitaet einer Zeile davon ab,
welcher Pfad sie geschrieben hat.
"""

import logging
from decimal import Decimal

from app.values.currency_keys import CURRENCY_KEYS
from app.values.sign_keys import ALWAYS_POSITIVE_KEYS

logger = logging.getLogger(__name__)

# Source-Name der roten "manuell recherchieren"-Platzhalter im UI.
# Geteilt zwischen Gap-Fill (scripts/fill_gaps.py) und
# stamp_attempt_and_fill_not_found (unten).
NOT_FOUND_SOURCE = "No source found (research attempted)"


def normalize_sign(key: str, value: Decimal | None, *, context: str = "") -> Decimal | None:
    """abs() fuer ALWAYS_POSITIVE_KEYS; alle anderen Keys unveraendert.

    net_debt / eps_diluted / operating_cash_flow etc. sind bewusst NICHT in
    ALWAYS_POSITIVE_KEYS (Net-Cash-Position bzw. Verluste sind legitim).
    """
    if value is None or key not in ALWAYS_POSITIVE_KEYS or value >= 0:
        return value
    logger.info("Sign-normalising %s: %s -> %s%s",
                key, value, abs(value), f" ({context})" if context else "")
    return abs(value)


def currency_conflict(key: str, existing_currency: str | None, new_currency: str | None) -> bool:
    """True wenn ein Write die Waehrung einer bestehenden Zeile wechseln wuerde.

    Nur fuer CURRENCY_KEYS relevant; fehlt eine der beiden Waehrungen, gibt es
    nichts zu vergleichen. Caller entscheidet, ob er blockt oder flaggt.
    """
    return (
        key in CURRENCY_KEYS
        and existing_currency is not None
        and new_currency is not None
        and existing_currency != new_currency
    )


def adjusted_is_protected(source: str | None) -> bool:
    """True wenn die Adjusted-Felder einer Zeile authoritative sind und von
    Anker/Two-Stage/Derive nicht angefasst werden duerfen: manueller
    Adjusted-Override ('Manual') oder eine belegte Quelle — jede reine
    https-URL (SEC-8-K-Enrichment, IR-/Press-Release-Links). Unbelegt und
    damit ueberschreibbar sind nur NULL und das Two-Stage-Format
    ('quote | url', beginnt mit dem Zitat) — sonst kleben stale
    LLM-Adjusted-Werte dauerhaft. Einzige Ausnahme (liegt beim Writer):
    ersetzt ein Actual-Writer (XBRL-Anker/8-K-Bruecke) eine manuelle
    FORECAST-Zeile durch berichtete Zahlen, wird auch 'Manual'-Adjusted
    geleert — er war nur ein Schaetz-Ableger."""
    if source is None:
        return False
    return source == "Manual" or source.startswith("https://")


def stamp_attempt_and_fill_not_found(
    db,
    company_id,
    value_key: str,
    year: int,
    periods,
    currency: str | None = None,
) -> None:
    """Pro Periode: Refresh-Versuch dokumentieren statt still nichts zu tun.

    Bestehende Zeilen (paarfest: Actual UND Forecast) bekommen einen
    last_refresh_attempt-Stempel; Perioden ganz ohne Zeile einen
    not_found-Platzhalter (primary_method='not_found'), damit das UI die
    Zelle rot markieren kann (= manuell raussuchen).
    """
    from datetime import datetime, timezone
    from uuid import uuid4

    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.values.models import CompanyValue

    periods = list(periods)
    if not periods:
        return
    now = datetime.now(timezone.utc)
    rows = db.execute(select(CompanyValue).where(
        CompanyValue.company_id == company_id,
        CompanyValue.value_key == value_key,
        CompanyValue.period_year == year,
        CompanyValue.period_type.in_(periods),
    )).scalars().all()
    for row in rows:
        row.last_refresh_attempt = now
    present = {row.period_type for row in rows}
    for pt in periods:
        if pt in present:
            continue
        placeholder = CompanyValue(
            id=uuid4(), company_id=company_id, value_key=value_key,
            period_type=pt, period_year=year, numeric_value=None,
            source_name=NOT_FOUND_SOURCE,
            primary_method="not_found", currency=currency,
            fetched_at=now, last_refresh_attempt=now,
            manually_overridden=False, from_ir_pdf=False,
        )
        # SAVEPOINT pro Insert: Unique-Index-Kollision (Race mit parallelem
        # Writer) -> Platzhalter ueberspringen statt Transaktion zu killen.
        try:
            with db.begin_nested():
                db.add(placeholder)
                db.flush()
        except IntegrityError:
            logger.warning(
                "not_found-Platzhalter %s/%s/%s FY%s existiert bereits (Race) — skip",
                company_id, value_key, pt, year,
            )
    db.flush()
