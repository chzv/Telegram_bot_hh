# backend/app/core/config.py
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = ROOT / ".env"
load_dotenv(ENV_FILE)  

import os
from typing import List

def _get(name: str, default: Optional[str] = None, cast=None, required: bool = False):
    v = os.getenv(name, default)
    if required and (v is None or str(v).strip() == ""):
        raise RuntimeError(f"Отсутствует обязательная переменная окружения: {name} (проверь {ENV_FILE})")
    if v is None or v == "":
        return default
    if cast is not None:
        try:
            return cast(v)
        except Exception:
            return default
    return v

def _get_list(name: str, default: str = "") -> List[str]:
    raw = os.getenv(name, default)
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]

@dataclass
class Settings:
    database_url: str                    
    backend_base_url: str                

    hh_client_id: str                     
    hh_client_secret: str                
    hh_redirect_uri: str                  

    telegram_bot_token: str                

    bot_username: str | None = _get('BOT_USERNAME', default=None)
    env: str = "dev"

    hh_oauth_base: str = "https://hh.ru"
    hh_api_base: str = "https://api.hh.ru"
    hh_dev_fake: int = 0

    cors_origins: List[str] = field(default_factory=list)

def load_settings() -> Settings:
    return Settings(
        database_url=_get("DATABASE_URL", required=True),
        backend_base_url=_get("BACKEND_BASE_URL", required=True),

        hh_client_id=_get("HH_CLIENT_ID", required=True) or "",
        hh_client_secret=_get("HH_CLIENT_SECRET", required=True) or "",
        hh_redirect_uri=_get("HH_REDIRECT_URI", required=True) or "",

        telegram_bot_token=_get("TELEGRAM_BOT_TOKEN", default="") or "",

        env=_get("ENV", default="dev") or "dev",

        hh_oauth_base=_get("HH_OAUTH_BASE", default="https://hh.ru") or "https://hh.ru",
        hh_api_base=_get("HH_API_BASE", default="https://api.hh.ru") or "https://api.hh.ru",
        hh_dev_fake=int(_get("HH_DEV_FAKE", default="0") or "0"),

        cors_origins=_get_list("CORS_ORIGINS"),
    )

CP_PUBLIC_ID = os.getenv("CP_PUBLIC_ID","")
CP_API_SECRET = os.getenv("CP_API_SECRET","")
BASE_URL = os.getenv("BASE_URL","http://localhost:8000").rstrip("/")
PAY_RETURN_BOT_URL = 

settings = load_settings()
settings.DATABASE_URL = settings.database_url