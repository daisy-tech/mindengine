# MindEngine · 技术设计文档（TDD）

> 版本：rebuild · v1.1 · **MindEngine**
> 与 [01-PRD.md](./01-PRD.md) 对位
> 颗粒度：**架构级**——讲清楚边界、数据流、关键决策。具体实现交给重构者发挥。

> 🔄 **修订 v1.1（2026-06）**本次涉及本文档：
> - §1.1 / §8：infra 栈改为 **Postgres + pgvector + Redis**（去 SQLite/Mem0/Qdrant，决策 D1）。
> - §2.1 / §9：**流式持久化与客户端连接解耦**——只要 ≥1 token 产出必落带 meta 的 message（决策 D2，修复断连丢审计漏洞）。
> - §3.1：重写"为什么 Postgres"。
> - §7：`DEV_MODE` 默认 `false` + dev 端点硬开关（决策 D6）。

---

## 1. 总体架构

### 1.1 分层

```
┌─────────────────────────────────────────────────────┐
│  Frontend (Vue 3 + Element Plus + Pinia, 不动)        │
└─────────────────────────────────────────────────────┘
                         │ HTTP / SSE (POST)
                         ▼
┌─────────────────────────────────────────────────────┐
│  API Layer (FastAPI)                                 │
│  - 薄路由，只做参数校验/鉴权/流式封装                  │
│  - 不直接调 LLM、不直接读写记忆                       │
└─────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│  Service Layer (纯逻辑，最大头)                       │
│  ├─ memory_router/    （Layer 1+2+3）                │
│  ├─ memory_context/   （根据 route 装填记忆）         │
│  ├─ prompt_composer/  （base + intent + 契约 + 渲染）│
│  ├─ chat_orchestrator/（编排上面三个 + 流式调 LLM）   │
│  ├─ correction/       （纠错管线）                    │
│  ├─ eval_chat_review/ （L0/L1/归因）                  │
│  └─ eval_synthetic/   （合成评测）                    │
└─────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│  Infrastructure (可替换适配层)                        │
│  ├─ llm/    OpenAI 兼容 / Anthropic / 本地           │
│  ├─ vector/ pgvector (episodic)  🔄 v1.1            │
│  ├─ db/     SQLAlchemy + Alembic (Postgres) 🔄      │
│  └─ queue/  Celery / arq                             │
└─────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│  Workers (Celery / arq)                              │
│  - extract_memory / extract_profile / extract_event  │
│  - correction_cleanup                                │
│  - 不直接被 API 调用，由 chat_orchestrator 发任务     │
└─────────────────────────────────────────────────────┘
```

### 1.2 模块边界（**这条 v0.97 没做好，重写必须做对**）

| 层 | 可以引用 | 不可以引用 |
|---|---|---|
| `api/` | `services/`、`domain/` | `infra/` 直接、`workers/` 直接 |
| `services/` | `domain/`、其它 `services/` | `api/`、SQLAlchemy 模型、HTTP/SSE |
| `domain/` | 纯类型，仅 stdlib + Pydantic | 任何 IO、任何 ORM |
| `infra/` | `domain/` | `services/`、`api/` |
| `workers/` | `services/`、`infra/` | `api/` |

**核心收益**：80% 的代码（services + domain）可以**完全在内存里跑 pytest**，不需要起 Postgres / Redis。

---

## 2. 一次请求的数据流

### 2.1 Chat 流式请求（最复杂的一条）

