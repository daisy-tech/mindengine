# MindEngine 重构路线图 · M1 → M5

> 责任：把 MindEngine 后端"从 0 重写到与 legacy MindMem v0.97 功能等价"切成 5 个里程碑，每个里程碑有明确"通过标准"。
> 预估工期：单人 Opus 4.7 ≈ 3-4 周，含测试。

---

## 总览

```
M1  脚手架 + 域模型      → 1-2 天
M2  核心聊天链路通       → 4-6 天
M3  四层记忆 + 异步抽取  → 4-6 天
M4  纠错管线 + 评测实验室 → 4-6 天
M5  前端适配 + 上线对齐  → 2-3 天
———————————————————————————
合计                     → 15-23 天
```

---

## M1 · 脚手架 + 域模型（1-2 天）

### 目标

仓库可跑、骨架就位、所有领域类型定义完成（无 IO 依赖）。

### 任务清单

1. **创建仓库结构**
   - 按 [README.md](./README.md) §推荐项目结构 建目录
   - `pyproject.toml` + 依赖（fastapi / sqlalchemy / pydantic / celery / openai / mem0 / pytest）
   - Dockerfile + docker-compose.yml
   - pre-commit（ruff + mypy）

2. **写所有 domain 类型**
   - `domain/memory.py`：Profile / Event / Episodic / Relationship + schema_version
   - `domain/route.py`：MemoryRoute / MemoryUsage / Intent enum
   - `domain/prompt.py`：PromptPack / PromptMeta / SectionKey
   - `domain/correction.py`：MemoryDeprecation / BannedEntity
   - `domain/eval.py`：EvalCase / EvalResult / ReviewRule

3. **写所有 Protocol**
   - `services/protocols.py`：LLMClient / IntentClassifier / TaskDispatcher / 各 Repository

4. **配置基础设施**
   - SQLAlchemy 模型（按 [08-Data-Model.md](./08-Data-Model.md)）
   - Alembic 初始化 + 第一次 migration
   - Redis 连接
   - Mem0 / Qdrant 连接

5. **写 LLMRouter 雏形**
   - `infra/llm/qwen_client.py`（含 enable_thinking 默认 false）
   - mock client for tests

### 通过标准

- `make test` → 至少 30 个 domain / repository / llm-mock 测试通过
- `docker compose up` → 5 个服务起来（backend/redis/qdrant/celery/celery-beat），健康检查全过
- `curl localhost:8000/healthz` → 200

---

## M2 · 核心聊天链路通（4-6 天）

### 目标

三种人格各跑一轮 casual / memory_challenge / knowledge_task，prompt_meta 字段完整、人格契约正确渲染、回复合理。

### 任务清单

1. **Memory Router**
   - `services/memory_router/rules.py`：硬规则（按 [03-Subsystem-Memory-Router.md](./03-Subsystem-Memory-Router.md) §3）
   - `services/memory_router/intent_classifier.py`：小模型分类
   - `services/memory_router/policy.py`：策略查表
   - `services/memory_router/router.py`：三层组合

2. **Memory Context**（M2 阶段简化：先只读 profile + episodic）
   - `services/memory_context/loader.py`
   - `services/memory_context/filter.py`（banned / deprecated）
   - `services/memory_context/usage_tagger.py`

3. **Prompt Composer**
   - `services/prompt_composer/base.py`（BASE_PERSONA / HARD_RULES）
   - `services/prompt_composer/intent_guides.py`（9 类）
   - `services/prompt_composer/personality.py`（契约 + 注入逻辑）
   - `services/prompt_composer/composer.py`（组装 + 输出 PromptPack）

4. **Chat Orchestrator**
   - `services/chat_orchestrator.py`：编排 router → context → composer → llm.stream → message.persist → dispatch
   - 同步落 Message + meta_json
   - dispatch task（M2 阶段先空实现，M3 接 Celery）

5. **API**
   - `api/chat.py`：POST /api/chat/stream
   - `api/auth.py`：登录注册（JWT）

