"""Dual-Provider-Research: parallel Claude + Gemini, Mittelwert + Divergenz-Flag.

Wird von values/routes.py und ir_documents/routes.py genutzt statt direkt
research_value(). Wenn nur Anthropic-Key konfiguriert ist, faellt es auf
Claude-only zurueck (kein behaviour change). Wenn beide Provider konfiguriert
sind, werden beide parallel aufgerufen und der Mittelwert persistiert.

Aggregation-Logik:
  - Beide liefern Werte: arithmetisches Mittel.
  - Diff > 50%: Mittel mit Divergenz-Flag im source_name.
  - Nur 1 Provider liefert: dieser Wert (kein Mittel).
  - Beide failen: None.
"""
import concurrent.futures
import logging
from dataclasses import dataclass, field
from decimal import Decimal

from app.config import settings
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
    """Aggregiertes Ergebnis aus beiden Providern."""
    # Aggregierter Wert (Mittel oder Single-Provider-Wert)
    value: Decimal | None
    # Aggregierter Source-Name (zeigt beide Provider und Mittel)
    source: str | None
    # Bevorzugter URL: Claude > Gemini (Claude tendiert zu spezifischeren URLs)
    url: str | None
    # Einzel-Ergebnisse fuer Persistierung in forecast_alternates
    claude: ProviderResult
    gemini: ProviderResult
    # Divergenz-Warning (Diff > 50%)
    divergent: bool
    # Anzahl Provider die einen Wert geliefert haben (0/1/2)
    providers_responded: int
    # Aggregierter Adjusted-Wert (Mittel beider Provider falls vorhanden)
    value_adjusted: Decimal | None = None
    adjustments_note: str | None = None
    adjustments_source: str | None = None


