"""Exakte Werte direkt aus der XBRL-Instanz eines konkreten Filings.

Die aggregierte companyfacts-API (EdgarProvider) hinkt frisch eingereichten
10-Q/10-K/8-K hinterher — Tage bis Wochen. Fuer ein gerade berichtetes Quartal,
dessen Werte noch NICHT in companyfacts stehen (Filing-Lag), laedt dieser
Provider die XBRL-Instanz des konkreten Filings und extrahiert exakte Werte per
Concept — dieselben us-gaap-Tags wie EdgarProvider, nur aus der Einzelquelle
statt aus dem Aggregat. Kein LLM-Raten, keine gerundeten Narrativ-Zahlen.

Extraktion je Kennzahl-Typ:
- Income-Statement (revenue, net_income): 3-Monats-Duration mit Ende am
  Quartalsende -> direkter Quartalswert.
- Cashflow (OCF, capex, buyback, dividends, sbc): im 10-Q nur als YTD-Duration
  getaggt -> Rueckgabe als YTD; der Aufrufer differenziert gegen die
  Vorquartale (Q_n = YTD_n - Summe(Q_1..Q_n-1)).
- Bilanz (cash, st_investments, st_debt, lt_debt): Instant am Quartalsende.
- diluted_shares: 3-Monats-Duration (fuer exaktes eps = net_income / shares,
  wo der Filer EPS nur mit Aktien-Klassen-Dimension taggt, z.B. Visa).
"""
import logging
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import httpx
from cachetools import TTLCache

from app.providers.edgar import CONCEPT_MAP, USER_AGENT

logger = logging.getLogger(__name__)

_XBRLI = "{http://www.xbrl.org/2003/instance}"

# Kennzahl-Typen (Extraktions-Modus). st_investments erweitert um das generische
# "Investments" (Visa taggt seine kurzfristigen Marktwertpapiere so, nicht als
# ShortTermInvestments) — bewusst als LETZTER Fallback.
_QUARTER_FLOW_KEYS = ("revenue", "net_income")
_YTD_FLOW_KEYS = ("operating_cash_flow", "capex", "buyback_volume", "dividends", "sbc")
_BALANCE_KEYS = ("cash_and_equivalents", "st_investments", "st_debt", "lt_debt")
_DILUTED_SHARE_CONCEPTS = [
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingDiluted",
]


def _concepts(key: str) -> list[str]:
    # CONCEPT_MAP enthaelt fuer st_investments bereits den generischen
    # "Investments"-Fallback (Visa-Tagging).
    return CONCEPT_MAP.get(key, [])


@dataclass(frozen=True)
class _Ctx:
    start: date | None
    end: date | None
    instant: date | None
    has_segment: bool


@dataclass
class FilingQuarter:
    """Extrahierte Werte eines Quartals-Filings."""
    quarter_values: dict[str, Decimal] = field(default_factory=dict)  # 3M-Duration
    ytd_values: dict[str, Decimal] = field(default_factory=dict)      # 9M-YTD
    balance_values: dict[str, Decimal] = field(default_factory=dict)  # Instant
    diluted_shares: Decimal | None = None
    source_url: str | None = None
    quarter_end: date | None = None
    fy_start: date | None = None


