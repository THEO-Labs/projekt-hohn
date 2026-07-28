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
import os
import re
import sys
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Literal
from uuid import UUID, uuid4

import anthropic
import httpx

# Reuse the existing client + rate limiter so we honor the same
# per-minute budget as the production extractor.
from app.config import settings
from app.llm.claude import get_client
from app.llm.rate_limiter import RateLimiter, claude_limiter


def _validate_source_url(url: str | None) -> bool:
    """Conservative existence check for the extractor-provided source_url.

    We only mark a URL as invalid on hard signals: DNS failure,
    connection refused, or an HTTP 404. Timeouts, 403 anti-bot walls,
    405 method-not-allowed, and other 4xx/5xx responses get the benefit
    of the doubt — many IR sites block HEAD from any non-browser UA.
    """
    if not url or not url.startswith(("http://", "https://")):
        return False
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; hohn-verify/1.0)",
    }
    try:
        r = httpx.head(url, timeout=4.0, follow_redirects=True, headers=headers)
        if r.status_code == 404:
            return False
        return True
    except httpx.ConnectError:
        return False  # DNS failure / connection refused
    except (httpx.TimeoutException, httpx.InvalidURL):
        return True  # timeout is not proof of fabrication
    except Exception:
        return True

# Cost estimates per 1M tokens (USD, checked 2026-07)
_COST_TABLE = {
    "claude-sonnet-4-6":  {"in": 3.00,  "out": 15.00},
    "claude-opus-4-7":    {"in": 15.00, "out": 75.00},
    "claude-haiku-4-5":   {"in": 1.00,  "out": 5.00},
    "claude-haiku-4-5-20251001": {"in": 1.00, "out": 5.00},
}
_WEB_SEARCH_COST_PER_CALL = 0.01  # $10 per 1000 web searches


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
    raw = p.read_text(encoding="utf-8")

    # YAML-Frontmatter strippen — reine Katalog-Metadaten.
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            raw = raw[end + 4:]

    # "Output-Format"- und "Query-Template"-Abschnitte sind fuer den
    # Standalone-Agent-Gebrauch. Im Two-Stage-Extractor konkurrieren ihre
    # JSON-Beispiele mit dem verbindlichen q1..q4/fy-Schema und provozieren
    # Format-Brueche — beim Einbetten weglassen.
    kept: list[str] = []
    skip = False
    for line in raw.splitlines():
        if line.startswith("## "):
            skip = "Output-Format" in line or "Query-Template" in line
        if not skip:
            kept.append(line)
    return "\n".join(kept).strip()


# --- Data structures ------------------------------------------------------

Mode = Literal["historic", "current"]


@dataclass
class QuarterValue:
    value: Decimal | None
    source_quote: str | None
    source_url: str | None
    is_estimate: bool = False
    # Adjusted / Non-GAAP variant of the same metric for the same period.
    # Only populated for companies that publish separate Adjusted / Core /
    # Bereinigt figures. Left as None for companies that don't.
    adjusted_value: Decimal | None = None
    adjusted_source_quote: str | None = None
    adjustments_note: str | None = None


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
    model_id: str | None = None  # extractor model used (provenance/reproducibility)

    def to_verifier_json(self) -> dict:
        def _q(qv: QuarterValue | None) -> dict | None:
            if qv is None:
                return None
            return {
                "value": str(qv.value) if qv.value is not None else None,
                "source_quote": qv.source_quote,
                "source_url": qv.source_url,
                "is_estimate": qv.is_estimate,
                "adjusted_value": str(qv.adjusted_value) if qv.adjusted_value is not None else None,
                "adjustments_note": qv.adjustments_note,
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
    "REPORTED periods: only what the document actually says, is_estimate=false; "
    "if a reported value cannot be backed by a source quote, set it to null. "
    "ESTIMATES are allowed ONLY where the task protocol explicitly asks for "
    "them (not-yet-reported periods of an in-progress fiscal year); they must "
    "be marked is_estimate=true and their source_quote must document the "
    "guidance/consensus/derivation basis. "
    "CONTAMINATION RULE: the metric guidance in the user message contains "
    "illustrative example numbers — they are NOT data. Every number you "
    "output must come from your own web_search results for THIS company and "
    "period, never from examples in the instructions."
)


