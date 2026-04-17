from __future__ import annotations

import requests

from app.config import TELEGRAM_BOT_TOKEN


class TelegramConfigError(RuntimeError):
    pass


def telegram_api_url(method: str) -> str:
    if not TELEGRAM_BOT_TOKEN:
        raise TelegramConfigError("TELEGRAM_BOT_TOKEN chưa được cấu hình.")
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"


def send_message(chat_id: int, text: str) -> dict:
    payload = {"chat_id": chat_id, "text": text}
    response = requests.post(telegram_api_url("sendMessage"), json=payload, timeout=20)
    response.raise_for_status()
    return response.json()


def set_webhook(webhook_url: str) -> dict:
    payload = {"url": webhook_url, "drop_pending_updates": True}
    response = requests.post(telegram_api_url("setWebhook"), json=payload, timeout=20)
    response.raise_for_status()
    return response.json()
