"""End-to-end self-test for the running MindEngine stack.

USAGE (inside the backend container)::

    docker compose exec backend python -m selftest.run
    docker compose exec backend python -m selftest.run --keep-data
    docker compose exec backend python -m selftest.run --no-llm-soft

Exit code is ``0`` if every HARD check passes (LLM-dependent checks are
SOFT by default — they print WARN but never flip the exit code).

What it covers
--------------

Tier 1  环境就绪
    /healthz, /readyz, settings.openai_api_key, alembic 已迁移

Tier 2  全链路 API
    register → login → me → change-password → 旧密码失效
    建会话 → SSE 聊天(自我介绍)→ assistant 落库 + meta.route
    PATCH profile → 回读;banned-entity 写入 → 回读
    记忆抽取 E2E(等 celery 干活)→ profile/episodic 增长 [SOFT]
    记忆召回 E2E(下一轮回复包含已存事实)         [SOFT]
    eval / conversations 只读端点

Tier 3  清理
    默认删除测试用户的所有数据(--keep-data 保留)。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import text

from app.config import get_settings
from app.infra.db.factory import make_engine, make_sessionmaker

# ─────────────────────────────────────────────────────────── tty colours

_USE_COLOR = sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    return f"\x1b[{code}m{s}\x1b[0m" if _USE_COLOR else s


def _green(s: str) -> str:  return _c("32", s)
def _red(s: str) -> str:    return _c("31", s)
def _yellow(s: str) -> str: return _c("33", s)
def _cyan(s: str) -> str:   return _c("36", s)
def _dim(s: str) -> str:    return _c("2", s)


# ─────────────────────────────────────────────────────────── results


@dataclass
class Counters:
    pass_: int = 0
    fail: int = 0
    warn: int = 0
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def section(title: str) -> None:
    print()
    print(_cyan(f"━━━ {title} ━━━"))


def ok(label: str, detail: str = "", counters: Counters | None = None) -> None:
    print(f"  {_green('✓ PASS')}  {label}" + (f"  {_dim(detail)}" if detail else ""))
    if counters is not None:
        counters.pass_ += 1


def warn(label: str, detail: str = "", counters: Counters | None = None) -> None:
    print(f"  {_yellow('● WARN')}  {label}" + (f"  {_dim(detail)}" if detail else ""))
    if counters is not None:
        counters.warn += 1
        counters.warnings.append(f"{label}  ({detail})" if detail else label)


def fail(label: str, detail: str = "", counters: Counters | None = None) -> None:
    print(f"  {_red('✗ FAIL')}  {label}" + (f"  {_dim(detail)}" if detail else ""))
    if counters is not None:
        counters.fail += 1
        counters.failures.append(f"{label}  ({detail})" if detail else label)


# ─────────────────────────────────────────────────────────── SSE parser


_SSE_FIELD = re.compile(r"^(event|data):\s?(.*)$")


async def stream_sse(
    client: httpx.AsyncClient,
    path: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any],
    overall_timeout_s: float = 60.0,
) -> tuple[list[tuple[str, str]], str]:
    """POST + read SSE. Returns (events, full_assistant_text).

    ``events`` preserves order: e.g. ``[("delta", "你"), ("delta", "好"), ("done", "{}")]``.
    Comment lines (``: ping``) are skipped silently.

    Raises ``httpx.HTTPStatusError`` if the response status is not 2xx.
    """
    events: list[tuple[str, str]] = []
    assistant_text = ""
    cur_event = "message"
    deadline = time.monotonic() + overall_timeout_s

    async with client.stream(
        "POST", path, headers=headers, json=json_body, timeout=overall_timeout_s
    ) as r:
        r.raise_for_status()
        async for raw_line in r.aiter_lines():
            if time.monotonic() > deadline:
                events.append(("_client_timeout", ""))
                break
            line = raw_line.rstrip("\r")
            if line == "":
                cur_event = "message"
                continue
            if line.startswith(":"):
                continue
            m = _SSE_FIELD.match(line)
            if not m:
                continue
            field_name, value = m.group(1), m.group(2)
            if field_name == "event":
                cur_event = value.strip()
            elif field_name == "data":
                events.append((cur_event, value))
                if cur_event == "delta":
                    # Server sends raw text in `data:` for delta events.
                    assistant_text += value
                if cur_event == "done":
                    break
    return events, assistant_text


# ─────────────────────────────────────────────────────────── http helper


class Api:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
        self.token: str | None = None
        self.user_id: str | None = None

    async def aclose(self) -> None:
        await self.client.aclose()

    @property
    def auth_headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    async def register(self, email: str, password: str) -> httpx.Response:
        return await self.client.post(
            "/auth/register",
            json={"email": email, "password": password, "display_name": "selftest"},
        )

    async def login(self, email: str, password: str) -> httpx.Response:
        return await self.client.post(
            "/auth/login", json={"email": email, "password": password}
        )

    async def me(self) -> httpx.Response:
        return await self.client.get("/auth/me", headers=self.auth_headers)

    async def change_password(self, current: str, new: str) -> httpx.Response:
        return await self.client.post(
            "/auth/change-password",
            headers=self.auth_headers,
            json={"current_password": current, "new_password": new},
        )

    async def create_conversation(self, conv_id: str) -> httpx.Response:
        return await self.client.post(
            "/api/conversations",
            headers=self.auth_headers,
            json={"id": conv_id, "title": "selftest"},
        )

    async def list_messages(self, conv_id: str) -> httpx.Response:
        return await self.client.get(
            f"/api/conversations/{conv_id}/messages",
            headers=self.auth_headers,
        )

    async def audit(self, conv_id: str) -> httpx.Response:
        return await self.client.get(
            f"/api/conversations/{conv_id}/audit",
            headers=self.auth_headers,
        )


# ─────────────────────────────────────────────────────────── DB helpers


async def db_check_users_table() -> tuple[bool, str]:
    settings = get_settings()
    engine = make_engine(settings.database_url, kind="web")
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(text("SELECT to_regclass('public.users')"))
            ).scalar_one_or_none()
            return (row is not None and row != ""), str(row)
    except Exception as e:  # noqa: BLE001
        return False, repr(e)
    finally:
        await engine.dispose()


# Tables that reference user_id, in safe deletion order (children → users last).
_USER_SCOPED_TABLES: tuple[str, ...] = (
    "messages",
    "conversations",
    "memory_deprecations",
    "banned_entities",
    "episodic_memories",
    "relationships",
    "events",
    "profiles",
    "llm_traces",
)


async def db_purge_user(user_id: str) -> dict[str, int]:
    """Delete every row scoped to ``user_id``. Returns ``{table: row_count}``.

    Used by the self-test cleanup. The order is hand-chosen so that
    foreign-keyed children go before parents (messages → conversations →
    users). All deletes are inside one transaction.
    """
    settings = get_settings()
    engine = make_engine(settings.database_url, kind="web")
    deleted: dict[str, int] = {}
    try:
        async with engine.begin() as conn:
            for tbl in _USER_SCOPED_TABLES:
                result = await conn.execute(
                    text(f"DELETE FROM {tbl} WHERE user_id = :uid"),
                    {"uid": user_id},
                )
                deleted[tbl] = result.rowcount or 0
            result = await conn.execute(
                text("DELETE FROM users WHERE id = :uid"), {"uid": user_id}
            )
            deleted["users"] = result.rowcount or 0
        return deleted
    finally:
        await engine.dispose()


# ─────────────────────────────────────────────────────────── tiers


async def tier1_environment(api: Api, c: Counters) -> bool:
    """Return False if any HARD check fails — caller should bail early."""
    section("Tier 1 · 环境就绪")
    all_hard_ok = True

    # 1.1 healthz
    try:
        r = await api.client.get("/healthz")
        if r.status_code == 200:
            ok("/healthz", r.json().get("status", ""), c)
        else:
            fail("/healthz", f"status={r.status_code}", c); all_hard_ok = False
    except Exception as e:  # noqa: BLE001
        fail("/healthz", repr(e), c); all_hard_ok = False

    # 1.2 readyz (Postgres + pgvector)
    try:
        r = await api.client.get("/readyz")
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 200 and body.get("status") == "ok":
            ok("/readyz (postgres + pgvector)", json.dumps(body.get("checks", {}), ensure_ascii=False), c)
        else:
            fail("/readyz", f"status={r.status_code} body={body}", c); all_hard_ok = False
    except Exception as e:  # noqa: BLE001
        fail("/readyz", repr(e), c); all_hard_ok = False

    # 1.3 OPENAI_API_KEY 真实存在 (web 进程视角)
    settings = get_settings()
    if settings.openai_api_key and settings.openai_api_key != "replace-me":
        masked = settings.openai_api_key[:6] + "…" + settings.openai_api_key[-4:]
        ok("OPENAI_API_KEY 已配置 (backend)", f"{masked}  base={settings.openai_base_url}", c)
    else:
        fail(
            "OPENAI_API_KEY 缺失或为 replace-me (backend)",
            "→ chat 会落到 MockLLMClient,记忆抽取也会全部失败",
            c,
        )
        all_hard_ok = False

    # 1.4 alembic 迁移已应用
    ok_users, detail = await db_check_users_table()
    if ok_users:
        ok("alembic 已迁移 (users 表存在)", detail, c)
    else:
        fail(
            "alembic 未迁移 (users 表不存在)",
            f"→ 请先 docker compose exec backend alembic upgrade head ({detail})",
            c,
        )
        all_hard_ok = False

    return all_hard_ok


async def tier2_auth(api: Api, c: Counters, email: str, password: str) -> bool:
    """Returns False if we can't get a working token (subsequent tiers skipped)."""
    section("Tier 2A · 认证")

    # 2.1 register
    r = await api.register(email, password)
    if r.status_code in (200, 201):
        body = r.json()
        api.token = body["access_token"]
        api.user_id = body["user_id"]
        ok("POST /auth/register", f"user_id={api.user_id}", c)
    else:
        fail("POST /auth/register", f"status={r.status_code} body={r.text[:200]}", c)
        return False

    # 2.2 me
    r = await api.me()
    if r.status_code == 200 and r.json().get("email", "").lower() == email.lower():
        ok("GET /auth/me", f"email={email}", c)
    else:
        fail("GET /auth/me", f"status={r.status_code} body={r.text[:200]}", c)

    # 2.3 change-password (旧 → 新)
    new_pwd = password + "_v2"
    r = await api.change_password(password, new_pwd)
    if r.status_code == 204:
        ok("POST /auth/change-password (旧→新)", "204", c)
    else:
        fail("POST /auth/change-password", f"status={r.status_code} body={r.text[:200]}", c)

    # 2.4 旧密码 login 应 401
    r = await api.login(email, password)
    if r.status_code == 401:
        ok("旧密码登录被拒 (401)", "", c)
    else:
        fail("旧密码登录被拒", f"status={r.status_code} (expected 401)", c)

    # 2.5 新密码 login 应 200
    r = await api.login(email, new_pwd)
    if r.status_code == 200:
        api.token = r.json()["access_token"]
        ok("新密码登录成功", "", c)
    else:
        fail("新密码登录", f"status={r.status_code} body={r.text[:200]}", c)
        return False

    return True