def _build_historic_extractor_prompt(
    ticker: str, company_name: str, value_key: str, year: int, currency: str
) -> str:
    metric_context = load_metric_prompt(value_key)

    schema = """{
  "q1": {
    "value": <number in absolute """ + currency + """|null: IFRS REPORTED>,
    "source_quote": <string|null>,
    "source_url": <string|null>,
    "is_estimate": <bool>,
    "adjusted_value": <number|null: Adjusted/Core/Bereinigt variant if company publishes one>,
    "adjusted_source_quote": <string|null>,
    "adjustments_note": <string|null: what is stripped out, e.g. 'excludes Monsanto impairment, restructuring, PPA'>
  },
  "q2": {...same shape...},
  "q3": {...same shape...},
  "q4": {...same shape...},
  "fy": {...same shape...},
  "extractor_note_adjusted_vs_reported": <string|null: describe overall differences at company level>
}"""

    return f"""Task: Extract `{value_key}` for {company_name} ({ticker}), fiscal year {year}.
Currency: {currency}. Report all values in ABSOLUTE {currency} (no millions, no per-share).

Return Q1, Q2, Q3, Q4 AND full-year FY {year} — all in one JSON object.

Ground rules (metric-specific, from backend/scripts/prompts/{value_key}.md):

{metric_context}

For each period MUST provide:
- `value`: IFRS REPORTED figure (with all one-offs, impairments,
  discontinued ops, PPA etc.), absolute {currency}. This is the
  headline / consolidated / statutory number in the income statement.
- `source_quote`: exact sentence from the report / interim statement.
- `source_url`: URL of the source document.
- `adjusted_value`: The company's Adjusted / Core / Bereinigt / Non-IFRS
  / Pre variant of the SAME metric for the SAME period. Only populate
  this if the company actually publishes a separate adjusted figure
  (Bayer Core, RWE Bereinigt, SAP Non-IFRS, MRK EBITDA Pre, ENR
  Adjusted, EOAN Adjusted, DTE Adjusted AL, etc.). If the company does
  not publish a separate adjusted variant (Continental, Vonovia,
  most banks/insurers): leave adjusted_value null.
- `adjusted_source_quote`: exact sentence for the adjusted figure.
- `adjustments_note`: a short list of what is stripped out to get from
  reported to adjusted, e.g. "Monsanto legal reserves 3.5B,
  restructuring 1.2B, PPA amortization 2.0B, discontinued Aumovio 0.4B".

SOURCE PRIORITY — use the HIGHEST available tier and stick to ONE tier per
period; name the tier in source_quote:
  1. Company filings / interim reports / official press releases (IR pages).
  2. Company guidance statements (call transcripts, outlook sections).
  3. Analyst consensus from ONE named aggregator — prefer MarketScreener;
     only if unavailable use TipRanks. Never mix aggregators across periods
     of the same metric, and never replace an available higher tier with a
     lower one.

Availability rules — CRITICAL:
- Each period has an `is_estimate` boolean. Set carefully:
    * `is_estimate: false` = value from a published company report
      (interim, quarterly, annual, press release with hard numbers).
      source_quote MUST be a sentence from that publication.
    * `is_estimate: true` = value from company FY guidance, analyst
      consensus (MarketScreener, Refinitiv, Bloomberg, TipRanks), or
      Q1-YTD run-rate × prior-year seasonal factor.
      source_quote MUST cite the guidance / consensus / model basis
      verbatim.

- INTERIM REPORTS ARE PRIMARY SOURCES for closed quarters even if the
  annual report is not out yet. If Q1 report is published: Q1 value
  MUST be is_estimate=false from that Q1 report. If H1 / 6-month
  report is published: Q1 is from Q1 report AND Q2 = H1 - Q1 (still
  is_estimate=false because H1 is filed). If 9M / three-quarter
  report is published: Q3 = 9M - H1 (still is_estimate=false). Only
  the currently-in-progress quarter and any quarter that has not
  reported yet gets is_estimate=true.

  Concrete example (Bayer FY 2025 as of mid-2026):
    - Bayer Q1 2025 report was released May 2025 -> Q1 value from that
      report, is_estimate=false.
    - Bayer H1 2025 report was released August 2025 -> Q2 = H1 - Q1
      derived, is_estimate=false, source_quote cites the H1 report
      and shows the subtraction: "H1 2025 net income €X million minus
      Q1 2025 €Y million = Q2 €Z million".
    - Bayer 9M 2025 report was released November 2025 -> Q3 = 9M - H1
      derived, is_estimate=false.
    - Bayer Annual Report 2025 was released March 2026 -> Q4 = FY - 9M
      derived, is_estimate=false, OR just extract the segment tables
      from the AR directly.
  Do NOT flag a quarter as is_estimate=true when a company filing
  covers that quarter, even if the FY annual report is still pending.
  Extrapolation from prior-year seasonality is a LAST RESORT, only
  when NO filing covers the period yet.

- If the annual report for FY {year} is NOT YET PUBLISHED (in-progress fiscal year):
    * Already-reported quarter: extract the reported value, `is_estimate: false`.
    * NOT-yet-reported quarter: YOU MUST PROVIDE A NUMERIC ESTIMATE.
      Priority order for FLOW metrics (revenue, net_income, ebitda,
      operating_cash_flow, capex, sbc, buyback_volume, dividends):
        1. Analyst consensus for that specific quarter (search
           MarketScreener / TipRanks / Bloomberg summary pages).
        2. Company Q-guidance if issued.
        3. Extrapolation: prior-year same-quarter value × (Q1 YoY growth
           observed). Document the calculation in source_quote as
           "Q1 YTD growth X% × prior-year Qn value Y = estimated Z".
      Priority order for DERIVED metrics (`fcf`, `net_debt`):
        1. If component metrics for the same period are known:
           - fcf = operating_cash_flow − capex (per period).
           - net_debt = st_debt + lt_debt − cash_and_equivalents − st_investments.
           Compute it. source_quote = "Derived: OCF X − CapEx Y = Z" or
           equivalent. is_estimate matches whether the components are
           estimated.
        2. Otherwise fall back to consensus / extrapolation as above.
      Priority order for BALANCE-SHEET metrics (`cash_and_equivalents`,
      `st_investments`, `st_debt`, `lt_debt`):
        These do not change quarter-to-quarter as fast as flows. If no
        new balance-sheet disclosure for a quarter exists, CARRY FORWARD
        the last known balance-sheet value (prior Q4 from the annual
        report, or the most recent interim balance sheet). source_quote
        must say so explicitly, e.g. "Carried forward from FY{year-1}
        year-end balance sheet — no new disclosure in QN {year} interim".
        is_estimate = true.
      Never null out any period just because you didn't find a
      consensus quickly — every method above ALWAYS produces a value.
    * FY total: MUST BE POPULATED. Priority:
        1. Company FY guidance midpoint.
        2. Analyst consensus FY.
        3. Sum-of-parts: Q1 actual + estimated Q2 + Q3 + Q4.
        4. For balance-sheet metrics: FY = Q4 = latest known value.
      source_quote cites the guidance / consensus / sum-of-parts basis.

- If the annual report IS published: extract all Q1..Q4 + FY from it,
  `is_estimate: false` throughout.

- ABSOLUTE RULE: every non-null value MUST have a non-empty source_quote
  that unambiguously points to where the number comes from. If you
  cannot find or construct a source_quote, set value to null. Never
  guess a number without documented basis.

Consistency: when Q1..Q4 are ALL non-null, Q1 + Q2 + Q3 + Q4 should equal FY within 0.5% for flow metrics.

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
    """Robust JSON extraction from LLM text output.

    Handles: markdown fences, prose prefix/suffix, trailing commas,
    truncated tails, mixed single/double quotes.
    """
    if not text:
        raise ValueError("empty response")

    # Strip markdown fences (```json ... ``` or ``` ... ```)
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find first well-balanced {...} block by counting braces
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in extractor response")
    depth = 0
    in_str = False
    esc = False
    end = -1
    for i, ch in enumerate(text[start:], start=start):
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        # Response likely truncated mid-JSON. Try to close open braces/brackets.
        candidate = text[start:]
        # Trim any trailing incomplete key/value line
        candidate = re.sub(r",\s*[^,{}\[\]:]*$", "", candidate)
        # Count and close open braces + brackets
        open_braces = candidate.count("{") - candidate.count("}")
        open_brackets = candidate.count("[") - candidate.count("]")
        candidate = candidate.rstrip(", \n\t") + ("]" * open_brackets) + ("}" * open_braces)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            raise ValueError(f"truncated JSON, salvage failed: {e}")
    return json.loads(text[start:end + 1])


def _to_qv(raw: dict | None) -> QuarterValue | None:
    if not raw:
        return None

    def _dec(x):
        if x is None:
            return None
        try:
            return Decimal(str(x))
        except Exception:
            return None

    return QuarterValue(
        value=_dec(raw.get("value")),
        source_quote=raw.get("source_quote"),
        source_url=raw.get("source_url"),
        is_estimate=bool(raw.get("is_estimate", False)),
        adjusted_value=_dec(raw.get("adjusted_value")),
        adjusted_source_quote=raw.get("adjusted_source_quote"),
        adjustments_note=raw.get("adjustments_note"),
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
    limiter = limiter or claude_limiter

    if mode == "historic":
        prompt = _build_historic_extractor_prompt(ticker, company_name, value_key, year, currency)
    else:
        if not quarter:
            raise ValueError("current-year mode requires a quarter (Q1..Q4)")
        prompt = _build_current_quarter_extractor_prompt(
            ticker, company_name, value_key, year, quarter, currency
        )

    def _call(extra_instruction: str = "") -> anthropic.types.Message:
        user_content = prompt + (f"\n\n{extra_instruction}" if extra_instruction else "")
        return client.messages.create(
            model=model,
            max_tokens=8192,
            temperature=0,
            system=EXTRACTOR_SYSTEM,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
            messages=[{"role": "user", "content": user_content}],
        )

    # First attempt
    response = limiter.call(lambda: _call(""))
    text_parts = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            text_parts.append(text)
    raw_text = "\n".join(text_parts).strip()

    # Try to parse; if it fails, retry once with an explicit "valid JSON only"
    # instruction. This catches the case where the first call ended with prose
    # after the JSON object, or emitted a truncated payload.
    try:
        payload = _extract_json(raw_text)
    except ValueError:
        retry_instr = (
            "IMPORTANT: your previous response could not be parsed as JSON. "
            "Return ONLY the JSON object matching the schema — no prose, no "
            "markdown fences, no commentary before or after. Every field must "
            "be complete; do not truncate."
        )
        response = limiter.call(lambda: _call(retry_instr))
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
            model_id=model,
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
            model_id=model,
        )


# --- Stage 2: Verifier ----------------------------------------------------


# Web-Search gibt dem Verifier UNABHAENGIGE Evidenz. Ohne sie prueft er nur
# das Zitat, das der Extractor selbst behauptet — ein plausibel halluziniertes
# Zitat wuerde bestaetigt (Zirkelschluss).
VERIFIER_WEB_SEARCH_MAX_USES = 3
VERIFIER_MAX_TOKENS = 2048

VERIFIER_SYSTEM = (
    "You are a financial-data second-opinion with your own web_search "
    "tool. You receive a set of extracted values plus the exact "
    "source_quote and source_url each came from. The quotes were "
    "produced by another model and may be fabricated or misattributed — "
    "do NOT treat them as ground truth. INDEPENDENT CHECK: use "
    "web_search to verify the most material number (FY, or the single "
    "quarter in single-quarter mode) against a primary source (company "
    "IR page, filed report, reputable financial data site). Budget is "
    "limited — one or two targeted searches, not one per period. "
    "For EACH value decide one of three verdicts:\n\n"
    "  * 'confirm' — DEFAULT. Use this when the source_quote plainly "
    "supports the number and no obvious error is present. This is what "
    "most values will get. Do NOT invent doubts to justify a lower "
    "verdict — 'confirm' with an empty reason is the correct behaviour "
    "for the majority of rows.\n"
    "  * 'correct' — Use ONLY when there is a concrete detectable error "
    "proven by the source_quote itself OR by your own web_search "
    "evidence (then cite that source in reason). Examples: "
    "per-share vs total confusion (quote says '2.70 EUR per share' but "
    "extractor stored 2,700,000,000); adjusted vs IFRS reported "
    "confusion (quote says 'Adjusted' but extractor stored it without "
    "flag); unit scale error; currency error; continuing vs discontinued "
    "mixup; dual-class share confusion; Q-sum vs FY mismatch >5%. "
    "Reason MUST cite the exact words in source_quote that prove the "
    "error and give the corrected value.\n"
    "  * 'insufficient_evidence' — Use ONLY when the source_quote is "
    "empty, missing, or clearly does not contain the number claimed. "
    "Do NOT use this because you feel uncertain or because the source "
    "is a consensus/guidance rather than a filed report — the extractor "
    "already marked is_estimate=true in those cases and estimates are "
    "legitimate. Reason MUST state what specifically is missing.\n\n"
    "Return ONLY the JSON verdict per schema, no prose outside JSON. "
    "REASON RULES:\n"
    "  - confirm: reason is an empty string.\n"
    "  - correct: reason cites the exact words in source_quote that "
    "triggered the fix.\n"
    "  - insufficient_evidence: reason states what is missing.\n\n"
    "LANGUAGE RULE: the reason field MUST be written entirely in English, "
    "as a single fluent paragraph. Do NOT mix German and English. Do NOT "
    "paste German quote fragments untranslated — translate them to "
    "English inline (e.g. write \"the source says '2.70 EUR per share'\" "
    "even if the German quote said '2,70 EUR je Aktie'). The reader is "
    "an English-speaking analyst.\n\n"
    "ESTIMATE CORRECTION RULE: for values marked is_estimate=true, "
    "'correct' is allowed ONLY for provable internal errors — arithmetic, "
    "unit scale, per-share confusion, temporal mislabeling. A third-party "
    "source showing a DIFFERENT forecast is never grounds for correction: "
    "forecasts legitimately differ.\n\n"
    "Bias toward 'confirm'. Only downgrade when you have a specific, "
    "citable reason — from the source_quote itself or from your own "
    "web_search evidence. If your independent search contradicts the "
    "extracted number materially (>5%), verdict='correct' with your "
    "found value and source cited in reason."
)


def _build_verifier_prompt(
    extract: ExtractResult,
    prev_year_fy_hint: Decimal | None = None,
) -> str:
    metric_context = load_metric_prompt(extract.value_key)
    payload = extract.to_verifier_json()

    temporal_rule = ""
    if extract.year >= date.today().year:
        temporal_rule = (
            f"\n\nIN-PROGRESS FISCAL YEAR RULE (critical): FY {extract.year} is "
            "not over. The FY value and unreported quarters are SUPPOSED to be "
            "forward estimates (guidance / analyst consensus) marked "
            "is_estimate=true — do NOT correct an estimate merely for being an "
            "estimate. A 'reported full-year' figure for an in-progress year "
            "cannot exist; any web source showing one is mislabeled (prior "
            "year, trailing-twelve-months, or the site's own forecast) and "
            "MUST NOT be used as a correction. Also sanity-check corrections "
            "against reported actuals and the prior-year FY: a correction that "
            "implies a massive residual swing in the remaining quarters "
            "(e.g. a deeply negative final quarter after growing reported "
            "quarters) is almost certainly wrong."
        )

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
{temporal_rule}{prev_hint}

Challenge every number. Check:

1. Does the source_quote literally contain the number claimed?
2. If per-share is quoted (e.g. "2.70 EUR per share"), is the extractor's
   value = per_share × shares_outstanding? Compute it and verify.
3. If quote says "Adjusted", "Core", "Bereinigt", "Non-IFRS", "Pre" — the
   extracted value is NOT reported IFRS. Flag 'adjusted_not_reported'.
4. Unit scale: is the number in millions but extractor stored as millions?
   (Should be absolute currency: 440M = 440000000)
5. For {extract.value_key}: any metric-specific pitfalls from the rules above?
6. Q-SUM CONSISTENCY CHECK (mandatory when Q1..Q4 and FY are all present).
   Compute S = Q1 + Q2 + Q3 + Q4. Compute delta_pct = 100 * (S - FY) /
   max(abs(FY), 1). If abs(delta_pct) > 5%:
     - flag 'qsum_mismatch'
     - verdict='correct'
     - Identify the likely culprit by scanning source_quotes: any
       quarter whose source_quote references "H1", "half-year", "6
       months", "9M", "nine months", "YTD", or "cumulative" instead
       of the standalone quarter is a strong suspect — the extractor
       probably stored a cumulative-window value in a standalone-Q
       slot.
     - In corrections, fix that quarter's value to the correctly
       derived standalone number (e.g. Q2 = H1 - Q1) and update Q4
       so Q1..Q4 sum matches FY within 0.5%.
     - reason MUST show the arithmetic and cite the mis-attributed
       source_quote fragment, e.g. "Q1+Q2+Q3+Q4 = 1.9B but FY = 1.5B,
       delta = +27%. Q4 source_quote says 'first half of 2025' which
       is H1, not Q4 — Q4 should be FY - 9M-YTD = 2.8B, adjusted."

7. INDEPENDENT VERIFICATION (mandatory, budget-aware): run 1-2 targeted
   web_search queries to check the most material number — FY {extract.year}
   {extract.value_key} for {extract.ticker} (or the single quarter in
   single-quarter mode) — against a primary source. The source_quotes
   above come from another model and may be fabricated; your search is
   the only independent evidence. If your finding matches within 5%:
   supports 'confirm'. If it contradicts materially: verdict='correct',
   put your found value in corrections and cite the source in reason.
   If you cannot find anything either way, do NOT downgrade to
   insufficient_evidence just because of that — judge on the quotes.

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
    limiter = limiter or claude_limiter

    prompt = _build_verifier_prompt(extract, prev_year_fy_hint)

    def _call() -> anthropic.types.Message:
        return client.messages.create(
            model=model,
            max_tokens=VERIFIER_MAX_TOKENS,
            temperature=0,
            system=VERIFIER_SYSTEM,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": VERIFIER_WEB_SEARCH_MAX_USES,
            }],
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
        """Extracted values overridden by verifier corrections.

        Korrekturen greifen NUR bei verdict='correct' — bei 'confirm' oder
        'insufficient_evidence' darf ein versehentlich befuelltes
        corrections-Feld den Extractor-Wert nicht ueberschreiben.
        """
        def _pick(period: str, extracted: QuarterValue | None) -> Decimal | None:
            corr = (
                self.verdict.corrections.get(period)
                if self.verdict.verdict == "correct"
                else None
            )
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


@dataclass
class CostTracker:
    """Tracks cumulative API cost across a batch run + enforces budget cap."""
    max_usd: float | None = None
    spent_usd: float = 0.0
    calls: int = 0

    def add_response(self, response: anthropic.types.Message, model: str, web_search_calls: int = 0) -> float:
        usage = getattr(response, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        rates = _COST_TABLE.get(model.replace("[1m]", ""), {"in": 3.0, "out": 15.0})
        cost = (in_tok * rates["in"] + out_tok * rates["out"]) / 1_000_000
        cost += web_search_calls * _WEB_SEARCH_COST_PER_CALL
        self.spent_usd += cost
        self.calls += 1
        return cost

    def check_budget(self) -> None:
        if self.max_usd is not None and self.spent_usd >= self.max_usd:
            raise RuntimeError(
                f"Budget cap reached: spent ${self.spent_usd:.2f} >= max ${self.max_usd:.2f} "
                f"after {self.calls} calls"
            )


def choose_mode_for_year(year: int, today: date | None = None, quarter: str | None = None) -> Mode:
    """Auto-select HISTORIC vs CURRENT based on target year and requested quarter.

    CURRENT is the single-standalone-quarter extraction and is only valid when
    a specific quarter is requested for the running/future year. FY-level runs
    (quarter=None) always use HISTORIC: its prompt carries the
    in-progress-fiscal-year protocol (reported quarters as actuals, missing
    quarters as estimates, FY via guidance/consensus).
    """
    if quarter is None:
        return "historic"
    t = today or date.today()
    return "historic" if year < t.year else "current"


# FY-Werte mit dem groessten Hebel auf die H-Return: hier eliminiert
# Median-of-3-Sampling die Ausreisser einzelner Web-Search-Laeufe.
CRITICAL_MEDIAN_KEYS = frozenset({"net_income", "revenue", "fcf"})


def _pick_median_extract(candidates: list[ExtractResult]) -> ExtractResult:
    """Waehlt die komplette Extraktion mit dem Median-FY-Wert (keine
    Perioden-Mischung ueber Samples hinweg)."""
    with_fy = [c for c in candidates if c.fy is not None and c.fy.value is not None]
    if not with_fy:
        return candidates[0]
    with_fy.sort(key=lambda c: c.fy.value)
    return with_fy[len(with_fy) // 2]


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
    verifier_model: str = "claude-haiku-4-5-20251001",
    cost_tracker: CostTracker | None = None,
) -> TwoStageResult:
    """Full pipeline: extract with sources -> verify -> apply corrections."""
    if cost_tracker:
        cost_tracker.check_budget()
    n_samples = 3 if (value_key in CRITICAL_MEDIAN_KEYS and mode == "historic") else 1
    n_samples = int(os.environ.get("TWO_STAGE_MEDIAN_SAMPLES", n_samples))
    candidates: list[ExtractResult] = []
    for _ in range(max(1, n_samples)):
        candidates.append(run_extractor(
            ticker=ticker,
            company_name=company_name,
            value_key=value_key,
            year=year,
            currency=currency,
            mode=mode,
            quarter=quarter,
            limiter=limiter,
            model=extractor_model,
        ))
    extract = _pick_median_extract(candidates)
    verdict = run_verifier(
        extract=extract,
        prev_year_fy_hint=prev_year_fy_hint,
        limiter=limiter,
        model=verifier_model,
    )
    return TwoStageResult(extract=extract, verdict=verdict)


# --- DB Persistence -------------------------------------------------------


def _period_key_for_result(result: TwoStageResult) -> list[tuple[str, Decimal | None]]:
    """Flatten TwoStageResult to (period_type, final_value) tuples for DB write."""
    out: list[tuple[str, Decimal | None]] = []
    for period, value in result.final_values.items():
        if value is None:
            continue
        out.append((period, value))
    return out


_STOCK_METRICS_NO_QSUM = {
    "cash_and_equivalents", "st_investments", "st_debt", "lt_debt", "net_debt",
    "market_cap", "shares_outstanding", "stock_price",
}


def _derive_missing_quarter(result: "TwoStageResult") -> None:
    """If FY + exactly three quarters are present as final values but the
    fourth is missing, derive the fourth as FY - sum(others).

    Catches the RWE Q4 2025 net_income class of bug where the extractor
    silently returned null for one quarter, apply_to_db then skipped that
    row, and the old (often stale) DB value survived. Only applied to
    flow metrics — for balance-sheet stock metrics the arithmetic does
    not make sense (Q4 = FY is the correct rule, not FY - Q1 - Q2 - Q3).
    """
    if result.extract.value_key in _STOCK_METRICS_NO_QSUM:
        return
    finals = result.final_values
    q_keys = ["Q1", "Q2", "Q3", "Q4"]
    fy = finals.get("FY")
    if fy is None:
        return
    present = [k for k in q_keys if finals.get(k) is not None]
    missing = [k for k in q_keys if finals.get(k) is None]
    if len(present) != 3 or len(missing) != 1:
        return
    m = missing[0]
    derived = fy - sum(finals[k] for k in present)
    # Inject a synthetic QuarterValue that apply_to_db can persist.
    other_qs = "+".join(present)
    quote = f"Derived: FY {fy} minus ({other_qs}) = {derived}"
    qv = QuarterValue(
        value=derived,
        source_quote=quote,
        source_url=None,
        is_estimate=False,
    )
    # Attach to the extract as the missing-quarter slot.
    setattr(result.extract, m.lower(), qv)


_QSUM_TOLERANCE = Decimal("0.005")  # 0.5% — gleiche Toleranz wie der Extractor-Prompt


def _enforce_qsum_consistency(result: "TwoStageResult") -> None:
    """Erzwingt Q1+Q2+Q3+Q4 = FY fuer Flow-Metriken IM CODE.

    Prompts und Verifier fordern die Konsistenz nur — der Adidas-EBITDA-Fall
    zeigte, dass das Modell die Ableitung 'Q4 = FY - Summe' beschreiben, aber
    falsch rechnen kann und der Verifier es dennoch bestaetigt. Hier wird das
    LETZTE geschaetzte Quartal deterministisch als Residuum neu berechnet.
    Sind alle Quartale reported (Actuals), wird nichts umgeschrieben — dann
    ist die Abweichung ein Extraktionsfehler, kein Schaetz-Residuum.
    """
    if result.extract.value_key in _STOCK_METRICS_NO_QSUM:
        return
    if result.extract.quarter_only:
        return
    finals = result.final_values
    fy = finals.get("FY")
    q_keys = ["Q1", "Q2", "Q3", "Q4"]
    if fy is None or fy == 0 or any(finals.get(k) is None for k in q_keys):
        return
    total = sum(finals[k] for k in q_keys)
    if abs(total - fy) <= abs(fy) * _QSUM_TOLERANCE:
        return
    target = None
    for k in ("Q4", "Q3", "Q2", "Q1"):
        qv = getattr(result.extract, k.lower(), None)
        if qv is not None and qv.is_estimate:
            target = k
            break
    if target is None:
        return
    others = sum(finals[k] for k in q_keys if k != target)
    derived = fy - others
    old = finals[target]
    quote = (
        f"Derived for Q-sum consistency: FY {fy} - sum(other quarters) {others} "
        f"= {derived}; model estimate {old} discarded (sum deviated "
        f"{(total - fy) / fy:.1%} from FY)"
    )
    setattr(result.extract, target.lower(), QuarterValue(
        value=derived, source_quote=quote, source_url=None, is_estimate=True,
    ))
    # Eine Verifier-Korrektur auf dem Ziel-Quartal wuerde final_values sonst
    # weiterhin dominieren.
    if result.verdict.verdict == "correct":
        result.verdict.corrections[target] = derived


def apply_to_db(
    db,
    company_id: UUID,
    value_key: str,
    year: int,
    result: TwoStageResult,
    currency: str | None = None,
) -> list:
    """Persist Verifier-corrected values to company_values.

    Writes each non-null quarter + FY as a separate row. Sets:
      primary_method = 'two_stage_verified' | 'two_stage_confirmed' | 'two_stage_insufficient'
      source_name = ticker + verdict.reason + source_url from extractor
      manually_overridden = True (so future refreshes don't overwrite blindly)

    Uses INSERT-or-UPDATE via a (company_id, value_key, period_type, period_year) key.
    """
    from sqlalchemy import select
    from app.values.models import CompanyValue
    from app.values.persistence import normalize_sign

    # Fill in a missing quarter if FY + 3 quarters are present.
    _derive_missing_quarter(result)
    # Enforce Q1..Q4 = FY for flow metrics (rewrites the last estimated
    # quarter as the residual when the model's arithmetic drifted).
    _enforce_qsum_consistency(result)

    verdict_tag = {
        "confirm": "two_stage_confirmed",
        "correct": "two_stage_verified",
        "insufficient_evidence": "two_stage_insufficient",
    }.get(result.verdict.verdict, "two_stage_verified")

    periods = _period_key_for_result(result)
    if not periods:
        # Extractor lieferte alle Perioden null (sbc-Fall): NICHT lautlos
        # nichts tun — bestehende Zeilen als versucht stempeln und fuer
        # fehlende Perioden not_found-Platzhalter anlegen, damit das UI die
        # Zelle rot markieren kann (= manuell raussuchen).
        now = datetime.now(timezone.utc)
        stale_rows = db.execute(select(CompanyValue).where(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == value_key,
            CompanyValue.period_year == year,
        )).scalars().all()
        for row in stale_rows:
            row.last_refresh_attempt = now
        present = {row.period_type for row in stale_rows}
        for pt in ("Q1", "Q2", "Q3", "Q4", "FY"):
            if pt in present:
                continue
            db.add(CompanyValue(
                id=uuid4(), company_id=company_id, value_key=value_key,
                period_type=pt, period_year=year, numeric_value=None,
                source_name="No source found (research attempted)",
                primary_method="not_found", currency=currency,
                fetched_at=now, last_refresh_attempt=now,
                manually_overridden=False, from_ir_pdf=False,
            ))
        db.flush()
        return []

    # Ein FY-Wert eines noch nicht beendeten Geschaeftsjahres kann kein
    # Actual sein — Extractor-Fehldeklarationen (is_estimate=false) hier
    # hart korrigieren. FY-Ende aus den Company-Stammdaten, Fallback 31.12.
    fy_end_future = False
    try:
        from datetime import date as _date
        from app.companies.models import Company
        comp = db.get(Company, company_id)
        m = getattr(comp, "fiscal_year_end_month", None) or 12
        d = getattr(comp, "fiscal_year_end_day", None) or 31
        fy_end_future = _date(year, m, d) >= _date.today()
    except Exception:
        fy_end_future = year >= datetime.now(timezone.utc).year

    written = []
    for period_type, value in periods:
        # Source URL and quote from the corresponding extracted quarter (if any)
        src_url = None
        src_quote = None
        qv = None
        if period_type == "FY":
            qv = result.extract.fy
        elif period_type in ("Q1", "Q2", "Q3", "Q4"):
            qv = getattr(result.extract, period_type.lower(), None)
        if qv:
            src_url = qv.source_url
            src_quote = (qv.source_quote or "").strip()[:400]
        # Drop fabricated URLs — HEAD-check confirms existence.
        if src_url and not _validate_source_url(src_url):
            src_url = None

        # HARD SKIP: never write a value that has no source_quote and no
        # verifier correction reason backing it. That produces the
        # "No source captured" placeholder cells the user rightly hates.
        # If the extractor did not have a quote and the verifier did not
        # correct with an explicit reason, keep the previous DB value.
        has_source_evidence = bool(src_quote)
        has_verifier_correction = (
            verdict_tag == "two_stage_verified"
            and result.verdict.reason.strip() != ""
        )
        if not has_source_evidence and not has_verifier_correction:
            # Alter Wert bleibt stehen — aber den Refresh-Versuch stempeln,
            # sonst sieht die stille Stale-Zeile ewig frisch aus (adidas-Lauf:
            # sbc/ocf/st_investments blieben unmarkiert auf Vor-Deploy-Stand).
            stale_stmt = select(CompanyValue).where(
                CompanyValue.company_id == company_id,
                CompanyValue.value_key == value_key,
                CompanyValue.period_type == period_type,
                CompanyValue.period_year == year,
            )
            stale_row = db.execute(stale_stmt).scalar_one_or_none()
            if stale_row is not None:
                stale_row.last_refresh_attempt = datetime.now(timezone.utc)
            continue

        value = normalize_sign(
            value_key, value, context=f"two-stage {result.extract.ticker}/{period_type} {year}",
        )

        stmt = select(CompanyValue).where(
            CompanyValue.company_id == company_id,
            CompanyValue.value_key == value_key,
            CompanyValue.period_type == period_type,
            CompanyValue.period_year == year,
        )
        existing = db.execute(stmt).scalar_one_or_none()

        # source_name is what the UI cell popover shows. We surface:
        #   1) origin: is this a Reported value from a filed report, or an
        #      Estimate (consensus / guidance / seasonality-extrapolation)?
        #   2) the extractor's raw source_quote — the actual sentence from
        #      the report or guidance announcement. This is what the user
        #      cares about ("where does the number come from?").
        #   3) if the verifier CORRECTED the extractor, add its reason as
        #      a secondary note. If it only confirmed, skip the verifier
        #      chatter — the user doesn't care about "reconciles correctly".
        origin = "Estimate" if (qv and qv.is_estimate) else "Reported"
        parts = [f"Two-Stage {verdict_tag}", f"origin={origin}"]
        if src_quote:
            parts.append(f"quote={src_quote}")
        if verdict_tag == "two_stage_verified" and result.verdict.reason:
            parts.append(f"verifier_correction={result.verdict.reason[:800]}")
        elif verdict_tag == "two_stage_insufficient" and result.verdict.reason:
            parts.append(f"verifier_note={result.verdict.reason[:800]}")
        if result.verdict.flags:
            parts.append(f"flags={','.join(result.verdict.flags)}")
        if result.extract.model_id:
            parts.append(f"model={result.extract.model_id}")
        source_name = " | ".join(parts)

        is_estimate = bool(qv.is_estimate) if qv else False
        if period_type == "FY" and fy_end_future:
            is_estimate = True
        adjusted_value = qv.adjusted_value if qv else None
        adjustments_note = qv.adjustments_note if qv else None
        adjustments_source = None
        if qv and (qv.adjusted_source_quote or qv.source_url):
            parts = []
            if qv.adjusted_source_quote:
                parts.append(qv.adjusted_source_quote[:400])
            if qv.source_url:
                parts.append(qv.source_url)
            adjustments_source = " | ".join(parts) or None

        # FIX: manually_overridden=False so future Refresh full re-runs
        # can actually update the value. The two-stage_* primary_method
        # tag is enough to distinguish these rows from provider or manual
        # writes in the UI.
        if existing:
            existing.numeric_value = value
            existing.numeric_value_adjusted = adjusted_value
            existing.adjustments_note = adjustments_note
            existing.adjustments_source = adjustments_source
            existing.source_name = source_name
            existing.source_link = src_url
            existing.primary_method = verdict_tag
            existing.manually_overridden = False
            existing.is_forecast = is_estimate
            existing.fetched_at = datetime.now(timezone.utc)
            # Das Currency-Label muss den GESCHRIEBENEN Wert beschreiben — der
            # Extractor liefert explizit in Firmenwaehrung. Ein altes Label
            # (z.B. USD aus einem frueheren Provider-Write) darf nicht am
            # neuen Wert kleben bleiben.
            if currency:
                existing.currency = currency
            written.append(existing)
        else:
            cv = CompanyValue(
                id=uuid4(),
                company_id=company_id,
                value_key=value_key,
                period_type=period_type,
                period_year=year,
                is_forecast=is_estimate,
                numeric_value=value,
                numeric_value_adjusted=adjusted_value,
                adjustments_note=adjustments_note,
                adjustments_source=adjustments_source,
                currency=currency,
                source_name=source_name,
                source_link=src_url,
                primary_method=verdict_tag,
                manually_overridden=False,
                from_ir_pdf=False,
                fetched_at=datetime.now(timezone.utc),
            )
            db.add(cv)
            written.append(cv)

    db.flush()
    return written


# --- CLI ------------------------------------------------------------------


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Two-stage extractor + verifier for a single value.")
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--company", required=True)
    ap.add_argument("--key", required=True, help="value_key, e.g. revenue")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--currency", default="EUR")
    ap.add_argument("--mode", choices=["historic", "current", "auto"], default="auto",
                    help="auto = decide based on today's date")
    ap.add_argument("--quarter", default=None, help="Q1/Q2/Q3/Q4 (required for current mode)")
    ap.add_argument("--prev-fy", type=str, default=None, help="Prior-year FY value as decimal")
    ap.add_argument("--extractor-model", default="claude-sonnet-4-6")
    ap.add_argument("--verifier-model", default=os.environ.get("VERIFIER_MODEL", "claude-haiku-4-5-20251001"))
    ap.add_argument("--apply-to-db", action="store_true", help="Write verifier-final values to prod DB")
    ap.add_argument("--company-id", default=None, help="UUID of the company (required for --apply-to-db)")
    ap.add_argument("--max-cost-usd", type=float, default=None, help="Budget cap for this run")
    args = ap.parse_args()

    if args.mode == "auto":
        args.mode = choose_mode_for_year(args.year, quarter=args.quarter)

    prev = Decimal(args.prev_fy) if args.prev_fy else None
    tracker = CostTracker(max_usd=args.max_cost_usd) if args.max_cost_usd else None
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
        cost_tracker=tracker,
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
        "mode_used": args.mode,
    }
    print(json.dumps(out, indent=2, default=str))

    if args.apply_to_db:
        if not args.company_id:
            print("ERROR: --apply-to-db requires --company-id", file=sys.stderr)
            return 2
        from app.db import SessionLocal
        db = SessionLocal()
        try:
            written = apply_to_db(
                db, UUID(args.company_id), args.key, args.year, result, currency=args.currency,
            )
            db.commit()
            print(f"\nApplied to DB: {len(written)} rows.", file=sys.stderr)
        finally:
            db.close()

    return 0


if __name__ == "__main__":
    sys.exit(_cli())
