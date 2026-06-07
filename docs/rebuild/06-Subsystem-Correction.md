# 子系统 · 在线记忆纠错管线（Correction Pipeline）

> 责任：用户在对话中说"不对，是 X" → AI 立刻软删错的记忆 + 实体硬封禁 + 复述用户事实。
> 不能简单地"打个 deprecated 标签就完事"——必须三层联动 + 防回流。

---

## 1. 设计目标

| 目标 | 实现 |
|---|---|
| 用户纠错后**下一轮**该错误绝不再现 | 软删 + 实体硬封禁 |
| 不会因为某个 LLM 错抽取又把错信息写回 | banned_entities 在写入时也过滤 |
| 不会"误删"用户其实没要否定的记忆 | LLM 判断 + 置信度阈值，低置信走 audit_only |
| 可回滚 | 全部用 deprecation 表，不真删 |
| 可观测 | 每次纠错操作落日志，前端可查 |

---

## 2. 触发与隔离

```
chat_orchestrator 流式回复结束后：

if route.intent == "correction":
    dispatch ONLY correction_cleanup_task(message_id, route, ...)
    # 不发 extract_memory / extract_profile / extract_event
else:
    dispatch extract_memory_task(...)
    dispatch extract_profile_task(...)
    dispatch extract_event_task(...)
```

**关键**：correction 与 extract 互斥。否则 AI 道歉后，extract 又把"安徽岳西" 抽进去了。

---

## 3. 数据模型

```python
class MemoryDeprecation(Base):
    __tablename__ = "memory_deprecations"
    id: int (PK, autoincr)
    user_id: str (index)
    source: Literal["episodic","event","profile","entity"]
    ref_id: str (index)              # episodic→mem_id; event→event_id; profile→field path; entity→entity text
    original_text: str
    reason: str
    correction_conversation_id: str | None
    correction_turn_id: str | None
    llm_confidence: float
    action: Literal["deprecate","update","audit_only"]
    new_text: str | None             # 若 action=update
    deprecated_at: datetime
    restored_at: datetime | None     # 软恢复用
```

---

## 4. 管线步骤

```
correction_cleanup_task(message_id):

  Step 1. 抽取纠错目标
    user_msg = load_message(message_id - 1)  # 用户的纠错语
    ai_msg = load_message(message_id)         # AI 刚回复的（含 prompt_meta）
    targets = extract_correction_targets(user_msg, ai_msg.prompt_meta)
    # → [{ref: "安徽岳西", verb: "不是", correct: "怀宁"}]

  Step 2. 按 target 搜候选
    candidates = []
    for t in targets:
        # 在 4 层里搜含有 t.ref 的记忆
        candidates += search_episodic(user_id, t.ref)
        candidates += search_events(user_id, t.ref)
        candidates += search_profile_fields(user_id, t.ref)
        candidates += search_relationships(user_id, t.ref)

  Step 3. LLM 判断每个候选
    for cand in candidates:
        judgement = llm_judge(
            user_correction=user_msg,
            ai_previous_reply=ai_msg.content,
            candidate=cand,
        )
        # → {action: "deprecate"|"update"|"audit_only", confidence: 0-1, ...}

  Step 4. 按阈值落库
    for cand, judgement in pairs:
        if judgement.confidence < CORRECTION_CONFIDENCE_THRESHOLD (0.7):
            _apply_audit_only(cand, judgement)  # 仅审计
        elif cand.source == "episodic":
            _apply_episodic(cand, judgement)     # mem0.soft_delete + deprecation 表
        elif cand.source == "event":
            _apply_event(cand, judgement)        # event.status='deprecated'
        elif cand.source == "profile":
            _apply_profile(cand, judgement)      # 清字段 + 加 user_corrections

  Step 5. 抽取被否定的实体 → banned_entities
    banned = llm_extract_banned(user_msg, targets)
    # → ["岳西"]
    _apply_banned_entities(user_id, banned)
```

---

## 5. LLM judge prompt

```
你在帮助评估一条"记忆候选"是否需要根据用户的纠正被清除。

用户纠正语：{user_correction}
AI 上一轮回复：{ai_previous_reply}
候选记忆：
  来源层：{candidate.source}
  原文：{candidate.text}

判断：
1. 这条候选是否就是用户在否定的对象？
2. 行动建议：
   - deprecate: 完全删除（用户明确说不存在/不对）
   - update: 替换为正确版本（用户给出了 new_text）
   - audit_only: 仅审计，不动数据（你不能很确定）
3. 置信度 0-1

输出 JSON：
{"action": "deprecate"|"update"|"audit_only", "confidence": 0.x, "reason": "<20 字>", "new_text": "<update 时必填>"}

要求：
- 只输出 JSON，不解释，不思考
- 置信度严格：50/50 → audit_only
- new_text 必须来自用户纠正语，不要自己编
```

模型：`qwen3.7-plus`，温度 0，`enable_thinking=false`。

---

## 6. Banned Entities 硬封禁

### 6.1 数据模型

```python
class BannedEntity(Base):
    __tablename__ = "banned_entities"
    id: int (PK)
    user_id: str (index)
    entity: str (index)        # "岳西"、"小鹏"
    reason: str
    created_at: datetime
    UniqueConstraint("user_id", "entity")
```

