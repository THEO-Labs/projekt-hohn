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

# Akzeptierte Waehrungscodes fuer XBRL-Units.
UNIT_CURRENCIES = ("USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD")


def _unit_currency(unit_name: str) -> str | None:
    """Currency eines XBRL-Unit-Namens. Monetaere Facts tragen reine
    Waehrungscodes ("USD"), Per-Share-Facts (EPS) Units der Form
    "<CUR>/shares" — Currency ist der Zaehler. Alles andere ("shares",
    "pure", ...) liefert None und wird uebersprungen."""
    if unit_name in UNIT_CURRENCIES:
        return unit_name
    num, sep, denom = unit_name.partition("/")
    if sep and denom == "shares" and num in UNIT_CURRENCIES:
        return num
    return None

# Map our value_keys to a list of XBRL concept names (us-gaap namespace).
# Multiple concepts per key because different filers use different tags.
CONCEPT_MAP: dict[str, list[str]] = {
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
    ],
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ],
    "eps_diluted": [
        "EarningsPerShareDiluted",
        "IncomeLossFromContinuingOperationsPerDilutedShare",
        "NetIncomeLossPerOutstandingLimitedPartnershipAndGeneralPartnershipUnitDiluted",
        # Fallback: Basic EPS if Diluted not reported (rare — small caps only).
        # Restluecke Visa: EPS ist dort NUR mit Class-A/B/C-Member-Dimension
        # getaggt; die companyfacts-API liefert ausschliesslich dimensionslose
        # Facts, daher taucht fuer Visa gar kein EarningsPerShare*-Concept auf
        # (unabhaengig vom Unit-Handling). Zelle bleibt leer/not_found.
        "EarningsPerShareBasic",
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByOperatingActivities",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
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
    "st_investments": [
        "MarketableSecuritiesCurrent",
        "ShortTermInvestments",
        "AvailableForSaleSecuritiesCurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        "DebtSecuritiesAvailableForSaleCurrent",
        "HeldToMaturitySecuritiesCurrent",
        "TradingSecurities",
        "InvestmentsCurrent",
        "OtherShortTermInvestments",
    ],
    "st_debt": [
        "ShortTermBorrowings",
        "DebtCurrent",
        "LongTermDebtCurrent",
        "CommercialPaper",
    ],
    # lt_debt: NUR LongTermDebtNoncurrent — bewusst KEINE Fallback-Kaskade
    # auf LongTermDebt/DebtInstrument-Konzepte: LongTermDebt ist bei vielen
    # Filern der Total Carrying Value INKLUSIVE Current Maturities (der
    # historische lt_debt-Fehler). Lieber leer als falsch.
    "lt_debt": [
        "LongTermDebtNoncurrent",
    ],
    # Legacy keys (nicht mehr im Catalog, aber Code kann darauf referenzieren):
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

# EBITDA-Ableitung fuer EDGAR: EBIT (Operating Income) + D&A (Depreciation AND
# Amortization). US-GAAP-Filer reporten EBITDA nicht als single concept
# (non-GAAP-Kennzahl), wir aggregieren aus GAAP-Standard-Konzepten.
# WICHTIG: EBIT ist STRIKT GAAP OperatingIncomeLoss — nie ein Non-GAAP-/
# Adjusted-Operating-Income (Intuit-Q4-Fall).
EBITDA_EBIT_CONCEPTS = [
    "OperatingIncomeLoss",
]
# Volle Cashflow-D&A INKL. Amortisation immaterieller Werte — diese Konzepte
# umfassen die Intangible-Amortisation per Definition. Reihenfolge egal fuer
# die Korrektheit, aber die zusammengesetzten Konzepte zuerst.
EBITDA_DA_FULL_CONCEPTS = [
    "DepreciationDepletionAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "DepreciationAndAmortization",
]
# Reine Abschreibung OHNE Amortisation immaterieller Werte. Alleine als D&A-
# Summand VERBOTEN (unterzeichnet EBITDA um die Intangible-Amort — der
# Dynatrace-Fehler: 199 statt 228 Mio). Nur nutzbar, wenn die Intangible-
# Amortisation separat getaggt ist und addiert wird.
EBITDA_DEPRECIATION_ONLY_CONCEPTS = [
    "Depreciation",
]
# Separat getaggte Amortisation immaterieller Vermoegenswerte — der fehlende
# Summand, wenn nur "Depreciation" (Abschreibung-only) verfuegbar ist.
EBITDA_INTANGIBLE_AMORT_CONCEPTS = [
    "AmortizationOfIntangibleAssets",
    "AmortizationOfFiniteLivedIntangibleAssets",
    "FiniteLivedIntangibleAssetsAmortizationExpense",
]

FCF_OP_CASH_CONCEPTS = [
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByOperatingActivities",
]
FCF_CAPEX_CONCEPTS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
]