```
1. POST /api/chat/stream
   { conversation_id, message, personality, history? }

2. API 层校验 + 鉴权 → 调 chat_orchestrator.stream(...)

3. chat_orchestrator:
   3.1 memory_router.route(message, personality, history)
       → MemoryRoute { intent, depth, load_layers, ... }
       Layer 1：硬规则（regex / 关键词，命中即返回）
       Layer 2：小模型 intent 分类（`INTENT` 模型 = qwen3.7-plus，JSON 输出）
       Layer 3：策略查表（intent → depth / max_explicit / sensitive_mode）

   3.2 memory_context.load(user_id, route)
       → MemoryContext { stable_profile, relevant_*, background_only }
       并发拉四层（均在 Postgres）：profile / event / relationship (SQL)
                  / episodic (pgvector 近邻检索)        🔄 v1.1
       过滤 banned_entities + deprecated（status='active'）
       为每条记忆打 usage 标签（EXPLICIT_OK / BACKGROUND_ONLY / ...）

   3.3 prompt_composer.compose(memory_context, personality)
       → PromptPack { system, meta }
       渲染 BASE_PERSONA + 时间 + 各记忆段 + 本轮规则
            + INTENT_GUIDES[intent] + PERSONALITY_CONTRACT[personality]
            + HARD_RULES
       空段不渲染，避免"（无）"占位噪音

   3.4 llm.stream(system, history+message, model=qwen3.7-max,
                  enable_thinking=False)
       → 实时 yield delta token

4. API 层 wrap 成 SSE event，吐给前端

5. 持久化（🔄 v1.1：与客户端连接解耦，见 §2.3）：
   5.1 把 reply（哪怕是 partial）持久化到 Message 表
   5.2 把 PromptPack.meta 序列化到 Message.meta_json (TEXT 字段)
       —— 关键：这一步必须在 finally / asyncio.shield 中执行，
          客户端断连也要落库，否则真实聊天评估失去基础

6. dispatch Celery task（同样在 shield 中，不依赖客户端是否还在）：
   6.1 if intent != "correction":
       - extract_memory_task  (写 episodic / pgvector)
       - extract_profile_task (LLM 抽取 + 合并到 Postgres)
       - extract_event_task   (LLM 抽取 + Postgres)
   6.2 if intent == "correction":
       - correction_cleanup_task（管线见 §6.6）
```

### 2.3 🔄 流式持久化的连接解耦（v1.1 · 决策 D2）

**问题（修复前）**：`chat_orchestrator.stream` 是 async generator，"流结束后落库"写在生成器尾部。若用户**中途关页面 / 断网**，生成器被取消/GC，落库与 dispatch 都不执行——这条 turn 的 `prompt_meta` 永久丢失，而它是评测体系的基石（坑外新增，比坑 7.3 "LLM 报错"更隐蔽）。

**不变式**：**只要 LLM 产出 ≥ 1 token，就必须有一条带 `meta_json` 的 message 落库**（成功=完整 reply；中断=partial reply + error 字段）。

**实现要点**：

```python
async def stream(self, req) -> AsyncIterator[ChatEvent]:
    route = await self.memory_router.route(...)
    ctx   = await self.memory_context.load(...)
    pack  = self.prompt_composer.compose(ctx, ...)
    partial: list[str] = []
    err: str | None = None
    try:
        async for tok in self.llm.stream(pack.system, ...):
            partial.append(tok)
            yield ChatEvent.delta(tok)
    except (asyncio.CancelledError, Exception) as e:
        err = type(e).__name__ if isinstance(e, Exception) else "client_disconnect"
        # 不吞 CancelledError 的取消语义，但要保证 finally 落库
        raise
    finally:
        reply = "".join(partial)
        if reply or err:
            # shield：即便外层任务被取消，落库与 dispatch 也跑完
            await asyncio.shield(self._persist_and_dispatch(req, route, pack, reply, err))
    yield ChatEvent.final(reply, pack.meta)
```

- `_persist_and_dispatch` 内部用 **worker engine / 独立 session**（不复用可能已随请求关闭的 session）。
- dispatch 用 fire-and-forget 语义（不让用户等任务入队）。
- 测试：mock LLM 产出 3 token 后抛 `CancelledError` → 断言 message 落库且 `error` 非空、`meta_json` 完整。

### 2.2 真实聊天评估请求（典型读路径）