async def tier2_chat(
    api: Api, c: Counters, *, llm_soft: bool
) -> tuple[str | None, str]:
    """Run an SSE chat turn. Returns (conversation_id, assistant_text)."""
    section("Tier 2B · 会话 + SSE 聊天")

    conv_id = f"selftest-{secrets.token_hex(4)}"
    r = await api.create_conversation(conv_id)
    if r.status_code != 201:
        fail("POST /api/conversations", f"status={r.status_code} body={r.text[:200]}", c)
        return None, ""
    ok("POST /api/conversations", f"conv_id={conv_id}", c)

    # SSE turn — 不带个人信息,只验证流式通路。
    events, assistant_text = await stream_sse(
        api.client,
        "/api/chat",
        headers=api.auth_headers,
        json_body={"conversation_id": conv_id, "message": "你好,做个自我介绍吧"},
        overall_timeout_s=45.0,
    )

    has_error_event = any(k == "error" for k, _ in events)
    has_delta = any(k == "delta" for k, _ in events)
    has_done = any(k == "done" for k, _ in events)

    if has_error_event:
        err_data = next(v for k, v in events if k == "error")
        # 已知坑:MockLLMClient 没脚本 / 模型 id 不存在 / API key 失效
        if "MockLLM" in err_data:
            fail("SSE chat: 命中 MockLLMClient", err_data[:200], c)
        elif "compose_failed" in err_data:
            (warn if llm_soft else fail)(
                "SSE chat: compose_failed (LLM 调用失败)",
                err_data[:200] + "  ← 多半是模型 id 或 API key,看 backend 日志",
                c,
            )
        else:
            (warn if llm_soft else fail)("SSE chat: error 事件", err_data[:200], c)
    elif has_delta and has_done:
        ok(
            "POST /api/chat (SSE)",
            f"{sum(1 for k,_ in events if k=='delta')} deltas, {len(assistant_text)} chars",
            c,
        )
    else:
        (warn if llm_soft else fail)(
            "POST /api/chat (SSE)",
            f"events={[k for k,_ in events][:8]}  无 delta/done",
            c,
        )

    # D2 持久化 - 不论 LLM 成功与否,user 消息一定要落库
    r = await api.list_messages(conv_id)
    if r.status_code == 200:
        msgs = r.json()
        roles = [m["role"] for m in msgs]
        if "user" in roles:
            ok("D2 持久化: user 消息已落库", f"messages={len(msgs)} roles={roles}", c)
        else:
            fail("D2 持久化: user 消息未落库", f"roles={roles}", c)

        assistants = [m for m in msgs if m["role"] == "assistant"]
        if assistants:
            meta = assistants[-1].get("meta") or {}
            route = meta.get("route") or {}
            if route.get("intent"):
                ok(
                    "assistant.meta.route 已写入",
                    f"intent={route.get('intent')} personality={route.get('personality')}",
                    c,
                )
            else:
                (warn if llm_soft else fail)(
                    "assistant.meta.route 缺失", f"meta_keys={list(meta)[:8]}", c
                )
        else:
            (warn if llm_soft else fail)(
                "assistant 消息未落库", "→ 检查 ChatOrchestrator persist 路径", c
            )
    else:
        fail("GET /api/conversations/{id}/messages", f"status={r.status_code}", c)

    # /audit 应能导出
    r = await api.audit(conv_id)
    if r.status_code == 200 and r.json().get("conversation_id") == conv_id:
        ok("GET /api/conversations/{id}/audit", f"messages={len(r.json().get('messages', []))}", c)
    else:
        fail("GET /api/conversations/{id}/audit", f"status={r.status_code}", c)

    return conv_id, assistant_text


