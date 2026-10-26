"""Settings read from the environment (and from .env, if present)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_url: str
    cors_origins: list[str]
    telegram_bot_token: str | None
    telegram_chat_id: str | None


def load_settings() -> Settings:
    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set (see .env.example)")
    origins = os.environ.get("CORS_ORIGINS", "")
    return Settings(
        database_url=database_url,
        cors_origins=[o.strip().rstrip("/") for o in origins.split(",") if o.strip()],
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID") or None,
    )