def _pct_diff(a: Decimal, b: Decimal) -> float:
    """Relative Abweichung |a-b| / max(|a|, |b|). 0..1+. Vorzeichen-aware:
    bei gegensaetzlichen Vorzeichen ist diff sehr hoch."""
    fa, fb = float(a), float(b)
    if fa == 0 and fb == 0:
        return 0.0
    if (fa * fb) < 0:
        # Vorzeichen-Mismatch: vermutlich Konzept-Divergenz (z.B. Net Cash vs Net Debt)
        return 2.0
    denom = max(abs(fa), abs(fb))
    if denom == 0:
        return 0.0
    return abs(fa - fb) / denom


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
    """Parallel Claude + Gemini Recherche. Faellt auf Claude-only zurueck
    wenn GEMINI_API_KEY nicht gesetzt ist."""
    gemini_available = bool(settings.gemini_api_key)

    # Adjusted nur fuer NI/EBITDA/FCF relevant — andere Keys haben per Definition
    # keine Non-GAAP-Variante (SBC, Buyback, Dividends sind Cash-Flows; Net Debt
    # ist Bilanz-Snapshot; Shares Outstanding ist physisch).
    adjusted_relevant = value_key in {"net_income", "ebitda", "fcf"}

    def _parse_adjusted(content: str | None) -> tuple[Decimal | None, str | None, str | None]:
        if not content or not adjusted_relevant:
            return None, None, None
        return extract_research_value_adjusted(content)

    # Beide Provider parallel mit ThreadPoolExecutor (synchroner SDK-Code).
    def _call_claude() -> ProviderResult:
        try:
            v, s, u, _p, c = research_value_claude(
                company_name, ticker, value_label, currency,
                period_type=period_type, period_year=period_year, value_key=value_key,
                prev_fy_val=prev_fy_val,
            )
            adj, adj_src, adj_note = _parse_adjusted(c)
            return ProviderResult(
                value=v, source=s, url=u, content=c,
                value_adjusted=adj, adjustments_source=adj_src, adjustments_note=adj_note,
            )
        except Exception as e:
            logger.warning("Dual research: Claude-Provider failed %s/%s: %s",
                           ticker, value_key or "?", e)
            return ProviderResult(value=None, source=None, url=None, content=None)

    def _call_gemini() -> ProviderResult:
        if not gemini_available:
            return ProviderResult(value=None, source=None, url=None, content=None)
        try:
            from app.llm.gemini import research_value_gemini
            v, s, u, _p, c = research_value_gemini(
                company_name, ticker, value_label, currency,
                period_type=period_type, period_year=period_year, value_key=value_key,
                prev_fy_val=prev_fy_val,
            )
            adj, adj_src, adj_note = _parse_adjusted(c)
            return ProviderResult(
                value=v, source=s, url=u, content=c,
                value_adjusted=adj, adjustments_source=adj_src, adjustments_note=adj_note,
            )
        except Exception as e:
            logger.warning("Dual research: Gemini-Provider failed %s/%s: %s",
                           ticker, value_key or "?", e)
            return ProviderResult(value=None, source=None, url=None, content=None)

    if gemini_available:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut_claude = executor.submit(_call_claude)
            fut_gemini = executor.submit(_call_gemini)
            claude_res = fut_claude.result()
            gemini_res = fut_gemini.result()
    else:
        # Nur Claude — kein Overhead vom Executor.
        claude_res = _call_claude()
        gemini_res = ProviderResult(value=None, source=None, url=None, content=None)

    # Aggregation
    cv, gv = claude_res.value, gemini_res.value
    # Pro-Provider-Source auf 1700 chars truncaten damit Konsens-Range +
    # Quartals-Aufschluesselung + per-Quartal-Begruendungen alle reinpassen.
    # DB-Spaltenlimit ist 4096 — mit 2x1700 + ~150 Overhead = 3550 chars Puffer.
    def _short(s: str | None) -> str:
        return (s or "KI-Einschätzung")[:1700]

    # Adjusted-Aggregation: gleiche Mean-Logik wie Reported. Nur wenn beide
    # Provider Adjusted geliefert haben mitteln. Sonst Single-Provider-Wert.
    cv_adj, gv_adj = claude_res.value_adjusted, gemini_res.value_adjusted
    aggregated_adjusted: Decimal | None
    aggregated_adj_note: str | None
    aggregated_adj_source: str | None
    if cv_adj is not None and gv_adj is not None:
        aggregated_adjusted = (cv_adj + gv_adj) / Decimal("2")
        adj_diff = _pct_diff(cv_adj, gv_adj)
        adj_div_marker = "⚠ DIVERGENZ " if adj_diff > 0.5 else ""
        aggregated_adj_note = (
            f"{adj_div_marker}Claude: {cv_adj:,.0f} ({claude_res.adjustments_note or '—'}) "
            f"| Gemini: {gv_adj:,.0f} ({gemini_res.adjustments_note or '—'})"
        )[:4000]
        aggregated_adj_source = (
            f"Claude: {claude_res.adjustments_source or '—'} | Gemini: {gemini_res.adjustments_source or '—'}"
        )[:2048]
    elif cv_adj is not None:
        aggregated_adjusted = cv_adj
        aggregated_adj_note = (claude_res.adjustments_note or "")[:4000] or None
        aggregated_adj_source = (claude_res.adjustments_source or "")[:2048] or None
    elif gv_adj is not None:
        aggregated_adjusted = gv_adj
        aggregated_adj_note = (gemini_res.adjustments_note or "")[:4000] or None
        aggregated_adj_source = (gemini_res.adjustments_source or "")[:2048] or None
    else:
        aggregated_adjusted = None
        aggregated_adj_note = None
        aggregated_adj_source = None

    if cv is not None and gv is not None:
        mean_val = (cv + gv) / Decimal("2")
        diff = _pct_diff(cv, gv)
        divergent = diff > 0.5
        if divergent:
            source = (
                f"⚠ DIVERGENZ ({diff*100:.0f}% Abweichung) — Mittel: {mean_val:,.0f} "
                f"{currency} | Claude: {cv:,.0f} ({_short(claude_res.source)}) "
                f"| Gemini: {gv:,.0f} ({_short(gemini_res.source)})"
            )
        else:
            source = (
                f"Konsens (Claude+Gemini Mittel): {mean_val:,.0f} {currency} "
                f"| Claude: {cv:,.0f} ({_short(claude_res.source)}) "
                f"| Gemini: {gv:,.0f} ({_short(gemini_res.source)})"
            )
        return DualResearchResult(
            value=mean_val,
            source=source[:3900],  # safety margin unter DB-Spaltenlimit 4096
            url=claude_res.url or gemini_res.url,
            claude=claude_res,
            gemini=gemini_res,
            divergent=divergent,
            providers_responded=2,
            value_adjusted=aggregated_adjusted,
            adjustments_note=aggregated_adj_note,
            adjustments_source=aggregated_adj_source,
        )
    if cv is not None:
        return DualResearchResult(
            value=cv,
            source=(f"Claude-only (Gemini lieferte nichts): {_short(claude_res.source)}")[:3900],
            url=claude_res.url,
            claude=claude_res,
            gemini=gemini_res,
            divergent=False,
            providers_responded=1,
            value_adjusted=aggregated_adjusted,
            adjustments_note=aggregated_adj_note,
            adjustments_source=aggregated_adj_source,
        )
    if gv is not None:
        return DualResearchResult(
            value=gv,
            source=(f"Gemini-only (Claude lieferte nichts): {_short(gemini_res.source)}")[:3900],
            url=gemini_res.url,
            claude=claude_res,
            gemini=gemini_res,
            divergent=False,
            providers_responded=1,
            value_adjusted=aggregated_adjusted,
            adjustments_note=aggregated_adj_note,
            adjustments_source=aggregated_adj_source,
        )
    return DualResearchResult(
        value=None,
        source=None,
        url=None,
        claude=claude_res,
        gemini=gemini_res,
        divergent=False,
        providers_responded=0,
    )