async def tier2_memory_writes(api: Api, c: Counters) -> None:
    section("Tier 2C · 记忆 API (无 LLM 路径)")

    # PATCH profile (走 correction 路径,纯本地写入)
    r = await api.client.patch(
        "/api/memory/profile",
        headers=api.auth_headers,
        json={"basic": {"name": "自测用户", "location": "北京"}, "interests": ["自测"]},
    )
    if r.status_code == 200:
        prof = r.json()
        if prof.get("basic", {}).get("name") == "自测用户":
            ok("PATCH /api/memory/profile → 写入字段", f"name={prof['basic']['name']}", c)
        else:
            fail("PATCH /api/memory/profile", f"merge 后 basic.name 不匹配: {prof.get('basic')}", c)
    else:
        fail("PATCH /api/memory/profile", f"status={r.status_code} body={r.text[:200]}", c)

    # GET profile 回读
    r = await api.client.get("/api/memory/profile", headers=api.auth_headers)
    if r.status_code == 200 and r.json() and r.json().get("basic", {}).get("name") == "自测用户":
        ok("GET /api/memory/profile (回读一致)", "", c)
    else:
        fail("GET /api/memory/profile", f"status={r.status_code} body={r.text[:200]}", c)

    # POST banned-entities → 回读
    # 注意:domain BannedEntity.entity 有 ≤ 8 字符硬校验(防 LLM 把整句当禁忌词,
    # 见 docs/rebuild/06 §6.4 + tests/unit/domain/test_correction.py),所以这里
    # 用一个短串。改长会触发 ValidationError → 当前 API 会 500(待修)。
    sentinel_entity = "测试禁词"  # 4 chars, ≤ 8
    r = await api.client.post(
        "/api/memory/banned-entities",
        headers=api.auth_headers,
        json={"entities": [sentinel_entity], "reason": "selftest"},
    )
    if r.status_code in (200, 201):
        ok("POST /api/memory/banned-entities", f"inserted={r.json().get('inserted')}", c)
    else:
        fail("POST /api/memory/banned-entities", f"status={r.status_code} body={r.text[:200]}", c)

    r = await api.client.get("/api/memory/banned-entities", headers=api.auth_headers)
    if r.status_code == 200 and any(
        b.get("entity") == sentinel_entity for b in r.json()
    ):
        ok("GET /api/memory/banned-entities (回读一致)", f"count={len(r.json())}", c)
    else:
        fail("GET /api/memory/banned-entities", f"status={r.status_code} body={r.text[:200]}", c)