```
1. GET /api/eval/chat-audit/{conv_id}?force=false

2. eval_chat_store.load_review(user_id, conv_id)
   → 命中磁盘缓存 → 直接返回（< 50ms）

3. 未命中：
   3.1 chat_audit.build_audit_pack(conversation)
       → chat_audit_v1 (含每轮 prompt_meta + 派生统计)
   3.2 eval_chat_review.review_audit_pack(pack)
       → 加每轮 review { l0/l1/final_status }
   3.3 eval_chat_store.save_review(user_id, conv_id, pack)
       chmod 0644 让 NFS 跨用户可读
   3.4 返回带 review 的 pack
```

---

## 3. 关键决策

### 3.1 🔄 为什么 Postgres + pgvector（v1.1 决策 D1，取代原 SQLite + Mem0）

**当前选择**：四层记忆 + 会话/消息/纠错 全在 **Postgres**；episodic 向量用 **pgvector**（同库 `vector` 列）。

**理由**：
- 本项目是**多账号、低并发、单机 docker compose**（PRD：邮箱注册、账号间记忆不共享，但存在多账号）。Postgres 在此前提下成本≈0——你已经要跑 Redis，再加一个 pg 容器就是几行 compose。
- 一次性消除 4 个 legacy 坑：
  - 坑 1.2（web/celery 共用 SQLAlchemy engine → `database is locked`）：Postgres 原生并发连接。
  - 坑 9.2（SQLite on NFS 文件锁不稳）：Postgres 无文件锁问题。
  - 坑 9.3（同用户 extract 抢锁需 redis_lock）：行级锁/MVCC，**可去掉 redis_lock**。
  - 坑 10.3（测试内存 SQLite vs 生产 SQLite dialect 差异）：测试用 testcontainers 起同款 pg，dialect 一致。
- pgvector 让四层记忆 + 向量**同库同事务**：episodic 软删/banned 过滤与关系数据在一个查询/事务里完成，省一个有状态服务、省一份备份。
- "v2 易迁 Postgres"从口号变为现实（本就是 Postgres）。

**为什么不再用 Mem0**：legacy 关掉 `infer`（坑 4.2）后 Mem0 ≈ "embed+存+召回"，自身智能全关却背依赖与版本漂移。事实抽取在 service 层做完，向量层只需存+近邻。详见 [08-Data-Model.md](./08-Data-Model.md) §3。

**engine 分离仍保留**（但动机变了）：不再是绕 SQLite 锁，而是**连接池隔离 + 故障隔离**——
- API 进程一个 engine（pool 大）；Celery worker 一个 engine（pool 小）。
- 通过 `infra/db/factory.py` 按 `RUNTIME_KIND` 构造（见 [10-Lessons-Learned.md](./10-Lessons-Learned.md) §1.2）。

### 3.2 为什么 SSE 用 POST 不用 EventSource？

**问题**：EventSource 是 GET，URL 长度有限。当 history 很长时，把 history 塞进 URL 会被网关截断。

**解法**：用 `fetch` POST + `ReadableStream` 自己解析 SSE 帧。

**反思**：v0.97 踩坑后才改的。新版直接走 POST + SSE，不要再去试 EventSource。

### 3.3 为什么 chat handler 不直接发 Celery 任务，而是要走 orchestrator？

**问题**：v0.97 直接在 router 函数里 `task.delay(...)`，耦合死了：
- 想加"correction 时跳过 extract" 这种逻辑要在 router 里写 if
- 想换队列实现（Celery → arq）要改一堆 router

**解法**：chat_orchestrator 提供唯一入口：

```python
class ChatOrchestrator:
    def __init__(self, llm, memory_router, memory_context, prompt_composer,
                 message_writer, task_dispatcher):
        ...

    async def stream(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        route = await self.memory_router.route(...)
        ctx   = await self.memory_context.load(...)
        pack  = self.prompt_composer.compose(ctx, ...)
        async for token in self.llm.stream(pack.system, ...):
            yield ChatEvent.delta(token)
        # 序列化阶段
        msg = await self.message_writer.persist(reply, pack.meta)
        # 异步分发
        await self.task_dispatcher.dispatch_after_chat(msg, route)
        yield ChatEvent.final(reply, pack.meta)
```

