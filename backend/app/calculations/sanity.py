"""LLM-based per-value sanity check for a company's FY values.

Nach jedem FY-Refresh (egal ob Estimate oder abgeschlossen) bewertet Claude
JEDEN Primaerwert einzeln (Net Income, FCF, SBC, Buyback Volume, Dividends,
Net Debt, Shares Outstanding) und stellt eine Einschaetzung als System-Message
in den Chat der jeweiligen Zelle.

Status pro Wert:
- ok      → Wert plausibel, keine Auffaelligkeit
- low     → leichte Auffaelligkeit, Hinweis
- medium  → mittlere Konzern, Vorsicht
- high    → starke Auffaelligkeit, vermutlich Datenfehler

Damit hat JEDE primaere Zelle eine Sanity-Begruendung im Chat — nicht nur
die mit Issues. User sieht konsistent ob wir einen Wert fuer plausibel
halten.
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

_anthropic_client: anthropic.Anthropic | None = None


def _client() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _anthropic_client


# Werte die wir individuell sanity-checken — die 7 Primaerwerte aus der
# PDF-Extraktion (siehe app/ir_documents/extraction.py:EXTRACTION_KEYS) plus
# die zwei Hohn-Rendite-Aggregate als zusaetzlicher Cross-Check.
_PRIMARY_KEYS = (
    "net_income",
    "fcf",
    "sbc",
    "buyback_volume",
    "dividends",
    "net_debt",
    "shares_outstanding",
)
_AGGREGATE_KEYS = (
    "hohn_return_simple",
    "hohn_return_detailed",
)
_ALL_CHECKED_KEYS = _PRIMARY_KEYS + _AGGREGATE_KEYS


SANITY_SYSTEM_PROMPT = """Du bist ein forensischer Accounting-Reviewer.
Du bekommst die Hohn-Rendite-Inputs und -Resultate eines Unternehmens fuer
das aktuelle und das Vorjahres-Geschaeftsjahr.

Aufgabe: Bewerte JEDEN der folgenden Werte einzeln auf Plausibilitaet:
- net_income, fcf, sbc, buyback_volume, dividends, net_debt, shares_outstanding
- hohn_return_simple, hohn_return_detailed

Pro Wert vergibst du einen severity-Tag und eine kurze Begruendung:

- "ok"      → Wert plausibel, in normaler Range, kein Hinweis noetig
- "low"     → leichter Hinweis (z.B. ungewoehnlicher YoY-Trend, aber noch im Rahmen)
- "medium"  → klare Auffaelligkeit (z.B. Q-Faktor verzerrt durch Saisonalitaet,
              Definition-Mix, hohe Volatilitaet — User sollte verifizieren)
- "high"    → vermutlich Datenfehler (Vorzeichen, Definitionswechsel,
              unrealistische Magnitude — vermutlich nicht nutzbar)

Beruecksichtige bei is_forecast=true Werten:
- Q-Faktor-Estimates haben Saisonalitaets-Risiko (Q1 oft nicht repraesentativ)
- FY-Fallback sind no-growth-Annahmen — kennzeichne als 'low' wenn FY-Wachstum
  in der Branche typisch ist.

Pruefe insbesondere:
- Vorzeichen (Cash-Outflows wie SBC/Buyback/Dividenden sollten POSITIV sein)
- YoY-Spruenge > 100% oder < -50% ohne erkennbaren Grund
- Hohn-Rendite > 30% oder < -30% (fast immer Datenproblem)
- ni_growth bei Turnaround (Vorjahr negativ → grosse Wachstumsraten sind Artefakt)
- net_debt-Spruenge > 50% YoY ohne M&A-Hinweis

Antworte AUSSCHLIESSLICH als JSON in diesem Format:
{
  "checks": {
    "net_income": {
      "severity": "ok|low|medium|high",
      "title": "<einzeiliger Titel>",
      "explanation": "<2-3 Saetze>"
    },
    "fcf": { ... },
    "sbc": { ... },
    "buyback_volume": { ... },
    "dividends": { ... },
    "net_debt": { ... },
    "shares_outstanding": { ... },
    "hohn_return_simple": { ... },
    "hohn_return_detailed": { ... }
  },
  "summary": "<1 Satz Gesamtbild>"
}

