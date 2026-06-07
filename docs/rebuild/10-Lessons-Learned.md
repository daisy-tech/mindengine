# 踩坑清单 · legacy MindMem v0.97 → MindEngine 必须避开的 30 个坑

> 责任：把 legacy MindMem v0.97 实际遇到的所有真实问题列出来。MindEngine 重构者实现每个模块前，**对应章节必读**。
> 优先级：🔥 = 大坑、必修；⚠️ = 中坑、强烈建议修；💡 = 小坑、可借鉴。

---

## 1. 架构与模块边界

### 🔥 1.1 services 层耦合 ORM / Celery / FastAPI Request

**症状**：测试要起整套 docker-compose（Redis / Qdrant / 后端）才能跑。

**v0.97 现状**：
- `chat_orchestrator` 直接调 `SessionLocal`、`Message(...).save()`
- `memory_router` 直接 import `celery_worker.tasks` 准备发任务
- service 函数签名带 `request: Request`

**新版做法**：
- service 层只看 `Protocol`（Repository / TaskDispatcher / LLMClient）
- ORM Session 注入而非全局
- service 函数不接 FastAPI Request，只接 Pydantic DTO

**收益**：80% 代码可在内存里 pytest，不依赖任何容器。

---

### 🔥 1.2 同一 SQLAlchemy engine 被 Web 和 Celery 共用

**症状**：偶发 `database is locked` / 写入丢失。

**v0.97 现状**：`backend/app/db.py` 单例 engine，FastAPI worker 和 Celery worker 都用它。

**新版做法**：
- Web 进程一个 engine（pool_size=10）
- Celery worker 一个 engine（pool_size=2，按队列分）
- 通过 `infra/db/factory.py` 按 `RUNTIME_KIND` env var 决定怎么构造

```python
def make_engine(kind: Literal["web","worker"]):
    if kind == "web":
        return create_engine(DB_URL, pool_size=10, pool_recycle=300)
    return create_engine(DB_URL, pool_size=2, pool_recycle=60)
```

---

### ⚠️ 1.3 Router 函数胖

**症状**：`backend/app/routers/chat.py` 单文件 800+ 行，混杂业务、SSE、序列化、错误处理。

**新版做法**：Router 不超过 50 行/函数，业务全调 `ChatOrchestrator`。

```python
# 好
@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, orch=Depends(get_chat_orchestrator)):
    return EventSourceResponse(orch.stream(req))

# v0.97 坏
@router.post("/chat/stream")
async def chat_stream(req, db=Depends(get_db), ...):
    user = db.query(User).filter_by(id=req.user_id).first()
    if not user: raise ...
    # 200 行业务...
    # 100 行 LLM 调用...
    # 80 行 SSE 序列化...
```

---

### 💡 1.4 scripts/ 混在 backend/ 部署被一起打包

**v0.97 现状**：`backend/scripts/` 里有 eval / seed / fix 脚本，会被 docker COPY 进去。

**新版做法**：脚本独立 `tools/` 目录在仓库根；Dockerfile 不 COPY tools。

---

## 2. Memory Router

### 🔥 2.1 Query 拼了太多用户历史导致召回稀释

**症状**：用户问"我家养了什么"，episodic 池里有"养了一只猫"，召不回。

**v0.97 现状**：`_build_query` 拼最近 3-5 轮 user message → 当前消息被旁支稀释。

**修复**：query = 当前消息 only（必要时拼上一句）；旁支推断交给 LLM 看 prompt 时做。

---

### ⚠️ 2.2 Intent 分类没显式 fallback

**症状**：classifier 抛 timeout → 整个 chat 流崩。

**修复**：

```python
try:
    cls = await classifier.classify(...)
except (TimeoutError, ValueError):
    cls = ClassifyResult(intent="casual", confidence=0.0)
```

兜底必须有，且记到 prompt_meta.route.reasons。

---

### ⚠️ 2.3 硬规则与小模型谁先没明确

**v0.97 现状**：初版混用，后才修。

**新版**：**硬规则一定先**。命中即返回，**绝不再调** classifier。理由：硬规则是为了"小模型经常错的典型句"准备的，跑到小模型就破功。

---

### 💡 2.4 intent 字符串散落各处

**v0.97 现状**：`"correction"` 字符串在 8+ 个文件出现，改名要全局替换。