router 函数只剩：

```python
@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, orch: ChatOrchestrator = Depends()):
    return EventSourceResponse(orch.stream(req))
```

### 3.4 为什么 prompt_meta 落到 Message.meta_json 而不是单独表？

**当前选择**：序列化为 JSON 文本存到 Message.meta_json (TEXT)。

**理由**：
- 1:1 关系，单独表没收益
- prompt_meta schema 频繁演化，单独表的列爆炸

**警示**：v0.97 用嵌套字典 + 各种 `.get()` 读取，导致：
- 写代码时不知道字段是不是真的存在
- 评估代码 grep 字符串硬编码字段名

**新版要改**：
- 定义清晰的 Pydantic 类型 `PromptMeta`
- 序列化用 `model.model_dump_json()`
- 读取用 `PromptMeta.model_validate_json(meta_json)`
- 字段演化用版本号 `prompt_meta.schema_version`

### 3.5 为什么 personality contract 注入所有 intent（包括 correction / emotional_support）？

**v0.97 教训**：原本设计"sensitive 场景下人格主动性被覆盖" → 在敏感 intent 下不注入契约 → 结果三种人格在敏感场景几乎一模一样，**根本没区分度**。

**新版决策**：
- knowledge_task 不注入（与人格无关）
- 其他所有 intent 都注入
- 契约本身含 4 个子分支（普通 / 质问记忆 / 纠错 / 情绪），每个子分支单独写"敏感场景下的差异化"

详见 [04-Subsystem-Personality.md](./04-Subsystem-Personality.md)

### 3.6 为什么 correction 必须跳过 extract_*？

**问题**：用户说"不是怀宁，是岳西" → 抽取任务可能把"怀宁"也抽进去 → 越纠越错。

**解法**：chat_orchestrator 看到 `route.intent == "correction"` → 直接跳过所有 extract 任务，只跑 correction_cleanup。

### 3.7 为什么 Memory Router 的 query 只用 last 1 user message？

**v0.97 教训**：原本拼 3-5 轮 user 历史去 Mem0 检索 → 语义被旁支稀释 → "我家养了什么"召不回"养了一只猫"（前几轮聊了别的）。

**新版决策**：
- query = 当前消息（去掉表情、问候词）
- 旁支推断（如"上下文提到孩子" → 该召回 relationship.son）由 LLM 在 prompt 层完成
- 检索阶段只信"当下信号"

### 3.8 为什么 LLM 要分两档（max vs plus）？

| 用途 | 模型 | 理由 |
|---|---|---|
| 主聊（chat） | qwen3.7-max | 用户感知的回复质量 |
| Intent / Extract / Correction | qwen3.7-plus | 结构化任务 + 高频调用，成本 1/3 |

新版结构允许后续平替：
- 主聊换 Claude 4.6 sonnet
- 结构化任务换 GPT-5 mini

通过 `llm.client(role="chat" | "structured")` 抽象。

### 3.9 为什么落盘文件要 chmod 0644？

**问题**：Docker 容器内 root 写出的文件 NFS 挂载到 mac 端默认 0600，非 root 用户读不到（连 sudo 都被 NFS 协议挡）。

**解法**：写盘后立刻 `os.chmod(path, 0o644)`，让任何用户能读。

**推广**：所有可能跨用户读的文件（log / report / export）都要主动 chmod。

---

## 4. 模块清单

