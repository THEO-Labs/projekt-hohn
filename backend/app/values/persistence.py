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
# Geteilt zwischen Gap-Fill (scripts/fill_gaps.py) und dem Two-Stage-
# Platzhalter-Pfad (scripts/two_stage_research.py).
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


# 8-K-Enrichment-Quellen (adjusted_enrichment.py) sind SEC-Archiv-URLs.
ADJUSTED_PROTECTED_URL_PREFIX = "https://www.sec.gov/"


def adjusted_is_protected(source: str | None) -> bool:
    """True wenn die Adjusted-Felder einer Zeile authoritative sind und von
    Anker/Two-Stage/Derive nicht angefasst werden duerfen: manueller
    Adjusted-Override ('Manual') oder 8-K-Enrichment (SEC-URL). Alle
    anderen Sources (Two-Stage 'quote | url', NULL) sind ueberschreibbar —
    sonst kleben stale LLM-Adjusted-Werte dauerhaft."""
    if source is None:
        return False
    return source == "Manual" or source.startswith(ADJUSTED_PROTECTED_URL_PREFIX)