# Bilanz-Keys: US-Filer taggen die als Instant-Facts (ohne "start") — die
# Standalone-Duration-Suche greift dort nie. Q4-Instant steht im 10-K.
# lt_debt dabei: der Instant-Lookup nutzt STRIKT LongTermDebtNoncurrent
# (siehe CONCEPT_MAP) — kein Teilwert-Risiko. st_debt bewusst NICHT dabei:
# braucht das Sum-Handling des FY-Pfads (DebtCurrent-Total vs Einzel-
# komponenten, siehe fetch) — ein nackter Instant-Lookup wuerde Teilwerte
# liefern; Quartale kommen nur ueber die 8-K-Bruecke.
BALANCE_KEYS = {"cash_and_equivalents", "st_investments", "lt_debt"}

# US-Filer: diese Bilanz-Debt-Keys sind EDGAR-only. Yahoos "Long Term Debt"
# enthaelt Operating-/Finance-Leases (Natera/Dynatrace haben KEINE Finanz-
# schuld, nur Operating-Leases — der Marktdaten-Feed ueberzeichnete net_debt
# um 96-118 Mio). Fehlt das strikte EDGAR-Konzept, bleibt die Zelle LEER:
# Firma ohne Finanzschuld -> net_debt = -(cash+st_investments), Netto-Cash.
# Genutzt vom FY-Anker (provider_anchor._fetch_from_chain), der den
# Marktdaten-Feed fuer diese Keys bei US-Filern uebergeht.
US_DEBT_BALANCE_KEYS = frozenset({"st_debt", "lt_debt"})

# Cashflow-Keys: 10-Qs taggen die meist nur als YTD-Duration. Quartal via
# YTD-Differenz aus derselben XBRL-Quelle wie das FY — Konsistenz per
# Konstruktion (Q4 = FY-10-K minus 9M-YTD).
CASHFLOW_YTD_KEYS = {"operating_cash_flow", "capex", "sbc", "buyback_volume", "dividends"}

# Immer-positive Keys: negativer YTD-Diff = Restatement-Artefakt -> verwerfen.
ALWAYS_POSITIVE_DIFF_KEYS = {"capex", "sbc", "buyback_volume", "dividends"}