### 6.2 写入时过滤

extract_* 任务在调 `mem0.add(...)` 前：

```python
banned = await banned_repo.list(user_id)
for fact in extracted_facts:
    if text_hits_banned(fact, banned):
        logger.info("skip banned entity write: %s", fact)
        continue
    mem0.add(fact, user_id=user_id, infer=False)
```

### 6.3 读取时过滤

memory_context.load 在拿到 episodic search 结果后：

```python
episodic = [e for e in episodic if not text_hits_banned(e.text, banned)]
```

### 6.4 实体清洗规则

写入 banned_entities 前：

- 去空白
- 长度 ≥ 1 且 ≤ 8（避免整段句子被 ban）
- 字符限制：中文 + 字母数字
- 去重

### 6.5 v2 优化：embedding 近邻匹配

v0.97 用子串匹配判断"text hits banned"。问题：
- "岳西" ban 后，"岳西县" 仍命中（OK）
- 但 "我喜欢学姐" 含 "学" 与 ban 的 "学渣" 误命中

v2 改用 embedding 相似度 + 阈值，更准。

---

## 7. Prompt 配合

`INTENT_GUIDES["correction"]` 必须强制 LLM：

1. 第一人称承认错误："我记错了"
2. **复述用户给出的正确事实**
3. 禁止再提被纠正实体
4. 禁止用机械系统语："已记录"、"我会注意"

例子：

```python
INTENT_GUIDES["correction"] = """【纠错指引】
用户在纠正你之前给的错误信息。**硬性要求**：
1. 第一句必须用第一人称承认，例如「我记错了」「是我搞混了」
2. **必须复述用户给出的正确事实**（不是泛泛的"明白了"）
3. **禁止**再次出现被纠正的实体词
4. **禁止**机械系统语："已记录"、"我会注意"、"下次不会再"
5. 后面可顺势接续（按人格契约的纠错子分支决定）"""
```

---

## 8. 评估配合

新增 3 条 L1 规则（在 [07-Subsystem-Eval-Lab.md](./07-Subsystem-Eval-Lab.md) 详述）：

| 规则 | severity |
|---|---|
| `correction_persisted` | high — 检查纠错后下一轮是否还出现被禁实体 |
| `correction_no_concrete_ack` | high — 检查纠错回复是否含具体复述 |
| `fabrication_under_challenge` | high — 池外编造（未必只在 correction，但相关） |

---

## 9. 测试要求

| 用例 | 验证 |
|---|---|
| episodic 软删 | `_apply_episodic` 落一条 deprecation + mem0.soft_delete 被调用 |
| 低置信走 audit_only | confidence=0.3 → action="audit_only"，记忆仍存在 |
| banned 持久化 | `_apply_banned_entities(["岳西"," "])` → 表里只有 "岳西" |
| banned 去重 | 重复 apply 不重复入表 |
| text_hits_banned 命中 | ban "小鹏" → "小鹏不是动物" 命中 |
| 跨层联动 | 同时有 episodic + event + profile 含 "岳西" → 3 层都被处理 |
| query token 提取 | 中文短语正确分 2-gram |
| LLM 解析鲁棒 | new_text 过长/空白 → 过滤 |

---

## 10. 运维与排查

### 10.1 看某用户最近的纠错

```sql
SELECT * FROM memory_deprecations
WHERE user_id = ? ORDER BY deprecated_at DESC LIMIT 20;
```

### 10.2 看某用户的 banned

```sql
SELECT * FROM banned_entities WHERE user_id = ? ORDER BY created_at DESC;
```

### 10.3 恢复某条软删

```sql
UPDATE memory_deprecations SET restored_at = NOW() WHERE id = ?;
-- 同步调用 mem0.restore(mem_id)
-- 同步 event.status='active'
```

### 10.4 调节阈值

环境变量：

| 变量 | 默认 | 调高效果 |
|---|---|---|
| `CORRECTION_CONFIDENCE_THRESHOLD` | 0.7 | 更保守，少误删 |
| `CORRECTION_CANDIDATE_LIMIT` | 10 | 候选搜得更广，但 LLM 调用更多 |
| `BANNED_MAX_LEN` | 8 | 允许更长实体被 ban |

---

## 11. 已知限制

1. 用户用比喻纠错（"不是茶，是水"），LLM 可能误判
2. 多语言纠错（如混中英）支持差
3. 没有"延后纠错"机制（用户隔几轮才说"对了那个 X 是错的"）
4. 没有跨会话同步（在会话 A 纠错的，会话 B 已经生效——因为是同一 user_id，但用户感知慢）

---

## 与 legacy MindMem v0.97 的差异

| 项 | legacy MindMem v0.97 | MindEngine |
|---|---|---|
| 模块化 | correction_engine.py 732 行单文件 | 拆为 extractor / judge / applier 三模块 |
| LLM 判断 | 一次性输出 actions + banned_entities | 同上保留 |
| 写盘并发 | 与 web 共用 SQLAlchemy engine | Worker 独立 engine |
| 测试 | selftest 散布 | 独立 tests/unit/correction/ |