async def tier2_memory_reads(api: Api, c: Counters) -> None:
    section("Tier 2D · 记忆只读端点")

    endpoints = [
        ("GET /api/memory/events",        "/api/memory/events"),
        ("GET /api/memory/episodic",      "/api/memory/episodic"),
        ("GET /api/memory/relationships", "/api/memory/relationships"),
        ("GET /api/memory/deprecations",  "/api/memory/deprecations"),
    ]
    for label, path in endpoints:
        r = await api.client.get(path, headers=api.auth_headers)
        if r.status_code == 200 and isinstance(r.json(), list):
            ok(label, f"len={len(r.json())}", c)
        else:
            fail(label, f"status={r.status_code} body={r.text[:200]}", c)


async def tier2_eval(api: Api, c: Counters) -> None:
    section("Tier 2E · 评测端点")

    r = await api.client.get("/api/eval/synthetic", headers=api.auth_headers)
    if r.status_code == 200 and isinstance(r.json(), list):
        ok("GET /api/eval/synthetic", f"case_files={len(r.json())}", c)
    else:
        fail("GET /api/eval/synthetic", f"status={r.status_code} body={r.text[:200]}", c)

    r = await api.client.get("/api/eval/chat-audit-stored", headers=api.auth_headers)
    if r.status_code == 200 and "items" in r.json():
        ok("GET /api/eval/chat-audit-stored", f"items={len(r.json()['items'])}", c)
    else:
        fail("GET /api/eval/chat-audit-stored", f"status={r.status_code}", c)