class EdgarProvider:
    name = "SEC EDGAR"
    supported_keys = set(CONCEPT_MAP.keys()) | {"fcf", "shares_outstanding", "ebitda"}

    def __init__(self) -> None:
        self._ticker_to_cik: dict[str, str] | None = None
        self._facts_cache: TTLCache = TTLCache(maxsize=200, ttl=3600)
        # Fehlversuche pro CIK (Ausfall/404/Parse) kurz negativ cachen.
        self._facts_fail_cache: TTLCache = TTLCache(maxsize=200, ttl=600)
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
        # Negative-Cache: bei EDGAR-Ausfall wuerde sonst JEDE Anker-Zelle
        # (hunderte pro Refresh) die volle Retry-Kette mit Backoff-Sleeps
        # durchlaufen — Fehlversuche werden 10 Minuten gemerkt.
        if cik in self._facts_fail_cache:
            return None
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        r = self._retried_get(url)
        if r is None:
            self._facts_fail_cache[cik] = True
            return None
        if r.status_code == 404:
            self._facts_fail_cache[cik] = True
            return None
        if r.status_code >= 400:
            logger.warning("EDGAR companyfacts CIK %s -> %s after retries", cik, r.status_code)
            self._facts_fail_cache[cik] = True
            return None
        try:
            data = r.json()
            self._facts_cache[cik] = data
            return data
        except Exception as e:
            logger.warning("EDGAR companyfacts parse failed for CIK %s: %s", cik, e)
            self._facts_fail_cache[cik] = True
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
            unit_keys = sorted(units.keys(), key=lambda u: 0 if _unit_currency(u) == "USD" else 1)
            for unit_name in unit_keys:
                currency = _unit_currency(unit_name)
                if currency is None:
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
                    return Decimal(str(best["val"])), currency, best.get("accn")
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
            unit_keys = sorted(units.keys(), key=lambda u: 0 if _unit_currency(u) == "USD" else 1)
            for unit_name in unit_keys:
                currency = _unit_currency(unit_name)
                if currency is None:
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
                    return Decimal(str(e["val"])), currency, e.get("accn")
        return None

    def _fy_start(
        self,
        period_year: int,
        fy_end_month: int | None,
        fy_end_day: int | None,
    ) -> "date":
        """Geschaeftsjahresbeginn = Vorjahres-FY-Ende + 1 Tag.
        Kalenderjahr-Default: 01.01.period_year."""
        from datetime import date, timedelta
        prev_end = self._q_end_date(period_year - 1, "Q4", fy_end_month, fy_end_day)
        if prev_end is None:
            return date(period_year, 1, 1)
        return prev_end + timedelta(days=1)

    def _find_q_ytd(
        self,
        facts: dict,
        concepts: list[str],
        fy_start: "date",
        target_end: "date",
    ) -> tuple[Decimal, str, str | None] | None:
        """Sucht einen YTD-Duration-Eintrag: start == FY-Beginn (+/-7 Tage),
        end == target_end (+/-7 Tage), Form 10-Q/10-K, earliest filed.
        Returns (value, currency, accession) oder None."""
        from datetime import date
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        for concept_name in concepts:
            concept_data = us_gaap.get(concept_name)
            if not concept_data:
                continue
            units = concept_data.get("units", {})
            unit_keys = sorted(units.keys(), key=lambda u: 0 if _unit_currency(u) == "USD" else 1)
            for unit_name in unit_keys:
                currency = _unit_currency(unit_name)
                if currency is None:
                    continue
                candidates: list[dict] = []
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
                    if abs((start_d - fy_start).days) > 7:
                        continue
                    candidates.append(e)
                if candidates:
                    best = min(candidates, key=lambda e: e.get("filed", "9999"))
                    return Decimal(str(best["val"])), currency, best.get("accn")
        return None

    def _find_q_instant(
        self,
        facts: dict,
        concepts: list[str],
        target_end: "date",
    ) -> tuple[Decimal, str, str | None] | None:
        """Sucht einen Instant-Eintrag (ohne "start") zum target_end (+/-7 Tage),
        Form 10-Q/10-K, earliest filed. Bilanz-Positionen sind Instant-Facts."""
        from datetime import date
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        for concept_name in concepts:
            concept_data = us_gaap.get(concept_name)
            if not concept_data:
                continue
            units = concept_data.get("units", {})
            unit_keys = sorted(units.keys(), key=lambda u: 0 if _unit_currency(u) == "USD" else 1)
            for unit_name in unit_keys:
                currency = _unit_currency(unit_name)
                if currency is None:
                    continue
                candidates: list[dict] = []
                for e in units[unit_name]:
                    form = e.get("form", "")
                    if not form.startswith(("10-Q", "10-K")):
                        continue
                    if e.get("start"):
                        continue
                    end_str = e.get("end") or ""
                    if not end_str:
                        continue
                    try:
                        end_d = date.fromisoformat(end_str)
                    except ValueError:
                        continue
                    if abs((end_d - target_end).days) > 7:
                        continue
                    candidates.append(e)
                if candidates:
                    best = min(candidates, key=lambda e: e.get("filed", "9999"))
                    return Decimal(str(best["val"])), currency, best.get("accn")
        return None

    def _find_fy_duration(
        self,
        facts: dict,
        concepts: list[str],
        fy_end: "date",
    ) -> tuple[Decimal, str, str | None] | None:
        """Sucht einen FY-Duration-Eintrag: start vorhanden, Duration 350-380
        Tage (volles Jahr), end == fy_end (+/-7 Tage), Form 10-Q/10-K,
        earliest filed. Eigener Lookup fuer den Q4 = FY - YTD3-Diff —
        _find_value bleibt fuer FY-fetch-Aufrufer unveraendert (die duerfen
        weiterhin fp=FY-Eintraege ohne start akzeptieren)."""
        from datetime import date
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        for concept_name in concepts:
            concept_data = us_gaap.get(concept_name)
            if not concept_data:
                continue
            units = concept_data.get("units", {})
            unit_keys = sorted(units.keys(), key=lambda u: 0 if _unit_currency(u) == "USD" else 1)
            for unit_name in unit_keys:
                currency = _unit_currency(unit_name)
                if currency is None:
                    continue
                candidates: list[dict] = []
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
                    if abs((end_d - fy_end).days) > 7:
                        continue
                    if not (350 <= (end_d - start_d).days <= 380):
                        continue
                    candidates.append(e)
                if candidates:
                    best = min(candidates, key=lambda e: e.get("filed", "9999"))
                    return Decimal(str(best["val"])), currency, best.get("accn")
        return None

    def _q_via_ytd_diff(
        self,
        facts: dict,
        concepts: list[str],
        period_year: int,
        quarter: str,
        fy_end_month: int | None,
        fy_end_day: int | None,
        forbid_negative_diff: bool = False,
    ) -> tuple[Decimal, str, str | None] | None:
        """Quartal aus YTD-Differenz: Q1 = YTD1, Q2 = YTD2 - YTD1,
        Q3 = YTD3 - YTD2, Q4 = FY (10-K) - YTD3. Beide Eintraege muessen vom
        SELBEN Konzept und derselben Currency stammen (sonst Aepfel-Birnen).
        forbid_negative_diff prueft PRO Konzept: negativer Diff bei
        immer-positiven Keys = Restatement-Artefakt -> naechstes Konzept."""
        fy_start = self._fy_start(period_year, fy_end_month, fy_end_day)
        target_end = self._q_end_date(period_year, quarter, fy_end_month, fy_end_day)
        if target_end is None:
            return None
        if quarter == "Q1":
            for concept in concepts:
                res = self._find_q_ytd(facts, [concept], fy_start, target_end)
                if res is None:
                    continue
                if forbid_negative_diff and res[0] < 0:
                    continue
                return res
            return None
        prev_q = {"Q2": "Q1", "Q3": "Q2", "Q4": "Q3"}.get(quarter)
        if prev_q is None:
            return None
        prev_end = self._q_end_date(period_year, prev_q, fy_end_month, fy_end_day)
        if prev_end is None:
            return None
        for concept in concepts:
            prev = self._find_q_ytd(facts, [concept], fy_start, prev_end)
            if prev is None:
                continue
            prev_val, prev_cur, _ = prev
            if quarter == "Q4":
                # FY-Seite nur mit echter Jahres-Duration (350-380 Tage) —
                # sonst kaeme z.B. ein Interim-Frame als FY-Basis durch.
                fy = self._find_fy_duration(facts, [concept], target_end)
                if fy is None:
                    continue
                fy_val, fy_cur, fy_accn = fy
                if fy_cur != prev_cur:
                    continue
                diff = fy_val - prev_val
                if forbid_negative_diff and diff < 0:
                    continue
                return diff, fy_cur, fy_accn
            cur_ytd = self._find_q_ytd(facts, [concept], fy_start, target_end)
            if cur_ytd is None or cur_ytd[1] != prev_cur:
                continue
            diff = cur_ytd[0] - prev_val
            if forbid_negative_diff and diff < 0:
                continue
            return diff, cur_ytd[1], cur_ytd[2]
        return None

    def _q_flow(
        self,
        facts: dict,
        concepts: list[str],
        period_year: int,
        quarter: str,
        fy_end_month: int | None,
        fy_end_day: int | None,
        target_end: "date",
        forbid_negative_diff: bool = False,
    ) -> tuple[Decimal, str, str | None, str] | None:
        """Quartals-Flow-Wert: Standalone-3M-Frame zuerst (manche Filer taggen
        die), sonst YTD-Differenz. Returns (value, currency, accession, method)
        mit method in ("standalone", "ytd_diff") oder None."""
        res = self._find_q_standalone(facts, concepts, target_end)
        if res is not None:
            return res[0], res[1], res[2], "standalone"
        # Negativ-Guard laeuft PRO Konzept in _q_via_ytd_diff — ein
        # Restatement-Artefakt in einem Konzept blockiert nicht die anderen.
        diff = self._q_via_ytd_diff(
            facts, concepts, period_year, quarter, fy_end_month, fy_end_day,
            forbid_negative_diff=forbid_negative_diff,
        )
        if diff is None:
            return None
        return diff[0], diff[1], diff[2], "ytd_diff"

    def _fy_da_full(
        self,
        facts: dict,
        period_year: int,
        fy_end_month: int | None,
        fy_end_day: int | None,
    ) -> tuple[Decimal, str, str | None] | None:
        """Volle FY-D&A INKL. Amortisation immaterieller Werte.
        Returns (value, currency, accession) oder None.

        Prioritaet: zuerst die zusammengesetzten D&A-Konzepte (enthalten die
        Intangible-Amort per Definition). Nur "Depreciation" (Abschreibung-
        only) getaggt -> es MUSS AmortizationOfIntangibleAssets separat
        addiert werden; fehlt das (oder andere Currency), gilt D&A als
        UNVOLLSTAENDIG -> None (lieber leer als ein zu niedriger EBITDA)."""
        da, cur, accn = self._find_value(
            facts, EBITDA_DA_FULL_CONCEPTS, period_year, fy_end_month, fy_end_day
        )
        if da is not None:
            return da, cur, accn
        dep, dep_cur, dep_accn = self._find_value(
            facts, EBITDA_DEPRECIATION_ONLY_CONCEPTS, period_year,
            fy_end_month, fy_end_day,
        )
        if dep is None:
            return None
        amort, amort_cur, _ = self._find_value(
            facts, EBITDA_INTANGIBLE_AMORT_CONCEPTS, period_year,
            fy_end_month, fy_end_day,
        )
        if amort is None or amort_cur != dep_cur:
            return None
        return dep + amort, dep_cur, dep_accn

    def _q_da_full(
        self,
        facts: dict,
        period_year: int,
        quarter: str,
        fy_end_month: int | None,
        fy_end_day: int | None,
        target_end: "date",
    ) -> tuple[Decimal, str] | None:
        """Volle Quartals-D&A INKL. Intangible-Amort. Returns (value,
        currency) oder None. Gleiche Konzept-Prioritaet wie _fy_da_full,
        aber ueber den Quartals-Flow (Standalone-3M oder YTD-Differenz).
        D&A ist real immer positiv -> forbid_negative_diff."""
        da = self._q_flow(
            facts, EBITDA_DA_FULL_CONCEPTS, period_year, quarter,
            fy_end_month, fy_end_day, target_end, forbid_negative_diff=True,
        )
        if da is not None:
            return da[0], da[1]
        dep = self._q_flow(
            facts, EBITDA_DEPRECIATION_ONLY_CONCEPTS, period_year, quarter,
            fy_end_month, fy_end_day, target_end, forbid_negative_diff=True,
        )
        if dep is None:
            return None
        amort = self._q_flow(
            facts, EBITDA_INTANGIBLE_AMORT_CONCEPTS, period_year, quarter,
            fy_end_month, fy_end_day, target_end, forbid_negative_diff=True,
        )
        if amort is None or amort[1] != dep[1]:
            return None
        return dep[0] + amort[0], dep[1]

    def fetch_quarterly(
        self,
        ticker: str,
        key: str,
        period_year: int,
        quarter: str,
        fy_end_month: int | None = None,
        fy_end_day: int | None = None,
    ) -> ProviderResult | None:
        """Liefert einen Q-Actual aus 10-Q/10-K-XBRL fuer US-Filer.

        Drei Wege je nach Key:
          - BALANCE_KEYS: Instant-Fact zum Q-Stichtag (Q1-Q4; Q4 aus dem 10-K).
          - CASHFLOW_YTD_KEYS + fcf/ebitda-Komponenten: Standalone-3M-Frame,
            sonst YTD-Differenz (Q4 = FY-10-K minus 9M-YTD) — gleiche
            XBRL-Quelle wie das FY, Konsistenz per Konstruktion.
          - uebrige Keys (revenue, net_income, eps_diluted, ...): Standalone-
            Frame wie bisher, Q4 -> None (implied via FY minus Sigma Q1-Q3).
        Vorzeichen: Payments*-Tags kommen positiv aus XBRL und werden positiv
        durchgereicht — Sign-Normalisierung macht der zentrale Persistenz-Pfad.
        Returns None bei: nicht-US, kein CIK, kein Eintrag, nicht-supported
        Key, oder unbekanntem FY-Ende-Datum.
        """
        if quarter not in ("Q1", "Q2", "Q3", "Q4"):
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

        form = "10-K" if quarter == "Q4" else "10-Q"

        if key in BALANCE_KEYS:
            # Bilanz-Positionen sind Instant-Facts (kein "start") — direkt
            # Instant-Suche, Standalone-Duration greift dort nie.
            res = self._find_q_instant(facts, CONCEPT_MAP[key], target_end)
            if res is None:
                return None
            val, cur, accn = res
            return ProviderResult(
                value=val,
                source_name=f"SEC EDGAR {form} ({quarter} FY{period_year}, Bilanz-Stichtag)",
                source_link=self._filing_link(cik, accn),
                currency=cur if key in CURRENCY_KEYS else None,
            )

        if key == "fcf":
            ocf = self._q_flow(
                facts, FCF_OP_CASH_CONCEPTS, period_year, quarter,
                fy_end_month, fy_end_day, target_end,
            )
            capex = self._q_flow(
                facts, FCF_CAPEX_CONCEPTS, period_year, quarter,
                fy_end_month, fy_end_day, target_end,
                forbid_negative_diff=True,
            )
            if ocf is None or capex is None:
                return None
            ocf_val, cur, accn, ocf_method = ocf
            capex_val, capex_cur, _, capex_method = capex
            # Currency-Kreuzcheck: OCF und Capex muessen in derselben
            # Waehrung kommen, sonst ist die Differenz Aepfel-Birnen.
            if capex_cur != cur:
                return None
            suffix = ", YTD-Differenz" if "ytd_diff" in (ocf_method, capex_method) else ""
            return ProviderResult(
                value=ocf_val - abs(capex_val),
                source_name=f"SEC EDGAR {form} ({quarter} FY{period_year}, FCF = OCF - CapEx{suffix})",
                source_link=self._filing_link(cik, accn),
                currency=cur if "fcf" in CURRENCY_KEYS else None,
            )

        if key == "ebitda":
            # EBIT nur standalone — Income-Statement-Facts haben 3M-Frames.
            # STRIKT GAAP OperatingIncomeLoss (nie Non-GAAP/Adjusted-OI).
            ebit = self._find_q_standalone(facts, EBITDA_EBIT_CONCEPTS, target_end)
            if ebit is None:
                return None
            ebit_val, cur, accn = ebit
            # Volle D&A inkl. Intangible-Amortisation. Ist sie nicht
            # vollstaendig bestimmbar (nur Depreciation-only ohne separate
            # Intangible-Amort), liefert _q_da_full None -> KEIN EBITDA
            # schreiben (lieber leer als ein zu niedriger Wert).
            da = self._q_da_full(
                facts, period_year, quarter, fy_end_month, fy_end_day, target_end,
            )
            if da is None:
                return None
            da_val, da_cur = da
            # Currency-Kreuzcheck: EBIT und D&A muessen dieselbe Waehrung
            # tragen, sonst ist die Summe Aepfel-Birnen.
            if da_cur != cur:
                return None
            return ProviderResult(
                value=ebit_val + abs(da_val),
                source_name=f"SEC EDGAR {form} ({quarter} FY{period_year}, EBITDA = EBIT + D&A)",
                source_link=self._filing_link(cik, accn),
                currency=cur if "ebitda" in CURRENCY_KEYS else None,
            )

        if key in CASHFLOW_YTD_KEYS:
            res = self._q_flow(
                facts, CONCEPT_MAP[key], period_year, quarter,
                fy_end_month, fy_end_day, target_end,
                forbid_negative_diff=key in ALWAYS_POSITIVE_DIFF_KEYS,
            )
            if res is None:
                return None
            val, cur, accn, method = res
            if method == "ytd_diff":
                if quarter == "Q4":
                    source_name = f"SEC EDGAR 10-K (Q4 FY{period_year}, FY minus 9M-YTD)"
                else:
                    source_name = f"SEC EDGAR 10-Q ({quarter} FY{period_year}, YTD-Differenz)"
            else:
                source_name = f"SEC EDGAR {form} ({quarter} FY{period_year})"
            return ProviderResult(
                value=val,
                source_name=source_name,
                source_link=self._filing_link(cik, accn),
                currency=cur if key in CURRENCY_KEYS else None,
            )

        # Restliche Keys (revenue, net_income, eps_diluted, ...): Standalone
        # wie bisher; Q4 hat keine separaten 3M-Frames im 10-K -> None.
        if quarter == "Q4":
            return None
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
            capex, capex_cur, _ = self._find_value(facts, FCF_CAPEX_CONCEPTS, period_year, fy_end_month, fy_end_day)
            if ocf is None or capex is None:
                return None
            # Currency-Kreuzcheck wie im Quartalspfad: OCF und Capex muessen
            # in derselben Waehrung kommen.
            if capex_cur != cur:
                return None
            return ProviderResult(
                value=ocf - abs(capex),
                source_name=f"SEC EDGAR 10-K (FCF = OCF − CapEx, FY{period_year})",
                source_link=self._filing_link(cik, accn),
                currency=cur if "fcf" in CURRENCY_KEYS else None,
            )

        if key == "ebitda":
            # EBIT strikt GAAP OperatingIncomeLoss (nie Non-GAAP/Adjusted-OI).
            ebit, cur, accn = self._find_value(facts, EBITDA_EBIT_CONCEPTS, period_year, fy_end_month, fy_end_day)
            if ebit is None:
                return None
            # Volle D&A inkl. Amortisation immaterieller Werte. Nicht
            # vollstaendig bestimmbar -> KEIN EBITDA (lieber leer als ein zu
            # niedriger Wert; frueher EBIT-only-Approximation, jetzt entfernt).
            da = self._fy_da_full(facts, period_year, fy_end_month, fy_end_day)
            if da is None:
                return None
            da_val, da_cur, _ = da
            # Currency-Kreuzcheck EBIT vs D&A.
            if da_cur != cur:
                return None
            return ProviderResult(
                value=ebit + abs(da_val),
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

        # ST-Debt Sum-Handling: PM u.a. reporten ShortTermBorrowings separat von
        # Current Portion of Long-Term Debt. Fallback-Chain nimmt nur den ersten
        # Match und liefert damit einen Teilwert (z.B. 168M statt Total 3.7B).
        # Wenn DebtCurrent (Total-Concept) NICHT existiert, summieren wir die
        # Einzelbestandteile.
        if key == "st_debt":
            dc_val, dc_cur, dc_accn = self._find_value(
                facts, ["DebtCurrent"], period_year, fy_end_month, fy_end_day
            )
            if dc_val is not None:
                return ProviderResult(
                    value=dc_val,
                    source_name=f"SEC EDGAR 10-K DebtCurrent (FY{period_year})",
                    source_link=self._filing_link(cik, dc_accn),
                    currency=dc_cur if key in CURRENCY_KEYS else None,
                )
            components = [
                "ShortTermBorrowings",
                "LongTermDebtCurrent",
                "CommercialPaper",
                # PM u.a. reporten Current Portion of LT Debt unter alternativen
                # XBRL-Concepts — hier alle Aliase die keine Overlap-Gefahr haben
                "LongTermDebtAndCapitalLeaseObligationsCurrent",
                "NotesPayableCurrent",
                "OtherShortTermBorrowings",
                "SecuredDebtCurrent",
                "UnsecuredDebtCurrent",
            ]
            total = Decimal("0")
            found_any = False
            last_cur: str | None = None
            last_accn: str | None = None
            parts: list[str] = []
            for concept in components:
                v, cur_c, accn_c = self._find_value(
                    facts, [concept], period_year, fy_end_month, fy_end_day
                )
                if v is not None:
                    total += v
                    found_any = True
                    last_cur = cur_c
                    last_accn = accn_c
                    parts.append(f"{concept}={float(v)/1e6:,.0f}M")
            if found_any:
                return ProviderResult(
                    value=total,
                    source_name=f"SEC EDGAR 10-K ST-Debt Sum ({' + '.join(parts)}, FY{period_year})",
                    source_link=self._filing_link(cik, last_accn),
                    currency=last_cur if key in CURRENCY_KEYS else None,
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
