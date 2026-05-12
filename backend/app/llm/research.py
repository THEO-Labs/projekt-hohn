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
from dataclasses import dataclass
from decimal import Decimal

from app.config import settings
from app.llm.claude import research_value as research_value_claude

logger = logging.getLogger(__name__)


@dataclass
class ProviderResult:
    value: Decimal | None
    source: str | None
    url: str | None
    content: str | None  # raw response text fuer Erklaerung


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

    # Beide Provider parallel mit ThreadPoolExecutor (synchroner SDK-Code).
    def _call_claude() -> ProviderResult:
        try:
            v, s, u, _p, c = research_value_claude(
                company_name, ticker, value_label, currency,
                period_type=period_type, period_year=period_year, value_key=value_key,
                prev_fy_val=prev_fy_val,
            )
            return ProviderResult(value=v, source=s, url=u, content=c)
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
            return ProviderResult(value=v, source=s, url=u, content=c)
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
    # Pro-Provider-Source auf 600 chars truncaten damit auch lange Begruendungen
    # nicht das DB-source_name-Limit (2048 chars) sprengen. Bei Konsens-Strings
    # mit beiden Providers bleibt noch Platz fuer Praefix+Mittel-Werte.
    def _short(s: str | None) -> str:
        return (s or "KI-Einschätzung")[:600]

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
            source=source[:1900],  # extra safety margin unter DB-Limit 2048
            url=claude_res.url or gemini_res.url,
            claude=claude_res,
            gemini=gemini_res,
            divergent=divergent,
            providers_responded=2,
        )
    if cv is not None:
        return DualResearchResult(
            value=cv,
            source=(f"Claude-only (Gemini lieferte nichts): {_short(claude_res.source)}")[:1900],
            url=claude_res.url,
            claude=claude_res,
            gemini=gemini_res,
            divergent=False,
            providers_responded=1,
        )
    if gv is not None:
        return DualResearchResult(
            value=gv,
            source=(f"Gemini-only (Claude lieferte nichts): {_short(gemini_res.source)}")[:1900],
            url=gemini_res.url,
            claude=claude_res,
            gemini=gemini_res,
            divergent=False,
            providers_responded=1,
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