6. **测试**
   - 黄金样本：3 personality × 3 intent = 9 个 compose 测试
   - Memory Router 单测 ≥ 15 个
   - 端到端 mock LLM：发"你好" → 走完整流程 → reply 非空、meta 字段完整

### 通过标准

- 手动 curl POST chat/stream，三种人格回复差异肉眼可见
- selftest §1-5（Memory Router + Composer + 人格契约 + L0/L1 部分）全过 ≥ 50 个 case

---

## M3 · 四层记忆 + 异步抽取（4-6 天）

### 目标

Celery 任务真的把记忆写入四层。新用户聊 10 轮后 profile / events / episodic / relationship 都有数据。

### 任务清单

1. **完善 Memory Context Loader**
   - 接 events / relationships
   - asyncio.gather 并发拉 4 层
   - banned / deprecated 过滤打通

2. **Celery 任务**
   - `workers/extract_memory.py`（Mem0 入库）
   - `workers/extract_profile.py`（LLM 抽取 + 合并）
   - `workers/extract_event.py`（LLM 抽取 + 入库）
   - 同用户串行（Redis lock）

3. **Repository 完整实现**
   - profile / event / episodic / relationship 都实现
   - banned_entity / deprecation 实现

4. **API**
   - `api/memory.py`：list_profile / list_events / list_episodic / list_relationships / delete_episodic / list_banned / list_deprecations
   - `api/conversations.py`：list/get/create/delete/export_audit

5. **测试**
   - 端到端集成：注册新用户 → 跑 10 轮 → 验证 4 层都有数据
   - 关系图无 self-loop
   - banned 同时在 write 和 read 端生效
   - Mem0 infer=False 验证

### 通过标准

- 自测 §1-6 + §11 (memory layers + correction extract skip) 全过
- 真实使用：新用户聊 10 轮后人物画像可见

---

## M4 · 纠错管线 + 评测实验室（4-6 天）

### 目标

纠错完整跑通；评测实验室能跑合成 case 和真实聊天评估。

### 任务清单

1. **Correction**
   - `services/correction/extractor.py`：抽取纠错目标
   - `services/correction/judge.py`：LLM 判断 action + banned
   - `services/correction/applier.py`：跨层软删 + banned 入库
   - `workers/correction_cleanup.py`

2. **Eval Synthetic**
   - `services/eval_synthetic/case_loader.py`
   - `services/eval_synthetic/runner.py`
   - `services/eval_synthetic/reporter.py`
   - 移植 smoke_cases.json / full_cases.json
   - `services/eval_synthetic/persona_seed.py`：seed_eval_persona

3. **Eval Chat Review**
   - `services/eval_chat_review/l0_rules.py`（8 条结构）
   - `services/eval_chat_review/l1_rules.py`（11 条启发式，必须含修复后的 personality_signature 和 reply_off_topic）
   - `services/eval_chat_review/attribution.py`
   - `services/eval_chat_review/reviewer.py`
   - `services/eval_chat_review/store.py`（chmod 0644 + 路径穿越防御）

4. **API**
   - `api/eval.py`：list_synthetic / start_synthetic / single_query / seed_persona / chat_audit (force) / chat_audit_stored (list/delete)

5. **测试**
   - 自测 §7-9 (correction + L0 + L1)
   - 移植 v0.97 的 112 个 selftest case，全过
   - 合成评测 smoke 20 跑通通过率 ≥ 90%

### 通过标准

- 自测全 112 个 case 全过
- 端到端纠错：让 AI 说错家乡 → 纠正 → 再问 5 次都答对
- 真实聊天评估对一个 conversation 跑通 + 落盘 + 列表 + 删除

---

## M5 · 前端适配 + 上线对齐（2-3 天）

### 目标

现有前端 5 个 Tab 都能正常工作；API 响应字段与前端期望对齐。

### 任务清单

1. **API 适配层**
   - 对照 `frontend/src/stores/` 各 store，把字段名/路径调对齐
   - 必要时前端做小幅调整（用户已许可 loose mode）

