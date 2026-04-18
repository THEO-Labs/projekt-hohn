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
from app.llm.schemas import AnalyzeResponse, ChatHistoryOut, ChatRequest, LlmMessageOut
from app.portfolios.models import Portfolio
from app.values.models import CompanyValue, ValueDefinition

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


def _get_or_create_conversation(db: Session, company_id: UUID, value_key: str) -> LlmConversation:
    conv = (
        db.query(LlmConversation)
        .filter(LlmConversation.company_id == company_id, LlmConversation.value_key == value_key)
        .order_by(LlmConversation.created_at.desc())
        .first()
    )
    if conv:
        return conv
    conv = LlmConversation(company_id=company_id, value_key=value_key)
    db.add(conv)
    db.flush()
    return conv


def _build_company_context(db: Session, company: Company) -> str:
    values = db.query(CompanyValue).filter(
        CompanyValue.company_id == company.id,
        CompanyValue.period_type == "SNAPSHOT",
    ).all()

    data = {}
    for v in values:
        if v.numeric_value is not None:
            data[v.value_key] = str(v.numeric_value)
        elif v.text_value is not None:
            data[v.value_key] = v.text_value

    return f"Unternehmen: {company.name} ({company.ticker}, ISIN: {company.isin or 'N/A'}, Currency: {company.currency})\n\nVerfuegbare Finanzdaten:\n{json.dumps(data, indent=2, ensure_ascii=False)}"


def _get_value_label(db: Session, value_key: str) -> str:
    vd = db.query(ValueDefinition).filter(ValueDefinition.key == value_key).one_or_none()
    if vd:
        return f"{vd.label_en} ({vd.label_de})"
    return value_key


@router.post("/{company_id}/analyze/{value_key}", response_model=AnalyzeResponse)
def analyze_value(
    company_id: UUID,
    value_key: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    company = _get_owned_company(db, user, company_id)
    conv = _get_or_create_conversation(db, company_id, value_key)

    existing_messages = (
        db.query(LlmMessage)
        .filter(LlmMessage.conversation_id == conv.id)
        .order_by(LlmMessage.created_at)
        .all()
    )
    if existing_messages:
        last_assistant = next(
            (m for m in reversed(existing_messages) if m.role == "assistant"),
            None,
        )
        if last_assistant:
            return {"conversation_id": conv.id, "message": last_assistant}

    context = _build_company_context(db, company)
    label = _get_value_label(db, value_key)
    vd = db.query(ValueDefinition).filter(ValueDefinition.key == value_key).one_or_none()
    is_qualitative = vd and vd.source_type.value == "QUALITATIVE"

    if is_qualitative:
        initial_prompt = f"Bewerte den folgenden Aspekt:\n\nAspekt: {label}\n\nGib einen Score von 0.5 bis 1.5 und eine Begründung."
    else:
        unit_hint = f" (Einheit: {vd.unit})" if vd and vd.unit else ""
        initial_prompt = (
            f"Recherchiere den folgenden Finanzkennwert für {company.name} ({company.ticker}):\n\n"
            f"Kennzahl: {label}{unit_hint}\n\n"
            f"Antworte mit:\n"
            f"1. WERT: [Zahl]\n"
            f"2. QUELLE: [Woher der Wert stammt]\n"
            f"3. ZEITRAUM: [z.B. FY2024, TTM, aktuell]\n"
            f"4. Kurze Erklärung (1-2 Sätze)"
        )

    user_msg = LlmMessage(conversation_id=conv.id, role="user", content=initial_prompt)
    db.add(user_msg)
    db.flush()

    messages = [{"role": "user", "content": initial_prompt}]

    try:
        content, score = call_claude(messages, context)
    except Exception as e:
        logger.error("Claude API error: %s", e)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Claude API nicht erreichbar")

    assistant_msg = LlmMessage(
        conversation_id=conv.id,
        role="assistant",
        content=content,
        score_suggestion=score,
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
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    company = _get_owned_company(db, user, company_id)
    conv = _get_or_create_conversation(db, company_id, value_key)
    context = _build_company_context(db, company)

    user_msg = LlmMessage(conversation_id=conv.id, role="user", content=payload.message)
    db.add(user_msg)
    db.flush()

    existing = (
        db.query(LlmMessage)
        .filter(LlmMessage.conversation_id == conv.id)
        .order_by(LlmMessage.created_at)
        .all()
    )
    messages = [{"role": m.role, "content": m.content} for m in existing]

    try:
        content, score = call_claude(messages, context)
    except Exception as e:
        logger.error("Claude API error: %s", e)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Claude API nicht erreichbar")

    assistant_msg = LlmMessage(
        conversation_id=conv.id,
        role="assistant",
        content=content,
        score_suggestion=score,
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
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    _get_owned_company(db, user, company_id)
    conv = (
        db.query(LlmConversation)
        .filter(LlmConversation.company_id == company_id, LlmConversation.value_key == value_key)
        .order_by(LlmConversation.created_at.desc())
        .first()
    )
    if not conv:
        conv = _get_or_create_conversation(db, company_id, value_key)
        db.commit()

    messages = (
        db.query(LlmMessage)
        .filter(LlmMessage.conversation_id == conv.id)
        .order_by(LlmMessage.created_at)
        .all()
    )

    return {"conversation_id": conv.id, "messages": messages}
