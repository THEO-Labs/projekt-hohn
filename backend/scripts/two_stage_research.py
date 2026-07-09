"""Two-Stage Research Framework: Extractor + Verifier for each metric.

Design (per user request):

    HISTORIC FY (2024, 2025 — closed years):
        Stage 1 (Extractor):  1 prompt per (company, value_key, year)
                              -> returns Q1+Q2+Q3+Q4+FY together, all with
                                 source_quote + source_url
        Stage 2 (Verifier):   1 prompt per response -> Q-Sum-Check,
                              Reported-vs-Adjusted challenge, anti-confusion

    CURRENT YEAR (2026 — Q1 actual, Q2-Q4 forecasts):
        Stage 1: 1 prompt per (company, value_key, quarter)
        Stage 2: 1 verifier prompt with anti-confusion + YoY cross-check
        FY 2026: separate FY-Guidance extraction (not Sum(Q))

Verifier always receives the raw source_quote + URL from Stage 1. Without
ground truth the verifier hallucinates. Per-metric anti-confusion rules
are loaded from backend/scripts/prompts/{value_key}.md.

Usage (from repo root):
    uv run python -m scripts.two_stage_research \\
        --portfolio-id b3a10032-c646-4036-97eb-ee72331ae423 \\
        --year 2025 \\
        --keys revenue,net_income,ebitda,dividends \\
        --mode historic
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Literal

import anthropic

# Reuse the existing client + rate limiter so we honor the same
# per-minute budget as the production extractor.
from app.config import settings
from app.llm.claude import get_client
from app.llm.rate_limiter import RateLimiter


# --- Prompt files ---------------------------------------------------------

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def load_metric_prompt(value_key: str) -> str:
    """Load the anti-confusion / definition Markdown for a metric.

    These files sit next to this script under prompts/{key}.md and were
    hand-curated to encode the "gotchas" per metric (per-share vs total,
    reported vs adjusted, dual-class handling, etc).
    """
    p = PROMPTS_DIR / f"{value_key}.md"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


# --- Data structures ------------------------------------------------------

Mode = Literal["historic", "current"]


@dataclass
class QuarterValue:
    value: Decimal | None
    source_quote: str | None
    source_url: str | None


@dataclass
class ExtractResult:
    """Stage 1 output: extracted values + raw source evidence."""

    ticker: str
    value_key: str
    year: int
    currency: str
    q1: QuarterValue | None
    q2: QuarterValue | None
    q3: QuarterValue | None
    q4: QuarterValue | None
    fy: QuarterValue | None
    quarter_only: str | None  # e.g. "Q1" if current-year single-quarter mode
    is_adjusted_note: str | None  # extractor flags if only adjusted found

    def to_verifier_json(self) -> dict:
        def _q(qv: QuarterValue | None) -> dict | None:
            if qv is None:
                return None
            return {
                "value": str(qv.value) if qv.value is not None else None,
                "source_quote": qv.source_quote,
                "source_url": qv.source_url,
            }

        return {
            "ticker": self.ticker,
            "value_key": self.value_key,
            "year": self.year,
            "currency": self.currency,
            "quarter_only": self.quarter_only,
            "q1": _q(self.q1),
            "q2": _q(self.q2),
            "q3": _q(self.q3),
            "q4": _q(self.q4),
            "fy": _q(self.fy),
            "extractor_note_adjusted_vs_reported": self.is_adjusted_note,
        }


@dataclass
class VerifierVerdict:
    """Stage 2 output: challenge result. Either confirms extracted values
    or returns corrected values, always with a reason string."""

    verdict: Literal["confirm", "correct", "insufficient_evidence"]
    corrections: dict[str, Decimal | None]  # {"Q1": ..., "FY": ...}
    reason: str
    confidence: float
    flags: list[str]  # e.g. ["adjusted_not_reported", "per_share_confusion"]

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "corrections": {
                k: (str(v) if v is not None else None) for k, v in self.corrections.items()
            },
            "reason": self.reason,
            "confidence": self.confidence,
            "flags": self.flags,
        }


# --- Stage 1: Extractor ---------------------------------------------------


EXTRACTOR_SYSTEM = (
    "You are a precise financial-data extractor. Return ONLY a valid JSON "
    "object matching the schema in the user message — no prose, no markdown. "
    "For every number you output you MUST include the exact quoted sentence "
    "from the source document (source_quote) and the URL (source_url). "
    "If a value cannot be verified against a source quote, set it to null. "
    "Do NOT extrapolate, do NOT estimate — only report what the document "
    "actually says."
)


def _build_historic_extractor_prompt(
    ticker: str, company_name: str, value_key: str, year: int, currency: str
) -> str:
    metric_context = load_metric_prompt(value_key)

    schema = """{
  "q1": {"value": <number in absolute """ + currency + """|null>, "source_quote": <string|null>, "source_url": <string|null>},
  "q2": {"value": <number|null>, "source_quote": <string|null>, "source_url": <string|null>},
  "q3": {"value": <number|null>, "source_quote": <string|null>, "source_url": <string|null>},
  "q4": {"value": <number|null>, "source_quote": <string|null>, "source_url": <string|null>},
  "fy": {"value": <number|null>, "source_quote": <string|null>, "source_url": <string|null>},
  "extractor_note_adjusted_vs_reported": <string|null: describe if source only shows Adjusted/Core/Bereinigt>
}"""

    return f"""Task: Extract `{value_key}` for {company_name} ({ticker}), fiscal year {year}.