| 模块 | 职责 | 依赖 | 测试要求 |
|---|---|---|---|
| `domain/memory.py` | 四层记忆 DTO（Profile / Event / Episodic / Relationship） | stdlib | 无 |
| `domain/route.py` | MemoryRoute / RoutedMemory / MemoryUsage | stdlib | 无 |
| `domain/prompt.py` | PromptPack / PromptMeta | stdlib | 无 |
| `services/memory_router/intent_classifier.py` | 小模型 intent 分类 | llm | mock llm 单测 |
| `services/memory_router/rules.py` | 硬规则识别 correction / knowledge_task | 无 | 纯单测 |
| `services/memory_router/router.py` | 三层融合，输出 MemoryRoute | rules + classifier | mock + 单测 |
| `services/memory_context/loader.py` | 按 route 拉四层并打 usage 标签 | infra.db + infra.vector | 集成测 |
| `services/memory_context/filter.py` | 过滤 banned / deprecated | 无 | 纯单测 |
| `services/prompt_composer/base.py` | BASE_PERSONA / HARD_RULES 常量 | 无 | 纯单测 |
| `services/prompt_composer/intent_guides.py` | 9 类 intent guide 常量 | 无 | 纯单测 |
| `services/prompt_composer/personality.py` | PERSONALITY_CONTRACT 常量 | 无 | 纯单测 |
| `services/prompt_composer/composer.py` | 组装 system prompt | 上 3 个 | 黄金样本对比 |
| `services/chat_orchestrator.py` | 编排全流程 | 上面所有 | mock llm 单测 |
| `services/correction/extractor.py` | 抽取纠错目标 | llm | mock 单测 |
| `services/correction/judge.py` | 小模型判断 action / banned_entities | llm | mock 单测 |
| `services/correction/applier.py` | 落 deprecation / banned 表 | infra.db | 集成测 |
| `services/eval_chat_review/l0.py` | 结构自检规则 | 无 | 纯单测 |
| `services/eval_chat_review/l1.py` | 启发式规则（11+ 条） | 无 | 纯单测 |
| `services/eval_chat_review/reviewer.py` | 编排 L0 + L1 + 归因 + final_status | 上 2 个 | 纯单测 |
| `services/eval_chat_review/store.py` | 落盘 / 读盘 / 列表 / 删除 | infra.fs | 临时目录集成测 |
| `services/eval_synthetic/runner.py` | 跑合成 case | chat_orchestrator | mock llm |
| `infra/llm/client.py` | OpenAI 兼容 client 抽象 | httpx | mock httpx |
| `infra/vector/pgvector_repo.py` | 🔄 v1.1：episodic add/search/soft_delete（pgvector） | sqlalchemy + pgvector | 集成测（testcontainers pg） |
| `infra/db/models.py` | SQLAlchemy 模型（Postgres） | sqlalchemy | 无 |
| `infra/db/repositories.py` | Repository 模式封装 | sqlalchemy | 集成测 |
| `workers/extract.py` | extract_memory/profile/event 任务 | services | 集成测 |
| `workers/correction.py` | correction_cleanup 任务 | services.correction | 集成测 |

---

## 5. 接口规约（按资源/动作列，不锁死路径）

> API loose 模式，新版可以重设具体路径。这里列资源+动作让重构者发挥。

| 资源 | 动作 | 备注 |
|---|---|---|
| Auth | login / logout / refresh | JWT |
| User | get_profile / update_profile / delete_account | |
| Conversation | list / get / create / delete / export_audit | |
| Message | list_by_conversation | |
| Chat | stream（POST + SSE） / non_stream | |
| Memory | list_profile / list_events / list_episodic / list_relationships / delete_episodic | |
| Memory | list_deprecations / list_banned_entities | |
| Personality | get / set | |
| Eval | list_synthetic_runs / get_synthetic_run / start_synthetic_run / single_query / drafts | |
| Eval | chat_audit (GET, force=bool) / list_stored / delete_stored | |
| Eval | seed_persona / get_persona_data | DEV_MODE only |

---

## 6. 数据流图（关键场景）

### 6.1 普通 casual 一轮

```
user "今天天气真好"
  → router (硬规则不命中 → 小模型 → "casual" / 0.92)
  → context loader (profile_basic + episodic top3)
  → composer (BASE + 时间 + profile + episodic + 本轮 + casual guide + 中性契约 + HARD)
  → llm.stream → "周日上午阳光确实暖。你今天有什么打算吗？"
  → message.persist + prompt_meta
  → dispatch [extract_memory, extract_profile, extract_event]
```

### 6.2 memory_challenge 一轮