2. **品牌适配（小白 / XiaoBai）**
   - 从 `docs/rebuild/xiaobai-avatar.png` 复制到 `public/xiaobai-avatar.png`（见 [12-Brand-XiaoBai.md](./12-Brand-XiaoBai.md)）
   - 更新 `App.vue` / `ChatView.vue` / `ChatMessage.vue` / `LoginView.vue` 四处引用
   - 侧边栏标题改为「小白」，OpenAPI title 改为 MindEngine API

3. **SSE 联调**
   - 前端 fetch POST + ReadableStream 已就绪
   - 后端 EventSourceResponse 加 ping=15s 心跳

4. **导出审计**
   - `GET /api/conversations/{id}/audit` 返回 chat_audit_v1

5. **部署联调**
   - docker compose up 起完整栈
   - NFS 挂载 `/app/eval/exports` 路径对
   - chmod 0644 验证 Mac 端可读

6. **回归测试**
   - 跑 smoke 20 + full 50 各一次
   - 跑真实聊天评估 ≥ 3 个 conversation，与 legacy MindMem v0.97 输出 diff

7. **文档**
   - 更新 README.md（部署、env、迁移说明）
   - 写 CHANGELOG/v2.0.0.md（MindEngine 首个正式版）

### 通过标准

- 前端 ChatView / MemoryView / EvalView / PromptDrawer / SocialGraph 全部正常
- 品牌验收通过（[12-Brand-XiaoBai.md](./12-Brand-XiaoBai.md) §4 五项全过）
- 真实聊天评估输出与 legacy MindMem v0.97 字段对齐（字段相同、final_status 相同）

---

## 风险与缓解

| 风险 | 概率 | 缓解 |
|---|---|---|
| 域模型设计不全，到 M3 才发现要返工 | 中 | M1 阶段对照 [08-Data-Model.md](./08-Data-Model.md) 逐字段过 |
| Memory Router 召回不好（无对比） | 中 | M2 末跑同一组 query 与 legacy MindMem v0.97 对比 |
| 人格契约 LLM 服从率低 | 中 | M4 跑评测验证；不到 70% 则加 few-shot |
| Celery 并发冲突 | 低 | M3 用 redis_lock；同用户串行 |
| 评测实验室前端字段对齐难 | 中 | M4 提前 review chat_audit_v1 schema 与前端 |

---

## 验收 Demo Script

完成后，按以下脚本演示：

```
1. 注册新用户 zhangsan@test.com
2. 切换中性人格
3. 发：你好，我叫张三，老家湖南怀化
4. 看 prompt_meta：intent=casual, personality=balanced, contract 注入
5. 发：你记得我老家在哪
6. 看 reply 必含"怀化"，无幻觉
7. 发：错，是怀宁，邻近安徽岳西
8. 看 intent=correction，dispatch 只发 correction_cleanup
9. 再发：你记得我老家在哪
10. reply 必含"怀宁"，绝不含"怀化"/"岳西"

11. 切换外向人格
12. 发：今天有点累
13. 看 reply ≥ 60 字，有具体画面承接，可能给建议

14. 打开评估实验室 → 跑这个会话的真实聊天评估
15. 看每轮 final_status，归因 root_cause_top
16. 删除评估结果 → 再次"开始评估"重跑

17. 跑合成评测 smoke 20 → 通过率 ≥ 95%
```

---

## 完成定义（Definition of Done）

MindEngine 后端重构完成 = 以下全部成立：

- [ ] 所有 M1-M5 任务清单完成
- [ ] selftest 112+ 项全过
- [ ] smoke / full 合成评测通过率 ≥ legacy MindMem v0.97
- [ ] 真实聊天评估输出与 legacy MindMem v0.97 字段对齐
- [ ] 现前端 5 个 Tab 全功能可用
- [ ] services/ 代码 100% 可无容器跑测
- [ ] 总核心代码 ≤ 7000 行（legacy MindMem ~10k）
- [ ] [10-Lessons-Learned.md](./10-Lessons-Learned.md) §13 检查清单全勾
- [ ] [12-Brand-XiaoBai.md](./12-Brand-XiaoBai.md) §4 品牌验收五项全过
- [ ] CHANGELOG/v2.0.0.md 已写（MindEngine 首个正式版），明确 breaking changes 列表