**新版**：

```python
class Intent(str, Enum):
    CASUAL = "casual"
    CORRECTION = "correction"
    ...
```

只在 `domain/route.py` 定义。其他地方 import。

---

## 3. Prompt Composer

### 🔥 3.1 BASE / TONE / CONTRACT 三处定义字数和句数，互相打架

**症状**：外向人格输出 1-3 句（BASE 的限制赢了），人格契约的 2-4 句失效。

**根因**：BASE_PERSONA 写过 "单句≤30字；1-3 句"，PERSONALITY_TONE 又写了一遍，PERSONALITY_CONTRACT 又写了第三遍。LLM 会取最严的。

**修复（v1.2.2 已做）**：
- 字数 / 句数 / 反问 / 引用 → **唯一归属契约**
- BASE 只留人设和禁用句式
- 删除 PERSONALITY_TONE 中间层

**重写后必须**：grep 一遍 prompt 文件，凡含数字 + 字 / 句 的硬指标，全在契约里。

---

### 🔥 3.2 敏感场景关掉了人格契约

**症状**：emotional_support / correction / memory_challenge 下三种人格回复几乎一样。

**v0.97 现状**：`if not route.sensitive_mode and intent in PERSONALITY_CONTRACT_INTENTS: inject(...)`

**修复**：删掉 `not route.sensitive_mode` 条件。让契约自己在子分支里处理敏感场景的差异化。

---

### ⚠️ 3.3 空段渲染成 "（无）" 误导 LLM

**症状**：profile 里没数据时 prompt 出现"【你大概知道用户】：（无）" → LLM 反而觉得"我应该说我不知道你"。

**修复**：空段直接不渲染整块。

---

### ⚠️ 3.4 系统 prompt 全文存到 Message.meta，DB 膨胀

**症状**：100 轮对话后 messages 表 100MB+。

**修复**：
- meta 只存 `system_excerpt`（前 500 字）+ section keys
- 全文 prompt 要看时走单独 "重组" 接口（按 prompt_meta 重新 compose）

---

### ⚠️ 3.5 L0 检查靠字符串匹配 prompt 段标题

**症状**：prompt 段标题改了一个字 → 所有 L0 false positive。

**v0.97 修复**：维护 `_SECTION_HEADERS` 和 `_SECTION_HEADERS_LEGACY` 两套常量（丑）。

**新版做法**：composer 输出结构化 `PromptPack.sections: dict`，L0 直接看 `sections.explicit is not None`。

---

### 💡 3.6 INTENT_GUIDES 和 CONTRACT 之间也会冲突

**症状**：`casual` guide 写了"可以反问"，contract 内向写"不反问" → 矛盾。

**修复**：
- guide 只写"该接什么话题、什么风格"
- 字数/反问/引用全归契约
- guide 末尾如有冲突可能，加一句 "反问和引用记忆按本轮人格契约执行"

---

## 4. 四层记忆

### 🔥 4.1 Profile 字段类型在多次抽取中漂移

**症状**：`occupation` 第 1 次抽是 `str`（"产品经理"），第 2 次是 `dict`（{"title":"PM","level":"senior"}），第 3 次 `list`。

**根因**：LLM 抽取没强 schema，后端没 schema_version。

**修复**：
- ProfileField 用 Pydantic 强约束
- 不在白名单内的字段全进 `extra` dict
- profile 整体带 `schema_version`，migration 函数化

---

### 🔥 4.2 Episodic 抽取时 `infer=True` 让 Mem0 二次"理解"

**症状**：明明抽出来是"用户儿子叫小李"，Mem0 入库变成"用户家里有男性成员"——稀释。

**修复**：调用 `mem0.add(text, user_id, infer=False)`。事实抽取在 service 层做完，Mem0 只负责存储和召回。

---

### ⚠️ 4.3 Relationship via 指向自己

**症状**：SocialGraph 前端渲染崩（自环）。

**修复**：DB CHECK constraint + Domain validator。

---

### ⚠️ 4.4 Episodic 召回数 = limit，过滤后剩几条

**症状**：limit=5，过滤掉 deprecated 后剩 2 条，显得 AI 没记忆。

**修复**：search 时取 `limit * 2`，留出过滤余量。

---

### ⚠️ 4.5 banned_entities 在 read 端过滤但不在 write 端过滤

