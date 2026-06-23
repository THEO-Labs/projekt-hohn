"""ESEF (European Single Electronic Format) provider.

Liefert strukturierte Fundamentals fuer EU-boersennotierte Filer via die
oeffentliche filings.xbrl.org-API. Funktioniert primaer fuer Non-DE-EU-
Filer (NL, FR, ES, IT, SE, DK, ...). Deutsche Filer (Munich Re, Allianz, ...)
sind in filings.xbrl.org nicht enthalten — DE nutzt Bundesanzeiger als
Pflicht-Quelle (separater Provider geplant).

Flow:
  1. ISIN -> LEI via GLEIF (https://api.gleif.org)
  2. LEI -> Filings-Liste via filings.xbrl.org/api/entities/{lei}/filings
  3. Filing fuer Ziel-FY auswaehlen (period_end matches)
  4. JSON-Download + IFRS-Taxonomy-Facts parsen
  5. Concept-Map auf unsere value_keys anwenden
"""
import logging
from decimal import Decimal

import httpx
from cachetools import TTLCache

from app.providers.base import ProviderResult
from app.values.currency_keys import CURRENCY_KEYS

logger = logging.getLogger(__name__)

USER_AGENT = "ProjektHohn/1.0 (mailto:till@theolabs.xyz)"

# IFRS-Taxonomy-Mapping unserer value_keys auf ESEF-Concepts.
# Mehrere Concepts pro Key fuer Fallback (Filer nutzen unterschiedliche Tags).
CONCEPT_MAP: dict[str, list[str]] = {
    "net_income": [
        "ifrs-full:ProfitLoss",
        "ifrs-full:ProfitLossAttributableToOwnersOfParent",
    ],
    "sbc": [
        "ifrs-full:ExpenseFromShareBasedPaymentTransactionsWithEmployees",
        "ifrs-full:IncreaseDecreaseThroughExerciseOfOptionsShareBasedPaymentArrangement",
    ],
    "buyback_volume": [
        "ifrs-full:PaymentsForRepurchaseOfTreasuryShares",
        "ifrs-full:PaymentsToAcquireOrRedeemEntitysShares",
    ],
    "dividends": [
        "ifrs-full:DividendsPaidClassifiedAsFinancingActivities",
        "ifrs-full:DividendsPaid",
    ],
    "cash_and_equivalents": [
        "ifrs-full:CashAndCashEquivalents",
    ],
    "long_term_debt": [
        "ifrs-full:NoncurrentBorrowings",
        "ifrs-full:NoncurrentInterestbearingLoansAndBorrowings",
    ],
    "lease_liabilities": [
        "ifrs-full:NoncurrentLeaseLiabilities",
        "ifrs-full:LeaseLiabilities",
    ],
}

EBITDA_EBIT_CONCEPTS = [
    "ifrs-full:ProfitLossFromOperatingActivities",
    "ifrs-full:OperatingProfit",
]
EBITDA_DA_CONCEPTS = [
    "ifrs-full:DepreciationAndAmortisationExpense",
    "ifrs-full:DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss",
]

FCF_OCF_CONCEPTS = [
    "ifrs-full:CashFlowsFromUsedInOperatingActivities",
]
FCF_CAPEX_CONCEPTS = [
    "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    "ifrs-full:PurchaseOfPropertyPlantAndEquipment",
]