async def tier2_memory_extraction(
    api: Api, c: Counters, *, llm_soft: bool, wait_s: float = 30.0
) -> None:
    """E2E memory extraction — 依赖 celery + 真实 LLM,默认 SOFT。"""
    section("Tier 2F · 记忆抽取 E2E (依赖 celery + LLM)")

    # 先清掉前面 PATCH 写的 profile,避免误判增长
    settings = get_settings()
    engine = make_engine(settings.database_url, kind="web")
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM profiles WHERE user_id = :uid"),
                {"uid": api.user_id},
            )
    finally:
        await engine.dispose()

    conv_id = f"selftest-mem-{secrets.token_hex(4)}"
    await api.create_conversation(conv_id)

    facts = [
        "我叫张三,在上海工作。",
        "我是一名后端工程师,主要写 Python。",
        "我喜欢爬山和摄影,周末经常去户外。",
    ]
    for msg in facts:
        try:
            events, _ = await stream_sse(
                api.client,
                "/api/chat",
                headers=api.auth_headers,
                json_body={"conversation_id": conv_id, "message": msg},
                overall_timeout_s=45.0,
            )
        except Exception as e:  # noqa: BLE001
            (warn if llm_soft else fail)(
                "SSE chat (抽取触发)", f"{msg!r} → {e!r}", c
            )
            return
        if any(k == "error" for k, _ in events):
            err = next(v for k, v in events if k == "error")
            (warn if llm_soft else fail)(
                "SSE chat 报 error,跳过抽取检查", err[:200], c
            )
            return

    print(_dim(f"  …等 celery 抽取(最长 {wait_s:.0f}s)"))
    deadline = time.monotonic() + wait_s
    profile_seen = False
    episodic_seen = False
    while time.monotonic() < deadline:
        await asyncio.sleep(3)
        rp = await api.client.get("/api/memory/profile", headers=api.auth_headers)
        re_ = await api.client.get("/api/memory/episodic", headers=api.auth_headers)
        if rp.status_code == 200 and rp.json():
            profile_seen = True
        if re_.status_code == 200 and re_.json():
            episodic_seen = True
        if profile_seen or episodic_seen:
            break

    if profile_seen or episodic_seen:
        ok(
            "celery 抽取产出新记忆",
            f"profile={'有' if profile_seen else '无'} / episodic={'有' if episodic_seen else '无'}",
            c,
        )
    else:
        (warn if llm_soft else fail)(
            f"等待 {wait_s:.0f}s 后仍无 profile/episodic",
            "→ 看 docker compose logs celery,关注 'Future attached to a different loop' 与 LLM 报错",
            c,
        )


async def tier2_memory_recall(
    api: Api, c: Counters, *, llm_soft: bool
) -> None:
    """下一轮提问能否真用上已存事实(产品核心承诺)。"""
    section("Tier 2G · 记忆召回 E2E (依赖 LLM)")

    # 直接用 PATCH 注入一条已知事实 → 提问 → 看回复是否包含
    sentinel_name = f"测试昵称{secrets.token_hex(2)}"
    r = await api.client.patch(
        "/api/memory/profile",
        headers=api.auth_headers,
        json={"basic": {"name": sentinel_name}},
    )
    if r.status_code != 200:
        (warn if llm_soft else fail)("无法注入 profile 事实", f"status={r.status_code}", c)
        return

    conv_id = f"selftest-recall-{secrets.token_hex(4)}"
    await api.create_conversation(conv_id)
    try:
        _events, reply = await stream_sse(
            api.client,
            "/api/chat",
            headers=api.auth_headers,
            json_body={"conversation_id": conv_id, "message": "你还记得我叫什么名字吗?"},
            overall_timeout_s=45.0,
        )
    except Exception as e:  # noqa: BLE001
        (warn if llm_soft else fail)("召回提问失败", repr(e), c)
        return

    if sentinel_name in reply:
        ok("回复中召回了 profile.basic.name", f"sentinel={sentinel_name}", c)
    else:
        warn(
            "回复未直接包含 sentinel 名字 (LLM 措辞不确定,SOFT)",
            f"sentinel={sentinel_name}  reply[:120]={reply[:120]!r}",
            c,
        )


