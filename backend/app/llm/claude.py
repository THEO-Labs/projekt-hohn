import re
import logging
from decimal import Decimal, InvalidOperation

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Du bist ein erfahrener Finanzanalyst bei einem Investmentunternehmen.
Du hast zwei Aufgaben:

1. QUALITATIVE BEWERTUNG: Bewerte qualitative Faktoren auf einer Skala von 0.5 bis 1.5.
   0.5 = sehr hohes Risiko / sehr schlecht, 1.0 = neutral, 1.5 = sehr gut
   Antworte mit: SCORE: [Zahl], BEGRÜNDUNG, FAKTOREN, QUELLEN

2. FINANZKENNZAHLEN RECHERCHE: Wenn nach einem konkreten Finanzkennwert gefragt wird,
   recherchiere den Wert und antworte mit:
   WERT: [Zahl]
   QUELLE: [Woher der Wert stammt]
   Erkläre kurz woher der Wert kommt.

Nutze echte Quellen (Geschäftsberichte, Analystenkonsens, Finanzdatenbanken).
Sei präzise. Antworte auf Deutsch, Fachbegriffe auf Englisch.
Wenn du einen Wert nicht sicher weisst, sage das ehrlich."""


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


def extract_value(text: str) -> Decimal | None:
    match = re.search(r"WERT:\s*([+-]?[\d.,]+(?:\s*(?:Mrd|Mio|B|M|T)\.?)?)", text, re.IGNORECASE)
    if not match:
        return extract_score(text)
    raw = match.group(1).strip()
    multiplier = Decimal("1")
    for suffix, mult in [("Mrd", "1000000000"), ("B", "1000000000"), ("Mio", "1000000"), ("M", "1000000"), ("T", "1000")]:
        if suffix.lower() in raw.lower():
            multiplier = Decimal(mult)
            raw = re.sub(r"\s*(Mrd|Mio|B|M|T)\.?", "", raw, flags=re.IGNORECASE)
            break
    raw = raw.strip().rstrip(".")
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return Decimal(raw) * multiplier
    except (InvalidOperation, ValueError):
        return None


RESEARCH_PROMPT = """Du bist ein Finanzanalyst. Dir wird eine Finanzkennzahl für ein Unternehmen gefragt,
die nicht über die API verfügbar war. Recherchiere den Wert basierend auf deinem Wissen.

Antworte NUR in diesem Format:
WERT: [Zahl]
EINHEIT: [z.B. USD, EUR, %, keine]
QUELLE: [Kurze Beschreibung der Quelle]
QUELLE_URL: [Direkte URL zur Quelle, z.B. https://www.allianz.com/en/investor_relations/ oder https://finance.yahoo.com/quote/ALV.DE/ oder https://www.wsj.com/... ]
ZEITRAUM: [z.B. FY2024, TTM, aktuell]
KONFIDENZ: [hoch/mittel/niedrig]

Wenn du den Wert nicht findest, antworte mit:
WERT: NICHT_GEFUNDEN

Wichtig:
- Gib nur verifizierbare Zahlen an. Im Zweifel NICHT_GEFUNDEN.
- Die QUELLE_URL muss eine echte, existierende URL sein (Investor Relations Seite, Yahoo Finance, Bloomberg, Reuters, etc.)
- Keine erfundenen URLs."""


def extract_research_value(text: str) -> Decimal | None:
    match = re.search(r"WERT:\s*([+-]?\d[\d.,]*)", text)
    if not match:
        return None
    try:
        raw = match.group(1).replace(".", "").replace(",", ".") if "," in match.group(1) else match.group(1)
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def research_value(company_name: str, ticker: str, value_label: str, currency: str, period_type: str = "SNAPSHOT", period_year: int | None = None) -> tuple[Decimal | None, str | None, str | None]:
    if period_type == "FY" and period_year:
        period_str = f"Geschäftsjahr {period_year} (FY{period_year})"
    elif period_type == "LTM":
        period_str = "letzte 12 Monate (LTM)"
    elif period_type == "TTM":
        period_str = "trailing twelve months (TTM)"
    else:
        period_str = "aktueller/letzter verfügbarer Wert"
    try:
        client = get_client()
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=[{"type": "text", "text": RESEARCH_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{
                "role": "user",
                "content": f"Unternehmen: {company_name} ({ticker}, {currency})\nGesuchte Kennzahl: {value_label}\nZeitraum: {period_str}\n\nWichtig: Liefere NUR den Wert für den angegebenen Zeitraum. Wenn du für {period_str} keinen verifizierbaren Wert findest, antworte mit WERT: NICHT_GEFUNDEN."
            }],
        )
        content = response.content[0].text
        value = extract_research_value(content)
        if value is None:
            return None, None, None
        source_match = re.search(r"QUELLE:\s*(.+)", content)
        source = source_match.group(1).strip() if source_match else "Claude-Recherche"
        url_match = re.search(r"QUELLE_URL:\s*(https?://\S+)", content)
        source_url = url_match.group(1).strip() if url_match else None
        return value, f"Claude-Recherche: {source}", source_url
    except Exception as e:
        logger.warning("Claude research failed for %s/%s: %s", ticker, value_label, e)
        return None, None, None


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
    score = extract_value(content)
    return content, score
