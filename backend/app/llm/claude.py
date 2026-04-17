import re
import logging
from decimal import Decimal, InvalidOperation

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Du bist ein erfahrener Finanzanalyst bei einem Investmentunternehmen.
Du bewertest qualitative Faktoren für Unternehmen auf einer Skala von 0.5 bis 1.5.

0.5 = sehr hohes Risiko / sehr schlecht
1.0 = neutral / durchschnittlich
1.5 = sehr geringes Risiko / sehr gut

Antworte immer mit:
1. SCORE: [Dezimalzahl 0.5-1.5]
2. BEGRÜNDUNG: [2-3 Sätze]
3. FAKTOREN:
   - [Stichpunkt 1]
   - [Stichpunkt 2]
   - [Stichpunkt 3]
4. QUELLEN:
   - [Name der Quelle](URL) - kurze Beschreibung
   - [Name der Quelle](URL) - kurze Beschreibung

Nutze echte, aktuelle Quellen (Geschäftsberichte, Nachrichtenartikel, Analystenbewertungen).
Sei präzise und nutze die bereitgestellten Finanzdaten als Grundlage.
Antworte auf Deutsch, Fachbegriffe auf Englisch."""


def get_client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def extract_score(text: str) -> Decimal | None:
    match = re.search(r"SCORE:\s*(\d+[.,]\d+)", text, re.IGNORECASE)
    if not match:
        return None
    try:
        score = Decimal(match.group(1).replace(",", "."))
        if Decimal("0.5") <= score <= Decimal("1.5"):
            return score
        return None
    except InvalidOperation:
        return None


def call_claude(messages: list[dict[str, str]], company_context: str) -> tuple[str, Decimal | None]:
    client = get_client()

    user_messages = []
    for msg in messages:
        user_messages.append({"role": msg["role"], "content": msg["content"]})

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT + "\n\n" + company_context,
            "cache_control": {"type": "ephemeral"}
        }],
        messages=user_messages,
    )

    content = response.content[0].text
    score = extract_score(content)
    return content, score