# ─────────────────────────────────────────────────────────── main


async def run(args: argparse.Namespace) -> int:
    counters = Counters()
    email = f"selftest+{secrets.token_hex(4)}@example.com"
    password = "SelfTest!" + secrets.token_hex(3)

    print(_cyan("MindEngine self-test"))
    print(_dim(f"  base_url   = {args.base_url}"))
    print(_dim(f"  user       = {email}"))
    print(_dim(f"  llm_soft   = {args.llm_soft}"))
    print(_dim(f"  cleanup    = {not args.keep_data}"))

    api = Api(args.base_url)
    try:
        env_ok = await tier1_environment(api, counters)
        if not env_ok:
            print()
            print(_red("Tier 1 出现 HARD 失败,后续 tier 跳过(先修环境)。"))
        else:
            auth_ok = await tier2_auth(api, counters, email, password)
            if auth_ok:
                conv_id, _reply = await tier2_chat(api, counters, llm_soft=args.llm_soft)
                _ = conv_id
                await tier2_memory_writes(api, counters)
                await tier2_memory_reads(api, counters)
                await tier2_eval(api, counters)
                if args.with_extraction:
                    await tier2_memory_extraction(
                        api, counters, llm_soft=args.llm_soft, wait_s=args.extraction_wait
                    )
                if args.with_recall:
                    await tier2_memory_recall(api, counters, llm_soft=args.llm_soft)
    finally:
        await api.aclose()

        if not args.keep_data and api.user_id:
            section("Tier 3 · 清理")
            try:
                deleted = await db_purge_user(api.user_id)
                shown = ", ".join(f"{k}={v}" for k, v in deleted.items() if v)
                ok("已删除测试用户的全部数据", shown or "无数据可删", counters)
            except Exception as e:  # noqa: BLE001
                fail("清理失败", repr(e), counters)

    # 总结
    section("汇总")
    print(
        f"  {_green(f'PASS={counters.pass_}')}  "
        f"{_yellow(f'WARN={counters.warn}')}  "
        f"{_red(f'FAIL={counters.fail}')}"
    )
    if counters.warnings:
        print()
        print(_yellow("WARN 详情:"))
        for w in counters.warnings:
            print(f"  · {w}")
    if counters.failures:
        print()
        print(_red("FAIL 详情:"))
        for f_ in counters.failures:
            print(f"  · {f_}")

    return 0 if counters.fail == 0 else 1


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m selftest.run",
        description="End-to-end self-test for the running MindEngine stack.",
    )
    p.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="backend base URL (default: %(default)s — works inside the backend container)",
    )
    p.add_argument(
        "--keep-data",
        action="store_true",
        help="保留测试用户与产生的数据 (默认跑完自动清理)",
    )
    p.add_argument(
        "--no-llm-soft",
        dest="llm_soft",
        action="store_false",
        help="LLM 相关检查改为 HARD 失败 (默认 SOFT,LLM 不稳定不影响 exit code)",
    )
    p.set_defaults(llm_soft=True)
    p.add_argument(
        "--skip-extraction",
        dest="with_extraction",
        action="store_false",
        help="跳过 Tier 2F (记忆抽取 E2E,依赖 celery + LLM,慢)",
    )
    p.set_defaults(with_extraction=True)
    p.add_argument(
        "--skip-recall",
        dest="with_recall",
        action="store_false",
        help="跳过 Tier 2G (记忆召回 E2E,依赖 LLM)",
    )
    p.set_defaults(with_recall=True)
    p.add_argument(
        "--extraction-wait",
        type=float,
        default=30.0,
        help="等 celery 抽取的最长秒数 (默认 30)",
    )
    return p.parse_args()


def main() -> int:
    args = _parse()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        raise SystemExit(main())
