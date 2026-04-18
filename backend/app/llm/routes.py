import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.models import User
from app.companies.models import Company
from app.db import get_db
from app.llm.claude import call_claude
from app.llm.models import LlmConversation, LlmMessage
from app.llm.schemas import AnalyzeResponse, ChatHistoryOut, ChatRequest
from app.portfolios.models import Portfolio
from app.values.models import CompanyValue, SourceType, ValueDefinition

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/companies", tags=["llm"])


def _get_owned_company(db: Session, user: User, company_id: UUID) -> Company:
    company = db.query(Company).filter(Company.id == company_id).one_or_none()
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    portfolio = db.query(Portfolio).filter(Portfolio.id == company.portfolio_id).one_or_none()
    if not portfolio or portfolio.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company


def _get_or_create_conversation(
    db: Session, company_id: UUID, value_key: str,
    period_type: str = "SNAPSHOT", period_year: int | None = None,
) -> LlmConversation:
    q = db.query(LlmConversation).filter(
        LlmConversation.company_id == company_id,
        LlmConversation.value_key == value_key,
        LlmConversation.period_type == period_type,
    )
    if period_year is None:
        q = q.filter(LlmConversation.period_year.is_(None))
    else:
        q = q.filter(LlmConversation.period_year == period_year)
    conv = q.first()
    if conv:
        return conv
    conv = LlmConversation(
        company_id=company_id, value_key=value_key,
        period_type=period_type, period_year=period_year,
    )
    db.add(conv)
    db.flush()
    return conv


def _build_company_context(db: Session, company: Company, period_type: str = "SNAPSHOT", period_year: int | None = None) -> str:
    values = db.query(CompanyValue, ValueDefinition).join(
        ValueDefinition, CompanyValue.value_key == ValueDefinition.key, isouter=True
    ).filter(
        CompanyValue.company_id == company.id,
        CompanyValue.period_type == "SNAPSHOT",
    ).all()

    data = {}
    for v, vd in values:
        label = vd.label_en if vd else v.value_key
        if v.numeric_value is not None:
            data[label] = str(v.numeric_value)
        elif v.text_value is not None:
            data[label] = v.text_value

    if period_type == "FY" and period_year:
        period_str = f"FY{period_year}"
    elif period_type == "LTM":
        period_str = "LTM"
    elif period_type == "TTM":
        period_str = "TTM"
    else:
        period_str = "aktuell"

    return (
        f"Unternehmen: {company.name} ({company.ticker}, ISIN: {company.isin or 'N/A'}, Currency: {company.currency})\n"
        f"Aktueller Analyse-Zeitraum: {period_str}\n\n"
        f"Verfuegbare Finanzdaten:\n{json.dumps(data, indent=2, ensure_ascii=False)}"
    )


def _get_value_label(db: Session, value_key: str) -> str:
    vd = db.query(ValueDefinition).filter(ValueDefinition.key == value_key).one_or_none()
    if vd:
        return f"{vd.label_en} ({vd.label_de})"
    return value_key


