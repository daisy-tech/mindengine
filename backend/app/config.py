"""Settings (pydantic-settings) — single source of runtime config.

Per docs/rebuild/02-TDD.md §7. Defaults are dev-friendly. Production must
override via env or a real .env file.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── runtime
    runtime_kind: Literal["web", "worker"] = "web"
    app_title: str = "MindEngine API"
    log_level: str = "INFO"
    prompt_timezone: str = "Asia/Shanghai"

    # ─── database (asyncpg by default)
    database_url: str = (
        "postgresql+asyncpg://mindengine:mindengine@localhost:5432/mindengine"
    )

    # ─── redis
    redis_url: str = "redis://localhost:6379/0"
    intent_cache_enabled: bool = True

    # ─── LLM
    openai_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    openai_api_key: str = "replace-me"
    enable_thinking: bool = False
    intent_model_temperature: float = 0.0

    # ─── auth
    jwt_secret: str = Field(default="dev-secret-change-me")
    jwt_alg: str = "HS256"
    jwt_ttl_min: int = 60

    # ─── dev / safety
    dev_mode: bool = False
    allow_destructive_dev: bool = False

    # ─── eval
    eval_chat_reviews_dir: str = "/app/eval/exports/reviews"
    eval_pass_threshold: float = 0.85
    judge_enabled: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
