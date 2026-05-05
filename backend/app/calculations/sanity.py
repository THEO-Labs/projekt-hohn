"""LLM-based consistency check over a company's FY values.

After all values for an FY are populated (whether from PDF, EDGAR, Yahoo,
Claude-research or factor-estimate), we send the full picture to Claude with
a 'forensic accountant' prompt and ask it to flag obvious inconsistencies:

- Definition mismatches between years (e.g. lease_liabilities 82B in one FY
  vs 7B in the next — clearly not the same accounting treatment).
- Sign issues (cash outflow accidentally stored as negative).
- Outliers vs. industry sense check.
- Currency mixing across periods.
- Cumulative Hohn-Rendite > 50 % p.a. is suspicious for non-startups.

The output is appended to a dedicated `consistency` chat message on the FY
period so the user sees a flag in the cell tooltip and can drill in.
"""
import json
import logging
from decimal import Decimal
from uuid import UUID

import anthropic
from sqlalchemy.orm import Session

from app.config import settings
from app.values.models import CompanyValue
from app.companies.models import Company
from app.llm.rate_limiter import claude_limiter

logger = logging.getLogger(__name__)

# Module-level shared client — anthropic.Anthropic() is thread-safe and the
# underlying httpx connection pool benefits from being reused across calls.
_anthropic_client: anthropic.Anthropic | None = None


def _client() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _anthropic_client


# Sentinel key under which "global" sanity issues (those that span multiple
# value_keys, e.g. overall data quality flags) are filed. We pin it to the
# canonical Hohn-Rendite cell because that's the natural place for the user
# to see a portfolio-wide warning, not net_income.
_GLOBAL_SANITY_KEY = "hohn_return_simple"


SANITY_SYSTEM_PROMPT = """Du bist ein forensischer Accounting-Reviewer.
Du bekommst die Hohn-Rendite-Inputs und -Resultate eines Unternehmens für ein
oder mehrere Geschäftsjahre. Deine Aufgabe: Auffälligkeiten zu finden, die auf
Definitions-Mix, Vorzeichenfehler, Datenquellen-Inkonsistenzen oder unrealistische
Werte hindeuten.

Konzentriere dich auf:
1. Sprünge zwischen Jahren, die NICHT durch operatives Geschäft erklärbar sind
   (z.B. lease_liabilities 82 Mrd in 2025 vs 7 Mrd in 2026 → Definitionswechsel).
2. Negative Werte bei Posten die immer positiv sein sollten (dividends, buyback,
   sbc, cash, debt).
3. Hohn-Rendite > 30 % p.a. oder < -30 % p.a. — fast immer ein Datenproblem.
4. ni_growth > 100 % oder < -50 % zwischen zwei Jahren — Sondereffekt prüfen.
5. net_debt-Sprünge > 50 % YoY ohne erkennbaren M&A-Anlass.
6. Mismatch zwischen Bilanz-Posten (cash_sum vs debt_sum sollte plausibel skalieren).

Antworte AUSSCHLIESSLICH als JSON in diesem Format:
{
  "ok": true|false,
  "issues": [
    {
      "severity": "high"|"medium"|"low",
      "key": "<betroffener value_key oder 'global'>",
      "title": "<einzeiliger Titel>",
      "explanation": "<2-3 Sätze: was auffällig ist, woran es vermutlich liegt, was zu prüfen wäre>"
    }
  ],
  "summary": "<1 Satz Gesamtbild — z.B. 'Daten plausibel' oder 'Lease-Definitionsmix verzerrt Hohn-Rendite'>"
}

Wenn alles plausibel: {"ok": true, "issues": [], "summary": "..."}
Keine Markdown-Zäune, kein Text drumherum.
"""


def _gather_period_data(
    db: Session,
    company_id: UUID,
    period_year: int,
) -> dict:
    """Collect all FY values for the year + the previous year for context."""
    out = {"period_year": period_year, "current": {}, "previous": {}}
    for label, year in (("current", period_year), ("previous", period_year - 1)):
        rows = (
            db.query(CompanyValue)
            .filter(
                CompanyValue.company_id == company_id,
                CompanyValue.period_type == "FY",
                CompanyValue.period_year == year,
            )
            .all()
        )
        for r in rows:
            v = r.numeric_value
            out[label][r.value_key] = {
                "value": str(v) if isinstance(v, Decimal) else None,
                "source": (r.source_name or "")[:80],
                "currency": r.currency,
                "is_forecast": r.is_forecast,
                "manually_overridden": r.manually_overridden,
            }
    return out


def run_sanity_check(
    db: Session,
    company_id: UUID,
    period_year: int,
) -> dict | None:
    """Run forensic-accountant LLM check over FY[period_year]. Returns parsed
    JSON dict or None on failure. Also writes a system message to the FY's
    chat thread so the user sees flagged issues inline."""
    if not settings.anthropic_api_key:
        return None

    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if company is None:
        return None

    data = _gather_period_data(db, company_id, period_year)
    user_prompt = (
        f"Unternehmen: {company.name} ({company.ticker}, Currency: {company.currency})\n"
        f"FY-End-Stichtag: {company.fiscal_year_end_day:02d}.{company.fiscal_year_end_month:02d}.\n\n"
        f"Daten FY{period_year} und FY{period_year-1} (Vorjahr als Vergleich):\n\n"
        f"{json.dumps(data, indent=2, ensure_ascii=False)}\n\n"
        f"Prüfe auf Auffälligkeiten und antworte als JSON."
    )

    try:
        response = claude_limiter.call(lambda: _client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            system=SANITY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        ))
    except Exception as e:
        logger.warning("Sanity check API call failed for %s/FY%s: %s", company_id, period_year, e)
        return None

    text = "\n".join(
        getattr(b, "text", "") or "" for b in response.content if getattr(b, "type", None) == "text"
    ).strip()
    if text.startswith("```"):
        import re as _re
        text = _re.sub(r"^```(?:json)?\s*", "", text)
        text = _re.sub(r"\s*```\s*$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Sanity check: could not parse Claude JSON for %s/FY%s. Raw: %s",
                       company_id, period_year, text[:300])
        return None

    # Persist as system messages on the affected cell chats so user sees a flag.
    issues = parsed.get("issues", []) or []
    if issues:
        try:
            from app.llm.routes import _get_or_create_conversation
            from app.llm.models import LlmMessage
            from app.values.models import ValueDefinition
            valid_keys = {row[0] for row in db.query(ValueDefinition.key).all()}
            for issue in issues:
                raw_key = issue.get("key") or ""
                # Map "global" or any unknown key to the canonical Hohn-Rendite
                # cell instead of dumping into net_income or crashing.
                if raw_key in ("global", "", None) or raw_key not in valid_keys:
                    key = _GLOBAL_SANITY_KEY
                else:
                    key = raw_key
                try:
                    conv = _get_or_create_conversation(db, company_id, key, "FY", period_year)
                    severity = issue.get("severity", "low").upper()
                    db.add(LlmMessage(
                        conversation_id=conv.id,
                        role="system",
                        content=(
                            f"Sanity-Check [{severity}]: {issue.get('title', '?')}\n"
                            f"{issue.get('explanation', '')}"
                        ),
                        source="sanity_check",
                    ))
                except Exception as e:
                    logger.warning("Sanity-check msg write failed for %s/%s: %s", company_id, key, e)
            db.flush()
        except Exception as e:
            logger.warning("Sanity-check persistence failed for %s/FY%s: %s", company_id, period_year, e)

    return parsed
