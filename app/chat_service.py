from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ChatMessage


MAX_CHAT_HISTORY = 4


def save_chat_message(db: Session, telegram_user_id: str, role: str, content: str) -> ChatMessage:
    message = ChatMessage(
        telegram_user_id=telegram_user_id,
        role=role,
        content=content.strip(),
    )
    db.add(message)
    db.flush()

    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.telegram_user_id == telegram_user_id)
        .order_by(ChatMessage.id.desc())
        .all()
    )
    for stale_message in messages[MAX_CHAT_HISTORY:]:
        db.delete(stale_message)

    db.commit()
    db.refresh(message)
    return message


def get_recent_chat_messages(db: Session, telegram_user_id: str, limit: int = MAX_CHAT_HISTORY) -> list[ChatMessage]:
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.telegram_user_id == telegram_user_id)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(messages))


def render_chat_history(messages: list[ChatMessage]) -> str:
    if not messages:
        return "Chưa có lịch sử hội thoại gần đây."

    lines = []
    for message in messages:
        speaker = "Khách" if message.role == "user" else "Bot"
        lines.append(f"{speaker}: {message.content}")
    return "\n".join(lines)
