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

    def _q_end_date(
        self,
        period_year: int,
        quarter: str,
        fy_end_month: int | None,
        fy_end_day: int | None,
    ) -> "date | None":
        """Q-Ende-Datum berechnen aus FY-Ende. Q4 = FY-Ende, Q3 = -3M, Q2 = -6M, Q1 = -9M.
        Day-Clamping fuer Februar-Ende (z.B. 30 -> 28)."""
        from datetime import date
        import calendar
        if quarter not in ("Q1", "Q2", "Q3", "Q4") or not fy_end_month or not fy_end_day:
            return None
        try:
            fy_end = date(period_year, fy_end_month, fy_end_day)
        except ValueError:
            return None
        months_back = (4 - int(quarter[1])) * 3
        new_month = fy_end.month - months_back
        new_year = fy_end.year
        while new_month <= 0:
            new_month += 12
            new_year -= 1
        last_day = calendar.monthrange(new_year, new_month)[1]
        return date(new_year, new_month, min(fy_end.day, last_day))

    def _find_q_standalone(
        self,
        facts: dict,
        concepts: list[str],
        target_end: "date",
    ) -> tuple[Decimal, str, str | None] | None:
        """Sucht einen Standalone-Q-Eintrag (start/end Abstand ~90 Tage) zum target_end.
        Returns (value, currency, accession) oder None.
        Toleranz: end-Datum +/- 7 Tage, period length 75-100 Tage (deckt 13-week-FY ab)."""
        from datetime import date
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        for concept_name in concepts:
            concept_data = us_gaap.get(concept_name)
            if not concept_data:
                continue
            units = concept_data.get("units", {})
            unit_keys = sorted(units.keys(), key=lambda u: 0 if u == "USD" else 1)
            for unit_name in unit_keys:
                if unit_name not in ("USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD"):
                    continue
                candidates: list[tuple[dict, int]] = []
                for e in units[unit_name]:
                    form = e.get("form", "")
                    if not form.startswith(("10-Q", "10-K")):
                        continue
                    end_str = e.get("end") or ""
                    start_str = e.get("start") or ""
                    if not end_str or not start_str:
                        continue
                    try:
                        end_d = date.fromisoformat(end_str)
                        start_d = date.fromisoformat(start_str)
                    except ValueError:
                        continue
                    if abs((end_d - target_end).days) > 7:
                        continue
                    period_days = (end_d - start_d).days
                    if not (75 <= period_days <= 100):
                        continue
                    candidates.append((e, period_days))
                if candidates:
                    # Earliest filed (= original 10-Q, nicht spaetere Restatement-Amendments)
                    best = min(candidates, key=lambda c: c[0].get("filed", "9999"))
                    e = best[0]
                    return Decimal(str(e["val"])), unit_name, e.get("accn")
        return None

    def fetch_quarterly(
        self,
        ticker: str,
        key: str,
        period_year: int,
        quarter: str,
        fy_end_month: int | None = None,
        fy_end_day: int | None = None,
    ) -> ProviderResult | None:
        """Liefert einen Standalone-Q-Actual aus 10-Q-XBRL fuer US-Filer.
        Returns None bei: nicht-US, kein CIK, kein 10-Q-Eintrag, Q4 (kommt erst
        mit FY-10-K), nicht-supported Key, oder unbekanntem FY-Ende-Datum.
        Q4-laufendes-FY ist sowieso noch nicht filed. Q4-abgeschlossen wird ueber
        FY-10-K - 9M-10-Q in der Backtest-Pipeline gehandhabt.
        """
        if quarter == "Q4":
            return None
        if quarter not in ("Q1", "Q2", "Q3"):
            return None
        if key not in self.supported_keys:
            return None
        cik = self._get_cik(ticker)
        if cik is None:
            return None
        facts = self._get_facts(cik)
        if facts is None:
            return None
        target_end = self._q_end_date(period_year, quarter, fy_end_month, fy_end_day)
        if target_end is None:
            return None

        if key == "fcf":
            ocf = self._find_q_standalone(facts, FCF_OP_CASH_CONCEPTS, target_end)
            capex = self._find_q_standalone(facts, FCF_CAPEX_CONCEPTS, target_end)
            if ocf is None or capex is None:
                return None
            ocf_val, cur, accn = ocf
            capex_val, _, _ = capex
            return ProviderResult(
                value=ocf_val - abs(capex_val),
                source_name=f"SEC EDGAR 10-Q ({quarter} FY{period_year}, FCF = OCF - CapEx)",
                source_link=self._filing_link(cik, accn),
                currency=cur if "fcf" in CURRENCY_KEYS else None,
            )

        if key == "ebitda":
            ebit = self._find_q_standalone(facts, EBITDA_EBIT_CONCEPTS, target_end)
            da = self._find_q_standalone(facts, EBITDA_DA_CONCEPTS, target_end)
            if ebit is None:
                return None
            ebit_val, cur, accn = ebit
            if da is None:
                return ProviderResult(
                    value=ebit_val,
                    source_name=f"SEC EDGAR 10-Q ({quarter} FY{period_year}, EBITDA ~ EBIT, D&A nicht in XBRL)",
                    source_link=self._filing_link(cik, accn),
                    currency=cur if "ebitda" in CURRENCY_KEYS else None,
                )
            da_val, _, _ = da
            return ProviderResult(
                value=ebit_val + abs(da_val),
                source_name=f"SEC EDGAR 10-Q ({quarter} FY{period_year}, EBITDA = EBIT + D&A)",
                source_link=self._filing_link(cik, accn),
                currency=cur if "ebitda" in CURRENCY_KEYS else None,
            )

        concepts = CONCEPT_MAP.get(key, [])
        if not concepts:
            return None
        result = self._find_q_standalone(facts, concepts, target_end)
        if result is None:
            return None
        val, cur, accn = result
        return ProviderResult(
            value=val,
            source_name=f"SEC EDGAR 10-Q ({quarter} FY{period_year})",
            source_link=self._filing_link(cik, accn),
            currency=cur if key in CURRENCY_KEYS else None,
        )

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