```
user "你记得我喜欢什么茶吗"
  → router (硬规则不命中 → 小模型 → "memory_challenge" / 0.96)
  → context loader (profile + relationships + events + episodic top10)
    - 装填后看池子里有"在家泡茶"，但没"具体茶种类"
  → composer (...含 memory_challenge guide + 中性契约 + "禁止幻觉"段)
  → llm.stream → "我能想到的是你在家泡茶，但具体哪种还没记下。是常喝的那种吗？"
  → message.persist
  → dispatch extract_*
```

### 6.3 correction 一轮

```
user "错 是怀宁，邻近安徽岳西"
  → router (硬规则命中 "错/不是/记错了" → "correction" / 1.0)
  → context loader (focused 模式，只拉与"岳西/怀宁"相关的少量记忆)
  → composer (...含 correction guide：第一人称承认 + 复述事实)
  → llm.stream → "我记错了，老家是怀宁，邻近岳西，按这个来。"
  → message.persist
  → dispatch ONLY [correction_cleanup_task]  ← 注意：不发 extract_*
```

correction_cleanup_task 流程见 [06-Subsystem-Correction.md](./06-Subsystem-Correction.md) §3。

---

## 7. 配置与环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `CHAT_MODEL` | `qwen3.7-max` | 主聊模型 |
| `INTENT_MODEL` | `qwen3.7-plus` | intent 分类 |
| `EXTRACT_MODEL` | `qwen3.7-plus` | 抽取记忆 |
| `CORRECTION_MODEL` | `qwen3.7-plus` | 纠错判断 |
| `ENABLE_THINKING` | `false` | Qwen3 必须 false |
| `OPENAI_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | |
| `OPENAI_API_KEY` | - | DashScope API key |
| `INTENT_MODEL_TEMPERATURE` | `0` | 分类要稳定 |
| `INTENT_CLASSIFIER_ENABLED` | `true` | 关掉则只剩硬规则 |
| `EVAL_CHAT_REVIEWS_DIR` | `/app/eval/exports/reviews` | 落盘根 |
| `EVAL_PASS_THRESHOLD` | `0.85` | 合成评测通过线 |
| `DEV_MODE` | 🔄 `false` | **默认关**；dev 端点（seed_persona 等）另需启动期硬开关，见 §7 下方说明 |
| `PROMPT_TIMEZONE` | `Asia/Shanghai` | 时间渲染 |
| `JWT_SECRET` | dev 兜底 | 生产必须显式给 |
| `APP_TITLE` | `MindEngine API` | OpenAPI 文档标题（legacy 为 MemoBot API，勿沿用） |
| `DATABASE_URL` | 🔄 `postgresql+asyncpg://...` | Postgres 连接串（v1.1 取代 SQLite 路径） |
| `RUNTIME_KIND` | `web` / `worker` | engine 池大小与隔离（见 §3.1） |
| `EMBEDDING_MODEL` | `text-embedding-v3` | pgvector 写入/检索用，dim=1024 |
| `INTENT_CACHE_ENABLED` | 🔄 `true` | intent 确定性可安全缓存（决策 D4） |

> 🔄 **修订 v1.1 · dev 端点防误开（决策 D6）**：`DEV_MODE=true` 仅放开"只读"调试。涉及**重置/灌库**的端点（`seed_persona` / `debug/replay` 等会改数据）必须**额外**满足启动期硬开关（如独立 env `ALLOW_DESTRUCTIVE_DEV=1` 且仅当 `DATABASE_URL` 指向非生产库），并在路由注册时按 allowlist 挂载——避免单个 env 误配就能清空用户四层记忆。

---

## 8. 部署架构