**症状**：用户纠错后立刻又被异步抽取重新写入"岳西"。

**修复**：
- write 端（extract_*）：调 add 前必须过 banned 检查
- read 端（memory_context.load）：取出后必须过 banned 检查

两端都要，否则总有种情况漏。

---

## 5. 在线纠错

### 🔥 5.1 correction 同轮还发了 extract_*

**症状**：用户"不是怀宁是岳西" → 道歉同时 extract 把"岳西" 又写进去。

**修复**：chat_orchestrator 看 intent==correction → 跳过 extract dispatch。

---

### ⚠️ 5.2 LLM 判断 banned 时把整句当 entity

**症状**：用户说"我老家不是河南信阳"，banned 写入"我老家不是河南信阳"。

**修复**：
- extract_banned 输出 JSON 时强约束 entity 字段 ≤ 8 字
- 后端再清洗一遍长度

---

### ⚠️ 5.3 LLM 判断 confidence 没用阈值

**症状**：低置信度判断也直接落库 → 误删用户其实没要删的记忆。

**修复**：confidence < 0.7 → action=audit_only（只记录不动数据）。

---

## 6. 评估

### 🔥 6.1 personality_signature 看 activated 数而非 reply 引用数

**症状**：内向人格被打 fail，但实际 reply 短得没引用任何记忆——是 activated 池子大让 L1 误判。

**根因**：rule 数了 `prompt_meta.activated`（备弹），不是"reply 实际引用"。

**修复**：
- 数 reply 文本里出现的人名 / 地名（用 banned + profile.basic + relationships.name 作字典）
- 或：用"reply 字数 - 用户消息字数 > 阈值 + intent 不在白名单"近似

---

### 🔥 6.2 reply_off_topic 对短事实回应误报

**症状**：用户问"我老家在哪"，AI 答"湖南怀化"——很对，但 rule 算字符 2-gram 重叠率 0% → 误报 suspicious。

**修复**：
- 白名单 intent（memory_challenge / correction / preference_request）下，rule 放宽
- 或加额外条件：reply 字数 < 15 且包含命名实体 → 跳过

---

### ⚠️ 6.3 fabrication_under_challenge 漏报

**症状**：AI 编造"安徽岳西" 是用户老家，rule 没抓到。

**根因**：rule 只检查 reply 里出现的实体是不是在 activated 池——但 LLM 编的实体不在 banned 里、不在 profile 里，rule 没字典。

**修复**：
- 建实体字典：profile.basic + relationships.name + 最近 50 条 episodic 的 NER
- reply 里出现具体地名/人名 → 必须在字典里，否则 suspicious

---

### ⚠️ 6.4 final_status 太严：medium fail → suspicious

**症状**：L0 medium warn（如 "explicit 段轻微缺失"）+ L1 全过 → 仍然 suspicious，假阴性多。

**修复**：medium fail + L1 全过 → final=ok。

---

### ⚠️ 6.5 评测落盘文件 0600 NFS 跨用户读不到

**症状**：Mac 上 NFS 挂载，sudo cat 也不行（NFS 协议挡）。

**修复**：save 后 `os.chmod(file, 0o644)`；目录 `os.chmod(dir, 0o755)`。

---

### 💡 6.6 路径穿越没挡

**症状**：理论上 conversation_id="../../../etc/passwd" 能写到任意位置。

**修复**：`_safe_segment` 函数严格白名单 `[A-Za-z0-9_-]+`，清洗结果 != 原值即 raise。

---

### 💡 6.7 评测 case 散在 backend/eval/

**v0.97 现状**：smoke_cases.json / full_cases.json 在 backend/eval/，docker COPY 进 image。

**新版做法**：case 数据放仓库根 `eval-cases/`，部署时挂载或单独 build target。

---

## 7. LLM 调用

### 🔥 7.1 enable_thinking 漏改

**症状**：Qwen3 输出大量 "<thinking>...</thinking>" 污染 reply 和 JSON 解析失败。

**v0.97 修复**：每个 LLM 调用点补丁式加 `extra_body={"enable_thinking": False}`。

**新版做法**：`infra/llm/qwen_client.py` 默认内置，业务代码无需关心。

---

### ⚠️ 7.2 LLM JSON 输出包 ```json ... ```