Currency: {currency}. Report all values in ABSOLUTE {currency} (no millions, no per-share).

Return Q1, Q2, Q3, Q4 AND full-year FY {year} — all in one JSON object.

Ground rules (metric-specific, from backend/scripts/prompts/{value_key}.md):

{metric_context}

For each value MUST provide:
- `value`: the number itself, absolute {currency}, IFRS reported (NOT adjusted unless no reported exists)
- `source_quote`: the exact sentence from the annual report / quarterly report you took the number from
- `source_url`: URL of the source document

Consistency: Q1 + Q2 + Q3 + Q4 should equal FY within 0.5% for flow metrics.

If the company only reports Adjusted / Core / Bereinigt values (not IFRS reported),
set the values but populate `extractor_note_adjusted_vs_reported` explaining which
non-IFRS metric you used and why reported was not available.

Return JSON matching exactly this schema:

{schema}
"""


def _build_current_quarter_extractor_prompt(
    ticker: str, company_name: str, value_key: str, year: int, quarter: str, currency: str
) -> str:
    metric_context = load_metric_prompt(value_key)

    schema = f"""{{
  "{quarter.lower()}": {{"value": <number in absolute {currency}|null>, "source_quote": <string|null>, "source_url": <string|null>}},
  "extractor_note_adjusted_vs_reported": <string|null>
}}"""

    return f"""Task: Extract `{value_key}` for {company_name} ({ticker}), {quarter} {year} STANDALONE.
Currency: {currency}. Report in ABSOLUTE {currency}.

This is the CURRENT year — only the single quarter {quarter} is required.
If {quarter} {year} report is not yet published, set value to null (do NOT estimate).

Ground rules (metric-specific):

{metric_context}

STANDALONE means the isolated quarter (Q1 = Jan-Mar), NOT year-to-date cumulative.
If source shows YTD, subtract prior quarters.

Return JSON:

{schema}
"""


def _extract_json(text: str) -> dict:
    """Robust JSON extraction from LLM text output."""
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to find first {...} block
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON object in extractor response")
    return json.loads(m.group(0))


def _to_qv(raw: dict | None) -> QuarterValue | None:
    if not raw:
        return None
    val = raw.get("value")
    dec: Decimal | None = None
    if val is not None:
        try:
            dec = Decimal(str(val))
        except Exception:
            dec = None
    return QuarterValue(
        value=dec,
        source_quote=raw.get("source_quote"),
        source_url=raw.get("source_url"),
    )


def run_extractor(
    ticker: str,
    company_name: str,
    value_key: str,
    year: int,
    currency: str,
    mode: Mode,
    quarter: str | None = None,
    limiter: RateLimiter | None = None,
    model: str = "claude-sonnet-4-6",
) -> ExtractResult:
    """Stage 1: fetch values with source quotes."""
    client = get_client()
    limiter = limiter or RateLimiter()

    if mode == "historic":
        prompt = _build_historic_extractor_prompt(ticker, company_name, value_key, year, currency)
    else:
        if not quarter:
            raise ValueError("current-year mode requires a quarter (Q1..Q4)")
        prompt = _build_current_quarter_extractor_prompt(
            ticker, company_name, value_key, year, quarter, currency
        )

    def _call() -> anthropic.types.Message:
        return client.messages.create(
            model=model,
            max_tokens=2048,
            system=EXTRACTOR_SYSTEM,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
            messages=[{"role": "user", "content": prompt}],
        )

    response = limiter.call(_call)

    text_parts = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            text_parts.append(text)
    raw_text = "\n".join(text_parts).strip()

    payload = _extract_json(raw_text)

    if mode == "historic":
        return ExtractResult(
            ticker=ticker,
            value_key=value_key,
            year=year,
            currency=currency,
            q1=_to_qv(payload.get("q1")),
            q2=_to_qv(payload.get("q2")),
            q3=_to_qv(payload.get("q3")),
            q4=_to_qv(payload.get("q4")),
            fy=_to_qv(payload.get("fy")),
            quarter_only=None,
            is_adjusted_note=payload.get("extractor_note_adjusted_vs_reported"),
        )
    else:
        assert quarter is not None
        q_lower = quarter.lower()
        qv = _to_qv(payload.get(q_lower))
        return ExtractResult(
            ticker=ticker,
            value_key=value_key,
            year=year,
            currency=currency,
            q1=qv if quarter == "Q1" else None,
            q2=qv if quarter == "Q2" else None,
            q3=qv if quarter == "Q3" else None,
            q4=qv if quarter == "Q4" else None,
            fy=None,
            quarter_only=quarter,
            is_adjusted_note=payload.get("extractor_note_adjusted_vs_reported"),
        )


# --- Stage 2: Verifier ----------------------------------------------------


VERIFIER_SYSTEM = (
    "You are a Devil's-Advocate financial-data verifier. You will receive "
    "a set of extracted values PLUS the exact source_quote and source_url "
    "each value came from. Your job is to challenge — not confirm — every "
    "number. Look for: per-share vs total confusion; adjusted vs IFRS reported "
    "confusion; continuing vs discontinued operations mixups; currency errors; "
    "unit-scale errors (thousands vs millions vs billions); dual-class share "
    "gotchas; Q-sum vs FY-total inconsistencies. "
    "Return ONLY a JSON verdict per schema. If the source_quote does NOT "
    "unambiguously support the extracted number, output verdict='correct' "
    "with your corrected value AND explain in `reason` exactly which words "
    "in the source_quote led to your correction. If genuinely uncertain, "
    "output verdict='insufficient_evidence' with corrections={} rather than "
    "guessing."
)


def _build_verifier_prompt(
    extract: ExtractResult,
    prev_year_fy_hint: Decimal | None = None,
) -> str:
    metric_context = load_metric_prompt(extract.value_key)
    payload = extract.to_verifier_json()

    prev_hint = ""
    if prev_year_fy_hint is not None:
        prev_hint = (
            f"\n\nContext: FY {extract.year - 1} value for this metric was "
            f"{prev_year_fy_hint} {extract.currency}. Extreme YoY jumps "
            f"(>100% or reversal without known one-off) are red flags."
        )

    schema = """{
  "verdict": "confirm" | "correct" | "insufficient_evidence",
  "corrections": {"Q1": <number|null>, "Q2": <number|null>, "Q3": <number|null>, "Q4": <number|null>, "FY": <number|null>},
  "reason": "<one-paragraph explanation citing specific words from the source_quote>",
  "confidence": <0.0-1.0>,
  "flags": ["<any of: adjusted_not_reported | per_share_confusion | unit_scale_error | currency_error | continuing_vs_discontinued | qsum_mismatch | dual_class_confusion | source_quote_does_not_support>"]
}"""

    return f"""Metric-specific anti-confusion rules (from prompts/{extract.value_key}.md):

{metric_context}

Extracted values with source evidence:

{json.dumps(payload, indent=2, default=str)}
{prev_hint}

Challenge every number. Check:

1. Does the source_quote literally contain the number claimed?
2. If per-share is quoted (e.g. "2.70 EUR per share"), is the extractor's
   value = per_share × shares_outstanding? Compute it and verify.
3. If quote says "Adjusted", "Core", "Bereinigt", "Non-IFRS", "Pre" — the
   extracted value is NOT reported IFRS. Flag 'adjusted_not_reported'.
4. Unit scale: is the number in millions but extractor stored as millions?
   (Should be absolute currency: 440M = 440000000)
5. For {extract.value_key}: any metric-specific pitfalls from the rules above?
6. If Q1+Q2+Q3+Q4 vs FY differs by >5%: flag 'qsum_mismatch' and correct.

Return JSON matching exactly:

