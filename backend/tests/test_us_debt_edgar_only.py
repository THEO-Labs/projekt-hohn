"""US-Filer: die Bilanz-Debt-Keys st_debt/lt_debt sind EDGAR-only. Der
Marktdaten-Feed (Yahoo, provider_kind='market') darf sie NICHT schreiben —
Yahoos "Long Term Debt" enthaelt Operating-/Finance-Leases (Natera/Dynatrace
haben keine Finanzschuld, nur Operating-Leases; der Feed ueberzeichnete
net_debt um 96-118 Mio). Fehlt das strikte EDGAR-Konzept, bleibt die Zelle
LEER. Nicht-US bleibt unveraendert.

Hermetisch: get_providers wird durch Fake-Provider ersetzt (kein Netz).
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.providers.base import ProviderResult


class _FakeEdgar:
    provider_kind = "filing"

    def fetch(self, ticker, key, ptype, year, **kwargs):
        return None  # EDGAR hat kein LongTermDebtNoncurrent -> leer


class _FakeMarket:
    provider_kind = "market"

    def fetch(self, ticker, key, ptype, year, **kwargs):
        return ProviderResult(
            value=Decimal("118000000"),  # Yahoos Lease-inklusiver Wert
            source_name="Marktdaten-Feed (Yahoo)",
            source_link=None,
            currency="USD",
        )


def _us_company():
    return SimpleNamespace(
        ticker="NTRA", isin="US6413401098", currency="USD",
        fiscal_year_end_month=12, fiscal_year_end_day=31,
    )


def _nonus_company():
    return SimpleNamespace(
        ticker="SAP", isin="DE0007164600", currency="EUR",
        fiscal_year_end_month=12, fiscal_year_end_day=31,
    )


# --- provider_anchor._fetch_from_chain -------------------------------------


@pytest.mark.parametrize("key", ["st_debt", "lt_debt"])
def test_us_filer_market_feed_blocked_for_debt_keys(key):
    from app.values import provider_anchor
    with patch.object(provider_anchor, "get_providers",
                      return_value=[_FakeEdgar(), _FakeMarket()]):
        res = provider_anchor._fetch_from_chain(_us_company(), key, 2024)
    assert res is None  # Yahoo darf lt_debt/st_debt nicht liefern


def test_us_filer_market_feed_allowed_for_non_debt_key():
    """Kontrast: bei einem Nicht-Debt-Key (cash) faellt die Kette fuer
    US-Filer weiterhin auf den Feed durch (EDGAR liefert None)."""
    from app.values import provider_anchor
    with patch.object(provider_anchor, "get_providers",
                      return_value=[_FakeEdgar(), _FakeMarket()]):
        res = provider_anchor._fetch_from_chain(
            _us_company(), "cash_and_equivalents", 2024
        )
    assert res is not None
    assert res.value == Decimal("118000000")


def test_nonus_filer_market_feed_blocked_for_all_fundamentals():
    """Nicht-US: der Feed ist ohnehin keine Fundamental-Quelle (skip_market)
    — auch fuer Debt-Keys leer."""
    from app.values import provider_anchor
    with patch.object(provider_anchor, "get_providers",
                      return_value=[_FakeEdgar(), _FakeMarket()]):
        res = provider_anchor._fetch_from_chain(_nonus_company(), "lt_debt", 2024)
    assert res is None


# --- routes._try_providers -------------------------------------------------


@pytest.mark.parametrize("key", ["st_debt", "lt_debt"])
def test_try_providers_us_debt_edgar_only(key):
    from app.values import routes
    payload = SimpleNamespace(period_type="FY", period_year=2024)
    with patch.object(routes, "get_providers",
                      return_value=[_FakeEdgar(), _FakeMarket()]):
        res = routes._try_providers(
            "NTRA", key, payload, 12, 31, isin="US6413401098",
            company=_us_company(),
        )
    assert res is None


def test_try_providers_us_non_debt_falls_through_to_market():
    from app.values import routes
    payload = SimpleNamespace(period_type="FY", period_year=2024)
    with patch.object(routes, "get_providers",
                      return_value=[_FakeEdgar(), _FakeMarket()]):
        res = routes._try_providers(
            "NTRA", "cash_and_equivalents", payload, 12, 31,
            isin="US6413401098", company=_us_company(),
        )
    assert res is not None
    assert res.value == Decimal("118000000")
