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

Fact-Matching (xBRL-JSON/OIM):
  - Duration-Facts: period "start/end", end ist EXKLUSIV = period_end + 1 Tag
    (FY2025 mit Ende 2025-12-31 -> "2025-01-01T00:00:00/2026-01-01T00:00:00").
  - Instant-Facts (Bilanz): dateTime = period_end + 1 Tag (Mitternacht NACH
    dem Stichtag), z.B. "2026-01-01T00:00:00" fuer den Stichtag 2025-12-31.
  - Facts mit Zusatz-Dimensionen (Segment-/Equity-Achsen) sind Teilwerte —
    dimensionslose Facts werden bevorzugt.
"""
import logging
from datetime import date, timedelta
from decimal import Decimal

import httpx
from cachetools import TTLCache

from app.providers.base import ProviderResult
from app.values.currency_keys import CURRENCY_KEYS

logger = logging.getLogger(__name__)

USER_AGENT = "ProjektHohn/1.0 (mailto:till@theolabs.xyz)"

# IFRS-Taxonomy-Mapping unserer value_keys auf ESEF-Concepts.
# Mehrere Concepts pro Key fuer Fallback (Filer nutzen unterschiedliche Tags).
# Concept-Patterns: mit Namespace (z.B. 'ifrs-full:X') = exact match;
# ohne Namespace (z.B. 'X') = suffix-match (deckt firm-extensions wie
# 'airbus:X', 'siemens:X' etc. ab).
CONCEPT_MAP: dict[str, list[str]] = {
    # Konvention: attributable (Anteil der Mutter-Aktionaere) VOR Konzern-
    # ProfitLoss — konsistent mit der EDGAR-Praeferenz.
    # Konvention (wie EDGAR): net_income = attributable to shareholders —
    # passt zur EPS-Basis und zum eps_ni-Check. Bei EU-Filern mit
    # Minderheiten verschiebt ein Anker-Lauf bestehende Total-Werte nach
    # unten; das ist beabsichtigt, nicht ein Bug.
    "net_income": [
        "ifrs-full:ProfitLossAttributableToOwnersOfParent",
        "ifrs-full:ProfitLoss",
    ],
    "revenue": [
        "ifrs-full:Revenue",
        "ifrs-full:RevenueFromContractsWithCustomers",
        "Revenue",
        "RevenueFromContractsWithCustomers",
    ],
    "eps_diluted": [
        "ifrs-full:DilutedEarningsLossPerShare",
        "ifrs-full:BasicEarningsLossPerShare",
    ],
    "st_debt": [
        "ifrs-full:CurrentBorrowings",
        "ifrs-full:CurrentInterestbearingLoansAndBorrowings",
        "CurrentInterestbearingLoansAndBorrowings",
        "CurrentFinancialLiabilities",
    ],
    "st_investments": [
        "ifrs-full:CurrentInvestments",
        "ifrs-full:OtherCurrentFinancialAssets",
    ],
    "sbc": [
        "ifrs-full:ExpenseFromShareBasedPaymentTransactionsWithEmployees",
        "ifrs-full:IncreaseDecreaseThroughExerciseOfOptionsShareBasedPaymentArrangement",
        "IncreaseDecreaseThroughSharebasedPaymentTransactions",
        "AdjustmentsForSharebasedPayments",
    ],
    "buyback_volume": [
        "ifrs-full:PaymentsForRepurchaseOfTreasuryShares",
        "ifrs-full:PaymentsToAcquireOrRedeemEntitysShares",
        "IncreaseDecreaseThroughTreasuryShareTransactions",
        "ChangeInTreasuryShares",
    ],
    "dividends": [
        "ifrs-full:DividendsPaidClassifiedAsFinancingActivities",
        "ifrs-full:DividendsPaidToEquityHoldersOfParentClassifiedAsFinancingActivities",
        "ifrs-full:DividendsPaid",
    ],
    "cash_and_equivalents": [
        "ifrs-full:CashAndCashEquivalents",
    ],
    "lt_debt": [
        "ifrs-full:NoncurrentBorrowings",
        "ifrs-full:NoncurrentInterestbearingLoansAndBorrowings",
        "NoncurrentFinancialLiabilities",
    ],
    "lease_liabilities": [
        "ifrs-full:NoncurrentLeaseLiabilities",
        "ifrs-full:LeaseLiabilities",
    ],
    "operating_cash_flow": [
        "ifrs-full:CashFlowsFromUsedInOperatingActivities",
        "ifrs-full:CashFlowsFromUsedInOperatingActivitiesContinuingOperations",
        "CashFlowsFromUsedInOperatingActivities",
    ],
}

EBITDA_EBIT_CONCEPTS = [
    "ifrs-full:ProfitLossFromOperatingActivities",
    "ifrs-full:OperatingProfit",
    "ProfitLossBeforeFinancialResultAndIncomeTaxes",
    "ProfitLossBeforeTax",
    "OperatingIncome",
]
EBITDA_DA_CONCEPTS = [
    "ifrs-full:DepreciationAndAmortisationExpense",
    "ifrs-full:AdjustmentsForDepreciationAndAmortisationExpense",
    "ifrs-full:DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss",
    "DepreciationAndAmortisationExpense",
]

FCF_OCF_CONCEPTS = CONCEPT_MAP["operating_cash_flow"]

# CapEx-Ableitung fuer ESEF: IFRS taggt PP&E- und Intangible-Kaeufe getrennt,
# manche Filer nutzen ein kombiniertes Extension-Concept. Combined hat
# Vorrang — dann werden die Einzel-Concepts NICHT zusaetzlich addiert
# (Doppelzaehlung). Cash-Outflows werden via abs() immer positiv gefuehrt.
CAPEX_COMBINED_CONCEPTS = [
    "PurchasesOfIntangibleAssetsPropertyPlantAndEquipmentInvestmentProperty",
]
CAPEX_PPE_CONCEPTS = [
    "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    "ifrs-full:PurchaseOfPropertyPlantAndEquipment",
    "PurchaseOfPropertyPlantAndEquipment",
]
CAPEX_INTANGIBLES_CONCEPTS = [
    "ifrs-full:PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",
    "PurchaseOfIntangibleAssets",
]


class ESEFProvider:
    name = "ESEF (filings.xbrl.org)"
    supported_keys = set(CONCEPT_MAP.keys()) | {"fcf", "ebitda", "capex"}

    GLEIF_BASE = "https://api.gleif.org/api/v1"
    ESEF_BASE = "https://filings.xbrl.org"

    def __init__(self) -> None:
        self._isin_lei_cache: TTLCache = TTLCache(maxsize=500, ttl=86400)  # 24h
        self._lei_filings_cache: TTLCache = TTLCache(maxsize=200, ttl=3600)
        self._filing_json_cache: TTLCache = TTLCache(maxsize=100, ttl=3600)
        # Negative-Cache fuer Fetch-FEHLER (Ausfall/HTTP>=400/Parse) — Muster
        # aus edgar._facts_fail_cache: ohne ihn wuerde JEDE Anker-Zelle die
        # volle Retry-Kette mit Backoff-Sleeps durchlaufen. Genuin leere
        # Ergebnisse (keine LEI, keine Filings) landen weiter in den
        # positiven Caches oben.
        self._fail_cache: TTLCache = TTLCache(maxsize=300, ttl=600)
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
        fail_key = f"gleif:{isin}"
        if fail_key in self._fail_cache:
            return None
        r = self._retried_get(f"{self.GLEIF_BASE}/lei-records?filter[isin]={isin}&page[size]=1")
        if r is None or r.status_code >= 400:
            self._fail_cache[fail_key] = True
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
            self._fail_cache[fail_key] = True
            return None

    def _list_filings(self, lei: str) -> list[dict]:
        """Filings-Liste fuer eine LEI. Cache 1h."""
        if not lei:
            return []
        if lei in self._lei_filings_cache:
            return self._lei_filings_cache[lei]
        fail_key = f"filings:{lei}"
        if fail_key in self._fail_cache:
            return []
        r = self._retried_get(f"{self.ESEF_BASE}/api/entities/{lei}/filings?page[size]=50")
        if r is None or r.status_code >= 400:
            # Fehler nur kurz negativ cachen — NICHT als "genuin leer" 1h.
            self._fail_cache[fail_key] = True
            return []
        try:
            data = r.json()
            filings = data.get("data") or []
            self._lei_filings_cache[lei] = filings
            return filings
        except Exception as e:
            logger.warning("ESEF filings parse failed for LEI %s: %s", lei, e)
            self._fail_cache[fail_key] = True
            return []

    def _pick_filing_for_year(
        self,
        filings: list[dict],
        period_year: int,
        fy_end_month: int | None,
        fy_end_day: int | None,
    ) -> dict | None:
        """Waehlt das Filing dessen period_end zum target FY-Ende passt.
        Default: Kalenderjahr (12-31). Sonst fy_end_month/day.

        Bei mehreren Treffern fuer dasselbe period_end (Doppel-Filing in
        mehreren Laendern, Amendments mit Revision-Suffix im fxo_id):
        deterministisch das Filing MIT json_url und juengstem date_added."""
        target_month = fy_end_month or 12
        target_day = fy_end_day or 31
        target = f"{period_year:04d}-{target_month:02d}-{target_day:02d}"
        try:
            target_d = date(period_year, target_month, target_day)
        except ValueError:
            target_d = None
        exact = [f for f in filings if f.get("attributes", {}).get("period_end") == target]
        if exact:
            return self._pick_best_filing(exact)
        # Sonst innerhalb +-15 Tage (Filer mit leicht abweichendem Stichtag)
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
                best_diff = min(d for d, _ in candidates)
                return self._pick_best_filing([f for d, f in candidates if d == best_diff])
        return None

    @staticmethod
    def _pick_best_filing(filings: list[dict]) -> dict:
        """Deterministische Wahl unter gleichwertigen Filings:
        json_url vorhanden > juengstes date_added > hoechste Revision."""
        def sort_key(f: dict):
            a = f.get("attributes", {})
            return (
                bool(a.get("json_url")),
                a.get("date_added") or "",
                a.get("fxo_id") or "",
            )
        return sorted(filings, key=sort_key, reverse=True)[0]

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
        fail_key = f"json:{full_url}"
        if fail_key in self._fail_cache:
            return None
        r = self._retried_get(full_url)
        if r is None or r.status_code >= 400:
            self._fail_cache[fail_key] = True
            return None
        try:
            data = r.json()
            self._filing_json_cache[full_url] = data
            return data
        except Exception as e:
            logger.warning("ESEF JSON parse failed %s: %s", full_url, e)
            self._fail_cache[fail_key] = True
            return None

    @staticmethod
    def _concept_matches(actual: str, pattern: str) -> bool:
        """Pattern-Match: 'ns:X' = exact match, 'X' = suffix match
        (deckt firm-extensions wie 'airbus:X' ab)."""
        if ":" in pattern:
            return actual == pattern
        if ":" in actual:
            return actual.split(":", 1)[1] == pattern
        return actual == pattern

    # Standard-Dimensionen jedes OIM-Facts; alles darueber hinaus sind
    # Taxonomy-Achsen (Segmente, Equity-Komponenten, ...) = Teilwerte.
    _CORE_DIMS = frozenset({"concept", "entity", "period", "unit", "language", "noteId"})

    def _find_fact_for_period(
        self,
        facts: dict,
        concepts: list[str],
        period_end: date,
    ) -> tuple[Decimal | None, str | None]:
        """Sucht den Fact-Value fuer ein IFRS-Concept im FY mit dem
        gegebenen Bilanzstichtag (period_end aus dem Filing).

        OIM-Perioden: Duration-Ende und Instant sind EXKLUSIV kodiert,
        d.h. Mitternacht des Folgetags (FY-Ende 2025-12-31 ->
        end/instant "2026-01-01T00:00:00"). Duration muss ~1 Jahr lang
        sein (schliesst Quartals-/Mehrjahres-Facts aus). Facts ohne
        Zusatz-Dimensionen werden bevorzugt (Pass 1), sonst Fallback auf
        dimensionierte Facts (Pass 2). Returns (value, currency)."""
        duration_end = (period_end + timedelta(days=1)).isoformat()
        # Manche Generatoren serialisieren den Instant als Stichtag selbst.
        instant_ok = (duration_end, period_end.isoformat())
        for allow_extra_dims in (False, True):
            for concept in concepts:
                # Pass 2: unter den dimensionierten Facts gewinnt der mit den
                # WENIGSTEN Zusatz-Dimensionen — sonst kann ein willkuerlicher
                # Segment-Teilwert dauerhaft als Provider-Actual verankert
                # werden (Teilwert-Falle).
                best: tuple[int, Decimal, str | None] | None = None
                for fact in facts.values():
                    dims = fact.get("dimensions") or {}
                    if not self._concept_matches(dims.get("concept", ""), concept):
                        continue
                    extra_dims = sum(1 for k in dims if k not in self._CORE_DIMS)
                    if not allow_extra_dims and extra_dims:
                        continue
                    period = dims.get("period") or ""
                    if "/" in period:
                        start, end = period.split("/", 1)
                        if not end.startswith(duration_end):
                            continue
                        try:
                            start_d = date.fromisoformat(start[:10])
                            end_d = date.fromisoformat(end[:10])
                        except ValueError:
                            continue
                        # ~1 Jahr: deckt 52/53-Wochen-FYs und Rumpf-Monate ab
                        if not 330 <= (end_d - start_d).days <= 400:
                            continue
                    else:
                        if not period.startswith(instant_ok):
                            continue
                    value_str = fact.get("value")
                    if value_str is None:
                        continue
                    try:
                        value = Decimal(str(value_str))
                    except Exception:
                        continue
                    currency = self._unit_currency(dims.get("unit") or "")
                    if not allow_extra_dims:
                        return value, currency
                    if best is None or extra_dims < best[0]:
                        best = (extra_dims, value, currency)
                if best is not None:
                    return best[1], best[2]
        return None, None

    @staticmethod
    def _unit_currency(unit: str) -> str | None:
        """Extrahiert die Waehrung aus einer OIM-Unit. Bei Quotienten wie
        'iso4217:EUR/xbrli:shares' (EPS) zaehlt der Zaehler."""
        numerator = unit.split("/", 1)[0]
        if ":" in numerator:
            return numerator.split(":")[-1]
        return None

    def _derive_capex(
        self, facts: dict, period_end: date
    ) -> tuple[Decimal | None, str | None, str]:
        """Gemeinsame CapEx-Ableitung fuer die Keys 'capex' und 'fcf':
        kombiniertes Concept hat Vorrang (deckt PP&E + Intangibles ab),
        sonst PP&E + Intangibles. Cash-Outflows via abs() immer positiv.
        fcf = ocf - capex MUSS strukturell mit dem capex-Key konsistent
        sein — deshalb genau EINE Ableitung fuer beide.

        Returns (value, currency, note); value None wenn kein PP&E-/
        Combined-Concept getaggt ist.
        """
        combined, cur = self._find_fact_for_period(facts, CAPEX_COMBINED_CONCEPTS, period_end)
        if combined is not None:
            return abs(combined), cur, "kombiniertes Concept (PP&E + Intangibles)"
        ppe, cur = self._find_fact_for_period(facts, CAPEX_PPE_CONCEPTS, period_end)
        if ppe is None:
            return None, None, ""
        intang, _ = self._find_fact_for_period(facts, CAPEX_INTANGIBLES_CONCEPTS, period_end)
        if intang is not None:
            return abs(ppe) + abs(intang), cur, "PP&E + Intangibles"
        return abs(ppe), cur, "PP&E (Intangibles nicht getaggt)"

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
        # Fact-Matching laeuft ueber den ECHTEN Bilanzstichtag des Filings —
        # nicht ueber die Kalenderjahr-Annahme (Filer mit abweichendem FY).
        try:
            period_end = date.fromisoformat(attrs.get("period_end") or "")
        except ValueError:
            period_end = date(period_year, fy_end_month or 12, fy_end_day or 31)

        if key == "fcf":
            # DIESELBE CapEx-Ableitung wie beim capex-Key — sonst verletzt
            # der Provider selbst die Identitaet fcf = ocf - abs(capex).
            ocf, cur = self._find_fact_for_period(facts, FCF_OCF_CONCEPTS, period_end)
            capex, _, _ = self._derive_capex(facts, period_end)
            if ocf is None or capex is None:
                return None
            return ProviderResult(
                value=ocf - capex,
                source_name=f"ESEF FY{period_year} (FCF = OCF - CapEx)",
                source_link=source_link,
                currency=cur if "fcf" in CURRENCY_KEYS else None,
            )

        if key == "capex":
            value, cur, note = self._derive_capex(facts, period_end)
            if value is None:
                return None
            return ProviderResult(
                value=value,
                source_name=f"ESEF FY{period_year} (CapEx = {note})",
                source_link=source_link,
                currency=cur if "capex" in CURRENCY_KEYS else None,
            )

        if key == "ebitda":
            ebit, cur = self._find_fact_for_period(facts, EBITDA_EBIT_CONCEPTS, period_end)
            da, _ = self._find_fact_for_period(facts, EBITDA_DA_CONCEPTS, period_end)
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
        value, cur = self._find_fact_for_period(facts, concepts, period_end)
        if value is None:
            return None
        return ProviderResult(
            value=value,
            source_name=f"ESEF FY{period_year}",
            source_link=source_link,
            currency=cur if key in CURRENCY_KEYS else None,
        )