@router.post("/{company_id}/analyze/{value_key}", response_model=AnalyzeResponse)
def analyze_value(
    company_id: UUID,
    value_key: str,
    period_type: str = "SNAPSHOT",
    period_year: int | None = None,
    force: bool = False,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    company = _get_owned_company(db, user, company_id)
    conv = _get_or_create_conversation(db, company_id, value_key, period_type, period_year)

    existing_messages = (
        db.query(LlmMessage)
        .filter(LlmMessage.conversation_id == conv.id)
        .order_by(LlmMessage.created_at, LlmMessage.id)
        .all()
    )
    if not force and existing_messages:
        last_assistant = next(
            (m for m in reversed(existing_messages) if m.role == "assistant"),
            None,
        )
        if last_assistant and last_assistant.source != "research":
            return {"conversation_id": conv.id, "message": last_assistant}

    context = _build_company_context(db, company, period_type, period_year)
    label = _get_value_label(db, value_key)
    vd = db.query(ValueDefinition).filter(ValueDefinition.key == value_key).one_or_none()
    is_qualitative = vd and vd.source_type == SourceType.QUALITATIVE

    if period_type == "FY" and period_year:
        period_str = f"Geschäftsjahr {period_year} (FY{period_year})"
    elif period_type == "LTM":
        period_str = "letzte 12 Monate (LTM)"
    elif period_type == "TTM":
        period_str = "trailing twelve months (TTM)"
    else:
        period_str = "aktuell"

    if is_qualitative:
        initial_prompt = f"Bewerte den folgenden Aspekt für {company.name}:\n\nAspekt: {label}\n\nGib einen Score von 0.5 bis 1.5 und eine Begründung."
        mode = "qualitative"
    else:
        unit_hint = f" (Einheit: {vd.unit})" if vd and vd.unit else ""
        initial_prompt = (
            f"Recherchiere den folgenden Finanzkennwert für {company.name} ({company.ticker}):\n\n"
            f"Kennzahl: {label}{unit_hint}\n"
            f"Zeitraum: {period_str}\n\n"
            f"Wichtig: Liefere NUR den Wert für {period_str}. Wenn du dafür keinen verifizierbaren Wert findest, antworte mit WERT: NICHT_GEFUNDEN.\n\n"
            f"Antworte mit:\n"
            f"1. WERT: [Zahl]\n"
            f"2. QUELLE: [Woher der Wert stammt]\n"
            f"3. ZEITRAUM: [Bestätigung des Zeitraums]\n"
            f"4. Kurze Erklärung (1-2 Sätze)"
        )
        mode = "research"

    user_msg = LlmMessage(conversation_id=conv.id, role="user", content=initial_prompt, source="analyze")
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    messages = [{"role": "user", "content": initial_prompt}]

    try:
        content, score = call_claude(messages, context, mode=mode)
    except Exception as e:
        logger.error("Claude API error: %s", e)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Claude API nicht erreichbar")

    assistant_msg = LlmMessage(
        conversation_id=conv.id,
        role="assistant",
        content=content,
        score_suggestion=score,
        source="analyze",
    )
    db.add(assistant_msg)
    db.commit()
    db.refresh(assistant_msg)

    return {
        "conversation_id": conv.id,
        "message": assistant_msg,
    }


@router.post("/{company_id}/chat/{value_key}", response_model=AnalyzeResponse)
def chat_message(
    company_id: UUID,
    value_key: str,
    payload: ChatRequest,
    period_type: str = "SNAPSHOT",
    period_year: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    company = _get_owned_company(db, user, company_id)
    conv = _get_or_create_conversation(db, company_id, value_key, period_type, period_year)
    context = _build_company_context(db, company, period_type, period_year)

    vd = db.query(ValueDefinition).filter(ValueDefinition.key == value_key).one_or_none()
    is_qualitative = vd and vd.source_type == SourceType.QUALITATIVE
    mode = "qualitative" if is_qualitative else "research"

    user_msg = LlmMessage(conversation_id=conv.id, role="user", content=payload.message, source="chat")
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    existing = (
        db.query(LlmMessage)
        .filter(LlmMessage.conversation_id == conv.id)
        .order_by(LlmMessage.created_at, LlmMessage.id)
        .all()
    )
    messages = [{"role": m.role, "content": m.content} for m in existing]

    try:
        content, score = call_claude(messages, context, mode=mode)
    except Exception as e:
        logger.error("Claude API error: %s", e)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Claude API nicht erreichbar")

    assistant_msg = LlmMessage(
        conversation_id=conv.id,
        role="assistant",
        content=content,
        score_suggestion=score,
        source="chat",
    )
    db.add(assistant_msg)
    db.commit()
    db.refresh(assistant_msg)

    return {
        "conversation_id": conv.id,
        "message": assistant_msg,
    }


@router.get("/{company_id}/chat/{value_key}/history", response_model=ChatHistoryOut)
def get_chat_history(
    company_id: UUID,
    value_key: str,
    period_type: str = "SNAPSHOT",
    period_year: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    _get_owned_company(db, user, company_id)
    q = db.query(LlmConversation).filter(
        LlmConversation.company_id == company_id,
        LlmConversation.value_key == value_key,
        LlmConversation.period_type == period_type,
    )
    if period_year is None:
        q = q.filter(LlmConversation.period_year.is_(None))
    else:
        q = q.filter(LlmConversation.period_year == period_year)
    conv = q.first()
    if not conv:
        return {"conversation_id": None, "messages": []}

    messages = (
        db.query(LlmMessage)
        .filter(LlmMessage.conversation_id == conv.id)
        .order_by(LlmMessage.created_at, LlmMessage.id)
        .all()
    )

    return {"conversation_id": conv.id, "messages": messages}