Wenn ein Wert fehlt (None/null in den Daten), gib severity=ok und
explanation='Wert nicht verfuegbar — kein Sanity-Check moeglich.'
Keine Markdown-Zaeune, kein Text drumherum.
"""


def _gather_period_data(
    db: Session,
    company_id: UUID,
    period_year: int,
) -> dict:
    """Sammle FY-Werte fuer aktuelles + Vorjahr."""
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
                "source": (r.source_name or "")[:120],
                "currency": r.currency,
                "is_forecast": r.is_forecast,
                "manually_overridden": r.manually_overridden,
            }
    return out


def _delete_old_sanity_messages(
    db: Session,
    company_id: UUID,
    period_year: int,
) -> None:
    """ALLE Sanity-Check-Messages fuer dieses FY weg — egal welcher value_key.
    Verhindert Stapeln + entfernt auch Legacy-Messages aus alten Code-Versionen
    (z.B. fuer market_cap, stock_price etc., die wir aktuell nicht checken)."""
    from app.llm.models import LlmConversation, LlmMessage
    convs = (
        db.query(LlmConversation.id)
        .filter(
            LlmConversation.company_id == company_id,
            LlmConversation.period_type == "FY",
            LlmConversation.period_year == period_year,
        )
        .all()
    )
    if not convs:
        return
    conv_ids = [c[0] for c in convs]
    db.query(LlmMessage).filter(
        LlmMessage.conversation_id.in_(conv_ids),
        LlmMessage.source == "sanity_check",
    ).delete(synchronize_session=False)


def run_sanity_check(
    db: Session,
    company_id: UUID,
    period_year: int,
) -> dict | None:
    """Run per-value LLM sanity check ueber FY[period_year]. Persistiert eine
    System-Message pro Primaer-/Aggregat-Wert in dessen Chat — sowohl OK als
    auch Auffaelligkeit. Returns das geparste JSON dict oder None bei Fehler."""
    if not settings.anthropic_api_key:
        return None

    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if company is None:
        return None

    data = _gather_period_data(db, company_id, period_year)
    fy_end = (
        f"{company.fiscal_year_end_day:02d}.{company.fiscal_year_end_month:02d}."
        if company.fiscal_year_end_day and company.fiscal_year_end_month
        else "unbekannt"
    )
    user_prompt = (
        f"Unternehmen: {company.name} ({company.ticker}, Currency: {company.currency})\n"
        f"FY-End-Stichtag: {fy_end}\n\n"
        f"Daten FY{period_year} und FY{period_year-1} (Vorjahr als Vergleich):\n\n"
        f"{json.dumps(data, indent=2, ensure_ascii=False)}\n\n"
        f"Bewerte jeden der genannten Werte einzeln und antworte als JSON."
    )

    try:
        response = claude_limiter.call(lambda: _client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
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

    checks = parsed.get("checks", {}) or {}
    if not isinstance(checks, dict):
        logger.warning("Sanity check: 'checks' missing or not dict for %s/FY%s", company_id, period_year)
        return parsed

    # Alte Sanity-Messages weg, dann neue schreiben — pro Wert genau eine.
    _delete_old_sanity_messages(db, company_id, period_year)

    try:
        from app.llm.routes import _get_or_create_conversation
        from app.llm.models import LlmMessage
        for key in _ALL_CHECKED_KEYS:
            check = checks.get(key)
            if not isinstance(check, dict):
                continue
            severity = (check.get("severity") or "ok").upper()
            title = check.get("title") or "Sanity-Check"
            explanation = check.get("explanation") or ""
            try:
                conv = _get_or_create_conversation(db, company_id, key, "FY", period_year)
                db.add(LlmMessage(
                    conversation_id=conv.id,
                    role="system",
                    content=f"Sanity-Check [{severity}]: {title}\n{explanation}",
                    source="sanity_check",
                ))
            except Exception as e:
                logger.warning("Sanity-check msg write failed for %s/%s: %s", company_id, key, e)
        db.flush()
    except Exception as e:
        logger.warning("Sanity-check persistence failed for %s/FY%s: %s", company_id, period_year, e)

    return parsed
