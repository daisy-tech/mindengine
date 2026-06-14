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

    # ─── dispatcher
    # 'celery' uses the Redis broker; 'in_memory' is a no-op recorder for
    # local dev when no worker is running. Tests pin this explicitly.
    task_dispatcher: Literal["celery", "in_memory"] = "celery"

    # ─── eval
    eval_chat_reviews_dir: str = "/app/eval/exports/reviews"
    eval_synthetic_cases_dir: str = "/app/eval/cases"
    # 合成评测「上次结果」每次跑完落盘到这里,前端可凭 run_id 反查;
    # 不写盘的话用户每次都要等 1~6 分钟跑完才能看到一次结果。
    eval_synthetic_runs_dir: str = "/app/eval/exports/synthetic"
    # Prompt archive 旁路落盘:每条 assistant message 写一个独立 JSON,
    # 包括完整 system + user + reply,供 prompt 调优时逐条复看。
    # 设为空字符串可关闭(归档磁盘吃紧时);默认开启。
    prompt_archive_dir: str = "/app/eval/exports/prompts"
    # 抽取去重阈值。情景层用 pgvector ANN 余弦相似度;同一只猫"奶黄"
    # 的 18 条重复记忆都来自这里没拦住(v2.0.2.7 之前完全没这环节)。
    # 0.90 是中文 embedding 实测的合理起点 —— 同义改写能命中,但语序
    # 翻转的真新事实("用户养了奶黄" vs "奶黄是用户养的")不会误杀。
    episodic_dedup_threshold: float = 0.90
    # 事件层(无 embedding)用 (type, normalized_title) 字符串去重;
    # 30 天窗口足够覆盖同一段时间内反复说同一件事的场景。
    event_dedup_window_days: int = 30
    eval_pass_threshold: float = 0.85
    judge_enabled: bool = False
    eval_user_id: str = "eval-bot-zhangsan"

    # ─── correction
    correction_confidence_threshold: float = 0.7
    correction_candidate_limit: int = 10
    banned_max_len: int = 8


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
