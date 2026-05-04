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
    supported_keys = set(CONCEPT_MAP.keys()) | {"fcf", "shares_outstanding"}

    def __init__(self) -> None:
        self._ticker_to_cik: dict[str, str] | None = None
        self._facts_cache: TTLCache = TTLCache(maxsize=200, ttl=3600)
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=15.0,
            follow_redirects=True,
        )

    def _get_cik(self, ticker: str) -> str | None:
        if self._ticker_to_cik is None:
            try:
                r = self._client.get("https://www.sec.gov/files/company_tickers.json")
                r.raise_for_status()
                data = r.json()
                self._ticker_to_cik = {
                    item["ticker"].upper(): str(item["cik_str"]).zfill(10)
                    for item in data.values()
                }
            except Exception as e:
                logger.warning("EDGAR ticker-list fetch failed: %s", e)
                return None
        return self._ticker_to_cik.get(ticker.upper())

    def _get_facts(self, cik: str) -> dict | None:
        if cik in self._facts_cache:
            return self._facts_cache[cik]
        try:
            url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
            r = self._client.get(url)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
            self._facts_cache[cik] = data
            return data
        except Exception as e:
            logger.warning("EDGAR companyfacts fetch failed for CIK %s: %s", cik, e)
            return None

    def _find_value(
        self,
        facts: dict,
        concepts: list[str],
        period_year: int,
    ) -> tuple[Decimal | None, str | None, str | None]:
        """Search facts for the first matching concept that has a 10-K entry
        for FY=period_year. Returns (value, currency, accession-number)."""
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        for concept_name in concepts:
            concept_data = us_gaap.get(concept_name)
            if not concept_data:
                continue
            units = concept_data.get("units", {})
            # Prefer USD; fall back to any monetary unit.
            unit_keys = sorted(units.keys(), key=lambda u: 0 if u == "USD" else 1)
            for unit_name in unit_keys:
                if unit_name not in ("USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD"):
                    continue
                entries = units[unit_name]
                # Filter to entries where the period actually ENDS in period_year
                # (the `fy` attribute is the filing's fiscal year, not the reporting period —
                # comparative-prior-years would otherwise leak in).
                yr_str = str(period_year)
                candidates = [
                    e for e in entries
                    if e.get("end", "").startswith(yr_str)
                    and e.get("form", "").startswith(("10-K", "20-F"))
                ]
                # Prefer entries explicitly marked fp=FY (annual) over Q4/etc.
                annual = [e for e in candidates if e.get("fp") == "FY"]
                pool = annual or candidates
                # If still multiple (e.g. same period reported in two consecutive 10-Ks),
                # take the one filed earliest — that's the original report, not a restatement
                # comparative. If user wants restated values, they can override manually.
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
            ocf, cur, accn = self._find_value(facts, FCF_OP_CASH_CONCEPTS, period_year)
            capex, _, _ = self._find_value(facts, FCF_CAPEX_CONCEPTS, period_year)
            if ocf is None or capex is None:
                return None
            return ProviderResult(
                value=ocf - abs(capex),
                source_name=f"SEC EDGAR 10-K (FCF = OCF − CapEx, FY{period_year})",
                source_link=self._filing_link(cik, accn),
                currency=cur if "fcf" in CURRENCY_KEYS else None,
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
        value, cur, accn = self._find_value(facts, concepts, period_year)
        if value is None:
            return None
        return ProviderResult(
            value=value,
            source_name=f"SEC EDGAR 10-K (FY{period_year})",
            source_link=self._filing_link(cik, accn),
            currency=cur if key in CURRENCY_KEYS else None,
        )
