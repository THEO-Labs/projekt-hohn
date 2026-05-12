"""SEC EDGAR XBRL provider — pulls fundamentals directly from filed 10-K data.

Authoritative source for US-listed companies (Apple, Visa, Microsoft, etc.).
Returns None for non-US tickers (no CIK match) so the registry falls back to
Yahoo for those.
"""
import logging
from decimal import Decimal

import httpx
from cachetools import TTLCache

from app.providers.base import ProviderResult
from app.values.currency_keys import CURRENCY_KEYS

logger = logging.getLogger(__name__)

USER_AGENT = "ProjektHohn/1.0 (mailto:till@theolabs.xyz)"

# Map our value_keys to a list of XBRL concept names (us-gaap namespace).
# Multiple concepts per key because different filers use different tags.
CONCEPT_MAP: dict[str, list[str]] = {
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
    ],
    "sbc": [
        "ShareBasedCompensation",
        "AllocatedShareBasedCompensationExpense",
        "ShareBasedCompensationExpense",
    ],
    "buyback_volume": [
        "PaymentsForRepurchaseOfCommonStock",
        "PaymentsForRepurchaseOfEquity",
    ],
    "dividends": [
        "PaymentsOfDividends",
        "PaymentsOfDividendsCommonStock",
    ],
    "cash_and_equivalents": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "marketable_securities_st": [
        "MarketableSecuritiesCurrent",
        "ShortTermInvestments",
        "AvailableForSaleSecuritiesCurrent",
    ],
    "marketable_securities_lt": [
        "MarketableSecuritiesNoncurrent",
        "LongTermInvestments",
        "AvailableForSaleSecuritiesNoncurrent",
    ],
    "lease_liabilities": [
        "OperatingLeaseLiability",
        "OperatingLeaseLiabilityNoncurrent",
    ],
    "long_term_debt": [
        "LongTermDebt",
        "LongTermDebtNoncurrent",
    ],
}

# EBITDA-Ableitung fuer EDGAR: EBIT (Operating Income) + D&A (Depreciation & Amortization).
# US-GAAP-Filer reporten EBITDA nicht als single concept (non-GAAP-Kennzahl),
# wir aggregieren aus zwei Standard-Konzepten.
EBITDA_EBIT_CONCEPTS = [
    "OperatingIncomeLoss",
]
EBITDA_DA_CONCEPTS = [
    "DepreciationDepletionAndAmortization",
    "DepreciationAndAmortization",
    "Depreciation",
]

FCF_OP_CASH_CONCEPTS = [
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByOperatingActivities",
]
FCF_CAPEX_CONCEPTS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
]