```
┌────────────────────────────────────────┐
│  ECS host (Aliyun)                      │
│                                          │
│  ┌─────────────┐  ┌──────────────────┐ │
│  │  backend    │  │  frontend (nginx)│ │
│  │  (FastAPI)  │  │                  │ │
│  └─────────────┘  └──────────────────┘ │
│         │                                │
│  ┌─────────────┐  ┌──────────────────┐ │
│  │   celery    │  │  celery-beat     │ │
│  │   worker    │  │  (定时任务，可选) │ │
│  └─────────────┘  └──────────────────┘ │
│         │                                │
│  ┌─────────────┐  ┌──────────────────┐ │
│  │   redis     │  │   postgres       │ │ 🔄 v1.1
│  │  (queue)    │  │  (+ pgvector)    │ │
│  └─────────────┘  └──────────────────┘ │
│                                          │
│  /app/eval/exports/ ← NFS shared        │
│  pgdata volume (Postgres 数据)          │ 🔄 v1.1
└────────────────────────────────────────┘
```

**说明**：
- 全部容器化（docker compose），一台 ECS 即可跑
- backend / celery / celery-beat 共用一份 image（同一份 `backend/` 代码）
- redis 用官方 image；Postgres 用 `pgvector/pgvector:pg16`（自带 pgvector 扩展）
- 持久化：`pgdata` volume（含关系数据 + episodic 向量），不放 NFS（坑 9.2 不再适用，但 db volume 仍用本地块存储更稳）
- 🔄 v1.1：相比 legacy 少一个 Qdrant 服务、少一份独立向量备份
- 启动顺序：backend/celery `depends_on` postgres + redis 的 `service_healthy`（坑 9.1）

---

## 9. 关键不变式（重构验收用）

> 这些是 v0.97 自测脚本（112 个 case）抽出来的核心不变式。新版必须全部成立。

1. **Memory Router**：硬规则命中即返回，不再走小模型
2. **Memory Router**：correction / knowledge_task 在所有 personality 下都正确路由
3. **Memory Context**：banned_entities 命中的记忆**不进**任何池子
4. **Memory Context**：deprecated_ids 命中的 episodic**不进**池子
5. **Prompt Composer**：空段不渲染"（无）"占位
6. **Prompt Composer**：personality contract 仅 knowledge_task 不注入，其他 intent 都注入
7. **Prompt Composer**：BASE / TONE / HARD_RULES 不出现矛盾指令（如"1-3 句"vs"2-4 句"）
8. **Chat handler**：correction intent 下不发 extract_* 任务
9. **Correction**：低置信度 → audit_only（仅审计不动数据）
10. **Correction**：banned_entities 持久化去空白、去重
11. **Eval store**：落盘文件权限 0644
12. **Eval store**：路径穿越被挡（"../" 在 id 段里）
13. **Eval review**：L0 warn 但 L1 全过 → final_status = ok（不是 suspicious）
14. **Eval review**：personality_signature 仅 knowledge_task 跳过
15. 🔄 **Chat 持久化（v1.1）**：LLM 产出 ≥1 token → 必有一条带 `meta_json` 的 message 落库；客户端断连不丢（§2.3）
16. 🔄 **数据隔离（v1.1）**：任一 Repository 缺 user_id 即 raise；user B 检索四层 + episodic 向量 → 0 命中 user A 数据（[08-Data-Model.md](./08-Data-Model.md) §8）
17. 🔄 **人格服从兜底（v1.1）**：reply 超人格契约字数上限 → 后置硬截断生效（[04-Subsystem-Personality.md](./04-Subsystem-Personality.md) §9.4）

详见 [10-Lessons-Learned.md](./10-Lessons-Learned.md) 后半部分。

---

## 10. 何时算"重构完成"

| 阶段 | 完成标志 |
|---|---|
| M1（核心通） | 三种人格各跑一轮 casual，prompt_meta 字段完整、人格契约渲染正确 |
| M2（记忆通） | smoke 20 通过率 ≥ 80%；自测前 6 节通过 |
| M3（纠错通） | correction case 端到端跑通；自测 7-9 节通过 |
| M4（评估通） | 真实聊天评估能跑出与 legacy MindMem v0.97 字段对齐的报告；自测全过 |
| M5（前端通） | 现前端 5 个 Tab 都能正常工作（API 适配层完成） |

详见 [11-Roadmap.md](./11-Roadmap.md)。
