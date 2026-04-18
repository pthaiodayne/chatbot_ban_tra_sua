from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
BASE_URL = os.getenv("BASE_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./shop.db")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
SELLER_CHAT_ID = os.getenv("SELLER_CHAT_ID", "")