class ESEFProvider:
    name = "ESEF (filings.xbrl.org)"
    supported_keys = set(CONCEPT_MAP.keys()) | {"fcf", "ebitda"}

    GLEIF_BASE = "https://api.gleif.org/api/v1"
    ESEF_BASE = "https://filings.xbrl.org"

    def __init__(self) -> None:
        self._isin_lei_cache: TTLCache = TTLCache(maxsize=500, ttl=86400)  # 24h
        self._lei_filings_cache: TTLCache = TTLCache(maxsize=200, ttl=3600)
        self._filing_json_cache: TTLCache = TTLCache(maxsize=100, ttl=3600)
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=15.0,
            follow_redirects=True,
            transport=httpx.HTTPTransport(retries=3),
        )

    def _retried_get(self, url: str, max_attempts: int = 3) -> httpx.Response | None:
        import time
        for attempt in range(max_attempts):
            try:
                r = self._client.get(url)
                if r.status_code in (429, 503) and attempt < max_attempts - 1:
                    time.sleep(2 ** attempt)
                    continue
                return r
            except Exception as e:
                if attempt == max_attempts - 1:
                    logger.warning("ESEF GET %s failed: %s", url, e)
                    return None
                time.sleep(2 ** attempt)
        return None

    def _resolve_isin_to_lei(self, isin: str) -> str | None:
        """ISIN -> LEI via GLEIF Public API. Cache 24h."""
        if not isin:
            return None
        isin = isin.strip().upper()
        if isin in self._isin_lei_cache:
            return self._isin_lei_cache[isin]
        r = self._retried_get(f"{self.GLEIF_BASE}/lei-records?filter[isin]={isin}&page[size]=1")
        if r is None or r.status_code >= 400:
            return None
        try:
            data = r.json()
            entries = data.get("data") or []
            if not entries:
                self._isin_lei_cache[isin] = None
                return None
            lei = entries[0].get("id")
            self._isin_lei_cache[isin] = lei
            return lei
        except Exception as e:
            logger.warning("ESEF GLEIF parse failed for %s: %s", isin, e)
            return None

    def _list_filings(self, lei: str) -> list[dict]:
        """Filings-Liste fuer eine LEI. Cache 1h."""
        if not lei:
            return []
        if lei in self._lei_filings_cache:
            return self._lei_filings_cache[lei]
        r = self._retried_get(f"{self.ESEF_BASE}/api/entities/{lei}/filings?page[size]=50")
        if r is None or r.status_code >= 400:
            self._lei_filings_cache[lei] = []
            return []
        try:
            data = r.json()
            filings = data.get("data") or []
            self._lei_filings_cache[lei] = filings
            return filings
        except Exception as e:
            logger.warning("ESEF filings parse failed for LEI %s: %s", lei, e)
            return []

    def _pick_filing_for_year(
        self,
        filings: list[dict],
        period_year: int,
        fy_end_month: int | None,
        fy_end_day: int | None,
    ) -> dict | None:
        """Waehlt das Filing dessen period_end zum target FY-Ende passt.
        Default: Kalenderjahr (12-31). Sonst fy_end_month/day."""
        target_month = fy_end_month or 12
        target_day = fy_end_day or 31
        target = f"{period_year:04d}-{target_month:02d}-{target_day:02d}"
        # Exact-Match bevorzugt; sonst innerhalb +-15 Tage; sonst hoechstes
        # period_end <= target
        from datetime import date
        try:
            target_d = date(period_year, target_month, target_day)
        except ValueError:
            target_d = None
        exact = [f for f in filings if f.get("attributes", {}).get("period_end") == target]
        if exact:
            return exact[0]
        if target_d:
            candidates = []
            for f in filings:
                pe = f.get("attributes", {}).get("period_end") or ""
                try:
                    pe_d = date.fromisoformat(pe)
                except ValueError:
                    continue
                diff = abs((pe_d - target_d).days)
                if diff <= 15:
                    candidates.append((diff, f))
            if candidates:
                candidates.sort(key=lambda x: x[0])
                return candidates[0][1]
        return None

    def _load_filing_facts(self, filing: dict) -> dict | None:
        """Laedt das JSON-File des Filings und gibt das parsed dict zurueck.
        Cache 1h pro filing-URL."""
        attrs = filing.get("attributes", {})
        json_url = attrs.get("json_url")
        if not json_url:
            return None
        full_url = f"{self.ESEF_BASE}{json_url}"
        if full_url in self._filing_json_cache:
            return self._filing_json_cache[full_url]
        r = self._retried_get(full_url)
        if r is None or r.status_code >= 400:
            return None
        try:
            data = r.json()
            self._filing_json_cache[full_url] = data
            return data
        except Exception as e:
            logger.warning("ESEF JSON parse failed %s: %s", full_url, e)
            return None

    def _find_fact_for_period(
        self,
        facts: dict,
        concepts: list[str],
        period_year: int,
    ) -> tuple[Decimal | None, str | None]:
        """Sucht den Fact-Value fuer ein IFRS-Concept im Ziel-Jahr.
        Returns (value, currency)."""
        period_start = f"{period_year:04d}-01-01"
        period_end = f"{period_year + 1:04d}-01-01"
        for concept in concepts:
            for _fid, fact in facts.items():
                dims = fact.get("dimensions") or {}
                if dims.get("concept") != concept:
                    continue
                period = dims.get("period") or ""
                # Period-Format: "2024-01-01T00:00:00/2025-01-01T00:00:00"
                # ODER instant "2024-12-31T00:00:00" fuer Bilanz-Posten.
                if "/" in period:
                    start, end = period.split("/", 1)
                    if not start.startswith(period_start) or not end.startswith(period_end):
                        continue
                else:
                    # Instant — match exactly the year-end
                    if not period.startswith(f"{period_year:04d}-12-31"):
                        continue
                value_str = fact.get("value")
                if value_str is None:
                    continue
                try:
                    value = Decimal(str(value_str))
                except Exception:
                    continue
                unit = dims.get("unit") or ""
                currency = unit.split(":")[-1] if ":" in unit else None
                return value, currency
        return None, None

    def fetch(
        self,
        ticker: str,
        key: str,
        period_type: str = "FY",
        period_year: int | None = None,
        fy_end_month: int | None = None,
        fy_end_day: int | None = None,
        isin: str | None = None,
    ) -> ProviderResult | None:
        """ESEF-Lookup fuer ein FY-Annual-Filing (kein Q-Support)."""
        if period_type != "FY" or period_year is None:
            return None
        if key not in self.supported_keys:
            return None
        if not isin:
            return None
        lei = self._resolve_isin_to_lei(isin)
        if not lei:
            return None
        filings = self._list_filings(lei)
        if not filings:
            return None
        filing = self._pick_filing_for_year(filings, period_year, fy_end_month, fy_end_day)
        if filing is None:
            return None
        data = self._load_filing_facts(filing)
        if not data:
            return None
        facts = data.get("facts") or {}
        attrs = filing.get("attributes", {})
        viewer_url = attrs.get("viewer_url") or ""
        source_link = f"{self.ESEF_BASE}{viewer_url}" if viewer_url else None

        if key == "fcf":
            ocf, cur = self._find_fact_for_period(facts, FCF_OCF_CONCEPTS, period_year)
            capex, _ = self._find_fact_for_period(facts, FCF_CAPEX_CONCEPTS, period_year)
            if ocf is None or capex is None:
                return None
            return ProviderResult(
                value=ocf - abs(capex),
                source_name=f"ESEF FY{period_year} (FCF = OCF - CapEx)",
                source_link=source_link,
                currency=cur if "fcf" in CURRENCY_KEYS else None,
            )

        if key == "ebitda":
            ebit, cur = self._find_fact_for_period(facts, EBITDA_EBIT_CONCEPTS, period_year)
            da, _ = self._find_fact_for_period(facts, EBITDA_DA_CONCEPTS, period_year)
            if ebit is None:
                return None
            value = ebit + abs(da) if da is not None else ebit
            note = "EBITDA = EBIT + D&A" if da is not None else "EBITDA ~ EBIT (D&A nicht getaggt)"
            return ProviderResult(
                value=value,
                source_name=f"ESEF FY{period_year} ({note})",
                source_link=source_link,
                currency=cur if "ebitda" in CURRENCY_KEYS else None,
            )

        concepts = CONCEPT_MAP.get(key, [])
        if not concepts:
            return None
        value, cur = self._find_fact_for_period(facts, concepts, period_year)
        if value is None:
            return None
        return ProviderResult(
            value=value,
            source_name=f"ESEF FY{period_year}",
            source_link=source_link,
            currency=cur if key in CURRENCY_KEYS else None,
        )