**症状**：模型偶尔返回 markdown 围栏 → `json.loads` 失败。

**修复**：解析前剥围栏：

```python
def strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s.rsplit("```", 1)[0]
    return s.strip()
```

---

### ⚠️ 7.3 流式回复中途异常无 partial 保存

**症状**：LLM 流到一半挂掉 → 前端看到半截 → 后端没存任何 message。

**修复**：
- 流过程中累积 `partial_reply`
- catch 异常后落 message: content=partial_reply, error=str(exc)
- 前端可显示 "[内容中断]"

---

## 8. SSE / 前后端

### 🔥 8.1 EventSource 用 GET，history 长后 URL 被截断

**症状**：聊到 50 轮后断流。

**修复**：fetch POST + ReadableStream 自解析 SSE。前端在 v0.97 已修，新版从一开始就这么写。

---

### ⚠️ 8.2 EventSource 不带 Authorization header

**修复**：fetch POST 可正常带 header。EventSource 时代要用 URL 传 token，不安全。

---

### 💡 8.3 SSE 心跳

长流（>30s）需要心跳，否则中间网关可能断。`EventSourceResponse` 加 `ping=15`。

---

## 9. 部署 / 运维

### ⚠️ 9.1 docker compose 启动顺序未约束

**症状**：backend 比 redis 先起 → 第一秒 Celery 连不上。

**修复**：`depends_on:` + `condition: service_healthy` + Redis healthcheck。

---

### ⚠️ 9.2 NFS 挂载下 SQLite 文件锁不稳

**症状**：偶发 database locked。

**修复**：SQLite 文件放容器本地 volume（`/app/data`），不挂 NFS。NFS 只挂 `/app/eval/exports/`（评测产物）。

---

### 💡 9.3 Celery worker 没限速

**症状**：用户连发 10 句 → 同一用户的 extract 任务并发抢 SQLAlchemy。

**修复**：按 user_id routing key，同一用户串行：

```python
@celery_app.task(bind=True)
def extract_memory_task(self, user_id, ...):
    with redis_lock(f"extract:{user_id}", timeout=30):
        ...
```

---

## 10. 测试

### 🔥 10.1 selftest 散在一个 1300 行单文件

**v0.97 现状**：`selftest_p0.py` 单文件 1304 行覆盖了 112 个 case。改一处影响其他 case。

**新版做法**：拆为 `tests/unit/<module>/test_xxx.py`，pytest 标记分类（`@pytest.mark.smoke`、`@pytest.mark.integration`）。

---

### ⚠️ 10.2 测试 MemoryContext 要手动初始化所有 list 字段

**症状**：`TypeError: 'type' object is not iterable`——因为 MemoryContext 某字段默认 `list`（类型）而非 `[]`。

**修复**：Pydantic 模型用 `default_factory=list`，不要 `default=list`。

---

### ⚠️ 10.3 SQLite 测试与生产 dialect 差异

**症状**：测试用 in-memory SQLite 通过，生产 SQLite 出 SQL syntax 错。

**修复**：测试用 `sqlite:///:memory:?cache=shared`，并定期跑一次集成测覆盖真实 schema。

---

## 11. 文档与变更管理

### ⚠️ 11.1 prompt 字符串改了忘改 L0 检查常量

**v0.97 现状**：每次改 prompt 段标题 → L0 false positive 一片 → 再去修常量。

**新版做法**：composer 输出 sections 字段，L0 检查不字符串匹配。

---

### 💡 11.2 Changelog 不及时

**v0.97 现状**：单一 Changelog-YYYY-MM-DD.md 累积多版本，找改动要翻。

**新版做法**：每次版本一个 `CHANGELOG/v1.x.x.md`，写上"breaking"标记。

---

## 12. 完整清单（一张表）