{schema}
"""


def _dec_or_none(x) -> Decimal | None:
    if x is None:
        return None
    try:
        return Decimal(str(x))
    except Exception:
        return None


def run_verifier(
    extract: ExtractResult,
    prev_year_fy_hint: Decimal | None = None,
    limiter: RateLimiter | None = None,
    model: str = "claude-sonnet-4-6",
) -> VerifierVerdict:
    """Stage 2: challenge extracted values against their own source quotes."""
    client = get_client()
    limiter = limiter or RateLimiter()

    prompt = _build_verifier_prompt(extract, prev_year_fy_hint)

    def _call() -> anthropic.types.Message:
        return client.messages.create(
            model=model,
            max_tokens=1024,
            system=VERIFIER_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )

    response = limiter.call(_call)
    text_parts = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            text_parts.append(text)
    raw_text = "\n".join(text_parts).strip()

    payload = _extract_json(raw_text)
    corr_raw = payload.get("corrections") or {}
    return VerifierVerdict(
        verdict=payload.get("verdict", "insufficient_evidence"),
        corrections={
            "Q1": _dec_or_none(corr_raw.get("Q1")),
            "Q2": _dec_or_none(corr_raw.get("Q2")),
            "Q3": _dec_or_none(corr_raw.get("Q3")),
            "Q4": _dec_or_none(corr_raw.get("Q4")),
            "FY": _dec_or_none(corr_raw.get("FY")),
        },
        reason=payload.get("reason", ""),
        confidence=float(payload.get("confidence", 0.0) or 0.0),
        flags=list(payload.get("flags", []) or []),
    )


# --- Orchestrator ---------------------------------------------------------


@dataclass
class TwoStageResult:
    extract: ExtractResult
    verdict: VerifierVerdict

    @property
    def final_values(self) -> dict[str, Decimal | None]:
        """Extracted values overridden by verifier corrections."""
        def _pick(period: str, extracted: QuarterValue | None) -> Decimal | None:
            corr = self.verdict.corrections.get(period)
            if corr is not None:
                return corr
            if extracted is None:
                return None
            return extracted.value

        if self.extract.quarter_only:
            q = self.extract.quarter_only
            qv = getattr(self.extract, q.lower(), None)
            return {q: _pick(q, qv)}

        return {
            "Q1": _pick("Q1", self.extract.q1),
            "Q2": _pick("Q2", self.extract.q2),
            "Q3": _pick("Q3", self.extract.q3),
            "Q4": _pick("Q4", self.extract.q4),
            "FY": _pick("FY", self.extract.fy),
        }


def research_two_stage(
    ticker: str,
    company_name: str,
    value_key: str,
    year: int,
    currency: str,
    mode: Mode,
    quarter: str | None = None,
    prev_year_fy_hint: Decimal | None = None,
    limiter: RateLimiter | None = None,
    extractor_model: str = "claude-sonnet-4-6",
    verifier_model: str = "claude-sonnet-4-6",
) -> TwoStageResult:
    """Full pipeline: extract with sources -> verify -> apply corrections."""
    extract = run_extractor(
        ticker=ticker,
        company_name=company_name,
        value_key=value_key,
        year=year,
        currency=currency,
        mode=mode,
        quarter=quarter,
        limiter=limiter,
        model=extractor_model,
    )
    verdict = run_verifier(
        extract=extract,
        prev_year_fy_hint=prev_year_fy_hint,
        limiter=limiter,
        model=verifier_model,
    )
    return TwoStageResult(extract=extract, verdict=verdict)


# --- CLI ------------------------------------------------------------------


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Two-stage extractor + verifier for a single value.")
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--company", required=True)
    ap.add_argument("--key", required=True, help="value_key, e.g. revenue")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--currency", default="EUR")
    ap.add_argument("--mode", choices=["historic", "current"], required=True)
    ap.add_argument("--quarter", default=None, help="Q1/Q2/Q3/Q4 (required for current mode)")
    ap.add_argument("--prev-fy", type=str, default=None, help="Prior-year FY value as decimal")
    ap.add_argument("--extractor-model", default="claude-sonnet-4-6")
    ap.add_argument("--verifier-model", default="claude-sonnet-4-6")
    args = ap.parse_args()

    prev = Decimal(args.prev_fy) if args.prev_fy else None
    result = research_two_stage(
        ticker=args.ticker,
        company_name=args.company,
        value_key=args.key,
        year=args.year,
        currency=args.currency,
        mode=args.mode,
        quarter=args.quarter,
        prev_year_fy_hint=prev,
        extractor_model=args.extractor_model,
        verifier_model=args.verifier_model,
    )

    out = {
        "extract": {
            "ticker": result.extract.ticker,
            "value_key": result.extract.value_key,
            "year": result.extract.year,
            "quarter_only": result.extract.quarter_only,
            "q1": asdict(result.extract.q1) if result.extract.q1 else None,
            "q2": asdict(result.extract.q2) if result.extract.q2 else None,
            "q3": asdict(result.extract.q3) if result.extract.q3 else None,
            "q4": asdict(result.extract.q4) if result.extract.q4 else None,
            "fy": asdict(result.extract.fy) if result.extract.fy else None,
            "extractor_note_adjusted_vs_reported": result.extract.is_adjusted_note,
        },
        "verdict": result.verdict.to_dict(),
        "final_values": {k: str(v) if v is not None else None for k, v in result.final_values.items()},
    }
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