def _parse_date(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def _to_decimal(text: str | None) -> Decimal | None:
    if text is None:
        return None
    try:
        return Decimal(text.strip())
    except (InvalidOperation, AttributeError):
        return None


class EdgarFilingProvider:
    name = "SEC EDGAR (Filing-XBRL)"

    def __init__(self) -> None:
        self._ticker_to_cik: dict[str, str] | None = None
        # (concept-facts, contexts, source_url) je Accession kurz cachen.
        self._instance_cache: TTLCache = TTLCache(maxsize=64, ttl=3600)
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=30.0,
            follow_redirects=True,
            transport=httpx.HTTPTransport(retries=3),
        )

    # ---- HTTP / Discovery -------------------------------------------------

    def _get(self, url: str) -> httpx.Response | None:
        try:
            r = self._client.get(url)
            if r.status_code >= 400:
                logger.info("EDGAR-Filing GET %s -> %s", url, r.status_code)
                return None
            return r
        except Exception as e:  # noqa: BLE001
            logger.info("EDGAR-Filing GET %s fehlgeschlagen: %s", url, e)
            return None

    def _get_cik(self, ticker: str) -> str | None:
        if self._ticker_to_cik is None:
            r = self._get("https://www.sec.gov/files/company_tickers.json")
            if r is None:
                return None
            try:
                data = r.json()
                self._ticker_to_cik = {
                    item["ticker"].upper(): str(item["cik_str"]).zfill(10)
                    for item in data.values()
                }
            except Exception as e:  # noqa: BLE001
                logger.info("EDGAR-Filing ticker-map parse: %s", e)
                return None
        return self._ticker_to_cik.get(ticker.upper())

    def _find_filing(self, cik: str, quarter_end: date) -> tuple[str, date] | None:
        """Juengstes 10-Q/10-K, dessen Berichtsperiode (reportDate) ~ quarter_end
        (±10 Tage) ist. Rueckgabe (accession-mit-Bindestrich, actual_report_date)."""
        r = self._get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        if r is None:
            return None
        try:
            recent = r.json()["filings"]["recent"]
        except (KeyError, ValueError):
            return None
        forms = recent.get("form", [])
        report_dates = recent.get("reportDate", [])
        accns = recent.get("accessionNumber", [])
        filed = recent.get("filingDate", [])
        best: tuple[str, date, str] | None = None  # (accn, report_date, filed)
        for i in range(min(len(forms), len(report_dates), len(accns))):
            if forms[i] not in ("10-Q", "10-K"):
                continue
            rd = _parse_date(report_dates[i])
            if rd is None or abs((rd - quarter_end).days) > 10:
                continue
            fd = filed[i] if i < len(filed) else "0000"
            if best is None or fd > best[2]:
                best = (accns[i], rd, fd)
        if best is None:
            return None
        return best[0], best[1]

    def _load_instance(self, cik: str, accn: str) -> tuple[dict[str, list], str] | None:
        """XBRL-Instanz des Filings laden und Facts (concept -> [(ctx, val)])
        plus Quell-URL zurueckgeben. Gecacht je Accession."""
        if accn in self._instance_cache:
            return self._instance_cache[accn]
        accn_clean = accn.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn_clean}"
        idx = self._get(f"{base}/index.json")
        if idx is None:
            return None
        try:
            items = idx.json()["directory"]["item"]
        except (KeyError, ValueError):
            return None
        names = [it["name"] for it in items if it.get("name", "").endswith(".xml")]
        linkbase = ("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml")
        cand = [n for n in names if not n.endswith(linkbase)]
        # Inline-XBRL-Instanz endet auf "_htm.xml"; sonst erster Nicht-Linkbase.
        instance_name = next((n for n in cand if n.endswith("_htm.xml")), None) or (cand[0] if cand else None)
        if instance_name is None:
            return None
        doc = self._get(f"{base}/{instance_name}")
        if doc is None:
            return None
        try:
            facts = self._parse_instance(doc.content)
        except ET.ParseError as e:
            logger.info("EDGAR-Filing XBRL-Parse %s: %s", accn, e)
            return None
        source_url = f"{base}/{accn}-index.htm"
        self._instance_cache[accn] = (facts, source_url)
        return facts, source_url

    @staticmethod
    def _parse_instance(xml_bytes: bytes) -> dict[str, list]:
        root = ET.fromstring(xml_bytes)
        gaap_ns: str | None = None
        for el in root.iter():
            if el.tag.startswith("{http://fasb.org/us-gaap/"):
                gaap_ns = el.tag[: el.tag.index("}") + 1]
                break
        ctx: dict[str, _Ctx] = {}
        for c in root.iter(_XBRLI + "context"):
            per = c.find(_XBRLI + "period")
            ent = c.find(_XBRLI + "entity")
            seg = ent.find(_XBRLI + "segment") if ent is not None else None
            s = per.find(_XBRLI + "startDate") if per is not None else None
            e = per.find(_XBRLI + "endDate") if per is not None else None
            inst = per.find(_XBRLI + "instant") if per is not None else None
            ctx[c.get("id")] = _Ctx(
                start=_parse_date(s.text if s is not None else None),
                end=_parse_date(e.text if e is not None else None),
                instant=_parse_date(inst.text if inst is not None else None),
                has_segment=seg is not None,
            )
        facts: dict[str, list] = {}
        if gaap_ns is None:
            return facts
        nlen = len(gaap_ns)
        for el in root.iter():
            tag = el.tag
            if not tag.startswith(gaap_ns):
                continue
            cref = el.get("contextRef")
            c = ctx.get(cref)
            if c is None or c.has_segment:
                continue  # nur konsolidierte (dimensionslose) Facts
            val = _to_decimal(el.text)
            if val is None:
                continue
            facts.setdefault(tag[nlen:], []).append((c, val))
        return facts

    # ---- Extraktion -------------------------------------------------------

    @staticmethod
    def _consensus(values: list[Decimal]) -> Decimal | None:
        """Haeufigsten Wert waehlen (dieselbe Kennzahl taucht in mehreren
        Statement-Praesentationen identisch auf); bei Gleichstand den ersten."""
        if not values:
            return None
        counts = Counter(values)
        top = counts.most_common(1)[0][1]
        for v in values:  # Reihenfolge-stabil bei Gleichstand
            if counts[v] == top:
                return v
        return values[0]

    def _duration(self, facts, concepts, *, end: date, min_days: int, max_days: int,
                  start: date | None = None) -> Decimal | None:
        for concept in concepts:
            hits: list[Decimal] = []
            for c, val in facts.get(concept, []):
                if c.start is None or c.end is None:
                    continue
                if abs((c.end - end).days) > 7:
                    continue
                span = (c.end - c.start).days
                if not (min_days <= span <= max_days):
                    continue
                if start is not None and abs((c.start - start).days) > 12:
                    continue
                hits.append(val)
            got = self._consensus(hits)
            if got is not None:
                return got
        return None

    def _instant(self, facts, concepts, *, at: date, tol: int = 7) -> Decimal | None:
        for concept in concepts:
            hits = [val for c, val in facts.get(concept, [])
                    if c.instant is not None and abs((c.instant - at).days) <= tol]
            got = self._consensus(hits)
            if got is not None:
                return got
        return None

    def fetch_quarter(self, *, ticker: str, fiscal_year: int, quarter: str,
                      fy_end_month: int | None, fy_end_day: int | None) -> FilingQuarter | None:
        """Exakte Werte des Quartals-Filings, oder None wenn kein passendes
        Filing/keine Instanz gefunden wird (dann faellt der Aufrufer auf
        Perplexity zurueck)."""
        if not fy_end_month or not fy_end_day:
            return None
        cik = self._get_cik(ticker)
        if cik is None:
            return None
        target_end = _quarter_end(fiscal_year, quarter, fy_end_month, fy_end_day)
        if target_end is None:
            return None
        found = self._find_filing(cik, target_end)
        if found is None:
            return None
        accn, report_date = found
        loaded = self._load_instance(cik, accn)
        if loaded is None:
            return None
        facts, source_url = loaded
        end = report_date  # exaktes Perioden-Ende aus dem Filing
        fy_start = _fy_start(fiscal_year, fy_end_month, fy_end_day)
        out = FilingQuarter(source_url=source_url, quarter_end=end, fy_start=fy_start)
        # Income-Statement: direktes 3-Monats-Quartal (Span ~ 85-95 Tage).
        for key in _QUARTER_FLOW_KEYS:
            v = self._duration(facts, _concepts(key), end=end, min_days=80, max_days=100)
            if v is not None:
                out.quarter_values[key] = v
        # Cashflow: YTD (Span variabel: Q1~90, Q2~180, Q3~270 Tage).
        for key in _YTD_FLOW_KEYS:
            v = self._duration(facts, _concepts(key), end=end, min_days=60, max_days=400,
                               start=fy_start)
            if v is not None:
                out.ytd_values[key] = v
        # Bilanz: Instant am Perioden-Ende.
        for key in _BALANCE_KEYS:
            v = self._instant(facts, _concepts(key), at=end)
            if v is not None:
                out.balance_values[key] = v
        # Diluted shares (3M) fuer exaktes eps.
        out.diluted_shares = self._duration(
            facts, _DILUTED_SHARE_CONCEPTS, end=end, min_days=80, max_days=100)
        return out


# ---- Perioden-Mathematik (dupliziert bewusst die EdgarProvider-Helfer, ohne
#      eine Instanz zu benoetigen) ------------------------------------------

def _quarter_end(period_year: int, quarter: str, fy_end_month: int, fy_end_day: int) -> date | None:
    import calendar
    if quarter not in ("Q1", "Q2", "Q3", "Q4"):
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


def _fy_start(period_year: int, fy_end_month: int, fy_end_day: int) -> date | None:
    prev_q4 = _quarter_end(period_year - 1, "Q4", fy_end_month, fy_end_day)
    if prev_q4 is None:
        return None
    return prev_q4 + timedelta(days=1)