| # | 坑 | 影响 | 优先级 | 章节 |
|---|---|---|---|---|
| 1.1 | services 耦合 ORM/Celery/FastAPI | 不可测 | 🔥 | §1 |
| 1.2 | Web 和 Celery 共用 engine | DB 锁 | 🔥 | §1 |
| 1.3 | Router 函数胖 | 难改难测 | ⚠️ | §1 |
| 1.4 | scripts 混入部署 | 镜像膨胀 | 💡 | §1 |
| 2.1 | Memory Router query 稀释 | 召回降级 | 🔥 | §2 |
| 2.2 | Intent 无 fallback | 服务崩 | ⚠️ | §2 |
| 2.3 | 硬规则与小模型顺序 | 误判 | ⚠️ | §2 |
| 2.4 | intent 字符串散落 | 改名困难 | 💡 | §2 |
| 3.1 | BASE/TONE/CONTRACT 打架 | 人格失效 | 🔥 | §3 |
| 3.2 | 敏感场景关人格契约 | 三人格雷同 | 🔥 | §3 |
| 3.3 | 空段渲染（无）| LLM 误导 | ⚠️ | §3 |
| 3.4 | system 全文存 meta | DB 膨胀 | ⚠️ | §3 |
| 3.5 | L0 字符串匹配段标题 | false positive | ⚠️ | §3 |
| 3.6 | GUIDE 与 CONTRACT 冲突 | 行为不稳 | 💡 | §3 |
| 4.1 | Profile 类型漂移 | 读取报错 | 🔥 | §4 |
| 4.2 | Mem0 infer=True 稀释 | 召回降级 | 🔥 | §4 |
| 4.3 | Relationship via 自环 | 前端崩 | ⚠️ | §4 |
| 4.4 | Episodic 召回 limit 过滤后剩少 | 召回降级 | ⚠️ | §4 |
| 4.5 | banned 不在 write 端过滤 | 数据污染 | ⚠️ | §4 |
| 5.1 | correction 同轮 extract | 越纠越错 | 🔥 | §5 |
| 5.2 | LLM 整句当 entity 落 banned | banned 表脏 | ⚠️ | §5 |
| 5.3 | confidence 无阈值 | 误删 | ⚠️ | §5 |
| 6.1 | personality_signature 数 activated 而非 reply 引用 | 误报 | 🔥 | §6 |
| 6.2 | reply_off_topic 误报短事实回应 | 误报 | 🔥 | §6 |
| 6.3 | fabrication_under_challenge 漏报 | 漏报幻觉 | ⚠️ | §6 |
| 6.4 | final_status 太严 | 假阴性 | ⚠️ | §6 |
| 6.5 | 落盘 0600 NFS 不可读 | 文件不可用 | ⚠️ | §6 |
| 6.6 | 路径穿越没挡 | 安全 | 💡 | §6 |
| 6.7 | 评测 case 在 backend/ | 镜像膨胀 | 💡 | §6 |
| 7.1 | enable_thinking 漏改 | 输出污染 | 🔥 | §7 |
| 7.2 | LLM JSON 包 markdown 围栏 | 解析失败 | ⚠️ | §7 |
| 7.3 | 流式中断无 partial | 数据丢 | ⚠️ | §7 |
| 8.1 | EventSource GET URL 截断 | 长会话断流 | 🔥 | §8 |
| 8.2 | EventSource 无 Authorization | 不安全 | ⚠️ | §8 |
| 8.3 | SSE 无心跳 | 断流 | 💡 | §8 |
| 9.1 | docker 启动顺序未约束 | 启动失败 | ⚠️ | §9 |
| 9.2 | SQLite on NFS | DB 锁 | ⚠️ | §9 |
| 9.3 | Celery 同用户并发 | 写冲突 | 💡 | §9 |
| 10.1 | selftest 单文件 1300 行 | 难维护 | 🔥 | §10 |
| 10.2 | Pydantic default=list bug | 类型错 | ⚠️ | §10 |
| 10.3 | SQLite dialect 差异 | 集成失败 | ⚠️ | §10 |
| 11.1 | prompt 改了忘改 L0 常量 | false positive | ⚠️ | §11 |
| 11.2 | Changelog 累积 | 难追溯 | 💡 | §11 |

---

## 13. 重构开工前检查清单

实现每个模块前，请先确认以下问题答案明确：

- [ ] services 函数签名只接 Pydantic DTO，不接 Request / Session？
- [ ] 该模块用的 Repository 已经定义 Protocol？
- [ ] LLM 调用是否通过 LLMRouter？enable_thinking 是否默认 false？
- [ ] 是否覆盖 §12 表里相关坑的修复？
- [ ] 单元测试可以无容器跑通？
- [ ] PromptPack 输出结构化 sections，不靠字符串匹配？
- [ ] 写盘后 chmod 0644？
- [ ] 任何 schema 字段带 schema_version？
