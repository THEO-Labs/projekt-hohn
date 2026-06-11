"""Single-Provider-Research via Claude.

Frueher Dual-Provider (Claude + Gemini); Gemini wurde entfernt weil dessen
Antworten haeufig methodisch schwaecher waren (EPS-Konversions-Verwechslungen,
Konsens-Aggregator-Halluzinationen) und das Mittelwert-Aggregat dadurch das
qualitativ bessere Claude-Result verwaessert hat. Claude bleibt als alleiniger
Web-Research-Provider.

Die DualResearchResult-Dataclass wird zur Caller-API-Kompatibilitaet beibehalten;
das gemini-Feld ist immer leer.
"""
import logging
from dataclasses import dataclass
from decimal import Decimal

from app.llm.claude import extract_research_value_adjusted
from app.llm.claude import research_value as research_value_claude

logger = logging.getLogger(__name__)


@dataclass
class ProviderResult:
    value: Decimal | None
    source: str | None
    url: str | None
    content: str | None  # raw response text fuer Erklaerung
    # Adjusted/Non-GAAP-Variante (nur fuer ni/ebitda/fcf relevant).
    value_adjusted: Decimal | None = None
    adjustments_note: str | None = None
    adjustments_source: str | None = None


@dataclass
class DualResearchResult:
    """Aggregiertes Ergebnis. Caller-Kompatibilitaet — gemini bleibt leer."""
    value: Decimal | None
    source: str | None
    url: str | None
    claude: ProviderResult
    gemini: ProviderResult
    divergent: bool
    providers_responded: int
    value_adjusted: Decimal | None = None
    adjustments_note: str | None = None
    adjustments_source: str | None = None


_EMPTY_GEMINI = ProviderResult(value=None, source=None, url=None, content=None)


def research_value_dual(
    company_name: str,
    ticker: str,
    value_label: str,
    currency: str,
    period_type: str = "FY",
    period_year: int | None = None,
    value_key: str | None = None,
    prev_fy_val: Decimal | None = None,
) -> DualResearchResult:
    """Claude-only Web-Recherche. Function-Name beibehalten fuer Caller-API-
    Kompatibilitaet; intern ist nichts mehr dual."""
    adjusted_relevant = value_key in {"net_income", "ebitda", "fcf"}

    try:
        v, s, u, _p, c = research_value_claude(
            company_name, ticker, value_label, currency,
            period_type=period_type, period_year=period_year, value_key=value_key,
            prev_fy_val=prev_fy_val,
        )
    except Exception as e:
        logger.warning("Claude research failed %s/%s: %s", ticker, value_key or "?", e)
        return DualResearchResult(
            value=None, source=None, url=None,
            claude=ProviderResult(value=None, source=None, url=None, content=None),
            gemini=_EMPTY_GEMINI,
            divergent=False, providers_responded=0,
        )

    adj_val: Decimal | None = None
    adj_src: str | None = None
    adj_note: str | None = None
    if c and adjusted_relevant:
        adj_val, adj_src, adj_note = extract_research_value_adjusted(c)

    claude_res = ProviderResult(
        value=v, source=s, url=u, content=c,
        value_adjusted=adj_val, adjustments_source=adj_src, adjustments_note=adj_note,
    )

    if v is None:
        return DualResearchResult(
            value=None, source=None, url=None,
            claude=claude_res, gemini=_EMPTY_GEMINI,
            divergent=False, providers_responded=0,
            value_adjusted=adj_val, adjustments_note=adj_note, adjustments_source=adj_src,
        )

    # Source-Name kompakt: Claude-Recherche-Praefix + raw Source (auf 3900 chars
    # gekuerzt damit DB-Spaltenlimit 4096 nicht ueberschritten wird).
    raw_source = (s or "KI-Einschätzung")[:3800]
    source = f"Claude-Recherche: {v:,.0f} {currency} | {raw_source}"[:3900]

    return DualResearchResult(
        value=v,
        source=source,
        url=u,
        claude=claude_res,
        gemini=_EMPTY_GEMINI,
        divergent=False,
        providers_responded=1,
        value_adjusted=adj_val,
        adjustments_note=(adj_note or "")[:4000] or None,
        adjustments_source=(adj_src or "")[:2048] or None,
    )