class EdgarProvider:
    name = "SEC EDGAR"
    supported_keys = set(CONCEPT_MAP.keys()) | {"fcf", "shares_outstanding", "ebitda"}

    def __init__(self) -> None:
        self._ticker_to_cik: dict[str, str] | None = None
        self._facts_cache: TTLCache = TTLCache(maxsize=200, ttl=3600)
        # HTTPTransport mit retries=3 fuer transiente 429/503/Network-Errors —
        # SEC ist gelegentlich rate-limited, transient.
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=15.0,
            follow_redirects=True,
            transport=httpx.HTTPTransport(retries=3),
        )

    def _retried_get(self, url: str, *, max_attempts: int = 3) -> httpx.Response | None:
        """GET mit explizitem Backoff bei 429/503. Transport-retries decken nur
        Connection-Errors, nicht HTTP-Status-Errors."""
        import time
        for attempt in range(max_attempts):
            try:
                r = self._client.get(url)
                if r.status_code in (429, 503) and attempt < max_attempts - 1:
                    wait = 2 ** attempt
                    logger.warning("EDGAR %s -> %s, retry in %ds", url, r.status_code, wait)
                    time.sleep(wait)
                    continue
                return r
            except Exception as e:
                if attempt == max_attempts - 1:
                    logger.warning("EDGAR GET %s failed after %d retries: %s", url, max_attempts, e)
                    return None
                wait = 2 ** attempt
                time.sleep(wait)
        return None

    def _get_cik(self, ticker: str) -> str | None:
        if self._ticker_to_cik is None:
            r = self._retried_get("https://www.sec.gov/files/company_tickers.json")
            if r is None or r.status_code >= 400:
                logger.warning("EDGAR ticker-list fetch failed (status=%s)",
                               r.status_code if r else "no-response")
                return None
            try:
                data = r.json()
                self._ticker_to_cik = {
                    item["ticker"].upper(): str(item["cik_str"]).zfill(10)
                    for item in data.values()
                }
            except Exception as e:
                logger.warning("EDGAR ticker-list parse failed: %s", e)
                return None
        return self._ticker_to_cik.get(ticker.upper())

    def _get_facts(self, cik: str) -> dict | None:
        if cik in self._facts_cache:
            return self._facts_cache[cik]
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        r = self._retried_get(url)
        if r is None:
            return None
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            logger.warning("EDGAR companyfacts CIK %s -> %s after retries", cik, r.status_code)
            return None
        try:
            data = r.json()
            self._facts_cache[cik] = data
            return data
        except Exception as e:
            logger.warning("EDGAR companyfacts parse failed for CIK %s: %s", cik, e)
            return None

    def _find_value(
        self,
        facts: dict,
        concepts: list[str],
        period_year: int,
        fy_end_month: int | None = None,
        fy_end_day: int | None = None,
    ) -> tuple[Decimal | None, str | None, str | None]:
        """Search facts for the first matching concept that has a 10-K entry
        for FY=period_year. If fy_end_month/day given, prefer entries whose
        end-date is within ±5 days of that fiscal-year-end (handles Sept-FY
        like Apple where startswith(year) alone could pick interim periods).
        Returns (value, currency, accession-number)."""
        from datetime import date
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        target_end: date | None = None
        if fy_end_month and fy_end_day:
            try:
                target_end = date(period_year, fy_end_month, fy_end_day)
            except ValueError:
                target_end = None
        for concept_name in concepts:
            concept_data = us_gaap.get(concept_name)
            if not concept_data:
                continue
            units = concept_data.get("units", {})
            unit_keys = sorted(units.keys(), key=lambda u: 0 if u == "USD" else 1)
            for unit_name in unit_keys:
                if unit_name not in ("USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD"):
                    continue
                entries = units[unit_name]
                yr_str = str(period_year)
                candidates = [
                    e for e in entries
                    if e.get("end", "").startswith(yr_str)
                    and e.get("form", "").startswith(("10-K", "20-F"))
                ]
                # Tighten by exact FY-end-date if known (±5d tolerance for
                # week-anchored FYs like Apple's last-Saturday-of-September).
                if target_end and candidates:
                    def _within_window(entry: dict) -> bool:
                        try:
                            ed = date.fromisoformat(entry.get("end", ""))
                        except ValueError:
                            return False
                        return abs((ed - target_end).days) <= 5
                    tight = [e for e in candidates if _within_window(e)]
                    if tight:
                        candidates = tight
                annual = [e for e in candidates if e.get("fp") == "FY"]
                pool = annual or candidates
                if pool:
                    best = min(pool, key=lambda e: e.get("filed", "9999"))
                    return Decimal(str(best["val"])), unit_name, best.get("accn")
        return None, None, None

    def _filing_link(self, cik: str, accn: str | None) -> str:
        if accn:
            accn_clean = accn.replace("-", "")
            return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn_clean}/{accn}-index.htm"
        return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K"

    def fetch(
        self,
        ticker: str,
        key: str,
        period_type: str = "FY",
        period_year: int | None = None,
        fy_end_month: int | None = None,
        fy_end_day: int | None = None,
    ) -> ProviderResult | None:
        if period_type != "FY" or period_year is None:
            return None
        if key not in self.supported_keys:
            return None
        cik = self._get_cik(ticker)
        if cik is None:
            return None
        facts = self._get_facts(cik)
        if facts is None:
            return None

        if key == "fcf":
            ocf, cur, accn = self._find_value(facts, FCF_OP_CASH_CONCEPTS, period_year, fy_end_month, fy_end_day)
            capex, _, _ = self._find_value(facts, FCF_CAPEX_CONCEPTS, period_year, fy_end_month, fy_end_day)
            if ocf is None or capex is None:
                return None
            return ProviderResult(
                value=ocf - abs(capex),
                source_name=f"SEC EDGAR 10-K (FCF = OCF − CapEx, FY{period_year})",
                source_link=self._filing_link(cik, accn),
                currency=cur if "fcf" in CURRENCY_KEYS else None,
            )

        if key == "ebitda":
            ebit, cur, accn = self._find_value(facts, EBITDA_EBIT_CONCEPTS, period_year, fy_end_month, fy_end_day)
            da, _, _ = self._find_value(facts, EBITDA_DA_CONCEPTS, period_year, fy_end_month, fy_end_day)
            if ebit is None:
                return None
            # Wenn D&A nicht findbar (selten — manche Filer reporten es nur im
            # 10-K Notes-Bereich, nicht als XBRL concept), nehmen wir EBIT als
            # konservative Approximation und markieren das in der Source.
            if da is None:
                return ProviderResult(
                    value=ebit,
                    source_name=f"SEC EDGAR 10-K (EBITDA ≈ EBIT, D&A nicht in XBRL — FY{period_year})",
                    source_link=self._filing_link(cik, accn),
                    currency=cur if "ebitda" in CURRENCY_KEYS else None,
                )
            return ProviderResult(
                value=ebit + abs(da),
                source_name=f"SEC EDGAR 10-K (EBITDA = EBIT + D&A, FY{period_year})",
                source_link=self._filing_link(cik, accn),
                currency=cur if "ebitda" in CURRENCY_KEYS else None,
            )

        if key == "shares_outstanding":
            dei = facts.get("facts", {}).get("dei", {})
            for concept_name in ("EntityCommonStockSharesOutstanding",):
                concept = dei.get(concept_name)
                if not concept:
                    continue
                units = concept.get("units", {}).get("shares", [])
                # Latest entry that ended in period_year, prefer 10-K
                matching = [e for e in units if e.get("end", "").startswith(str(period_year))]
                if not matching:
                    continue
                ten_ks = [e for e in matching if e.get("form", "").startswith(("10-K", "20-F"))]
                pool = ten_ks or matching
                latest = max(pool, key=lambda e: e["end"])
                return ProviderResult(
                    value=Decimal(str(latest["val"])),
                    source_name=f"SEC EDGAR ({latest.get('form', '?')}, {latest.get('end', '')})",
                    source_link=self._filing_link(cik, latest.get("accn")),
                )
            return None

        concepts = CONCEPT_MAP.get(key, [])
        value, cur, accn = self._find_value(facts, concepts, period_year, fy_end_month, fy_end_day)
        if value is None:
            return None
        return ProviderResult(
            value=value,
            source_name=f"SEC EDGAR 10-K (FY{period_year})",
            source_link=self._filing_link(cik, accn),
            currency=cur if key in CURRENCY_KEYS else None,
        )
