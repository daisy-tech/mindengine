# 子系统 · 四层记忆

> 责任：把"AI 知道用户什么"分成 4 层 schema，各有写入时机、读取规则、合并策略。
> 这是 MindEngine 区别于"长上下文聊天"的根本所在。

---

## 1. 为什么是四层不是一层

| 单层（如全塞 vector DB） | 四层 |
|---|---|
| 召回靠相似度，姓名都可能召不回 | profile 直接 SQL 查，必中 |
| 累积久了噪音爆炸 | 不同层有不同保留策略 |
| 没有结构化关系 | relationships 独立成图 |
| 不知道"这个事实是什么时候发生的" | events 带 timestamp |

---

## 2. 四层 schema 总览

| 层 | 形状 | 存储 | 典型字段 |
|---|---|---|---|
| **profile** | 单条 JSON | SQLite 一行/用户 | basic / interests / occupation / location / family_structure |
| **event** | 多条 | SQLite | id, user_id, type, title, content, occurred_at, status, source_message_id |
| **episodic** | 多条 | Mem0 + Qdrant | text, score, metadata{user_id, ts, source} |
| **relationship** | 图状 | SQLite | id, user_id, name, role, attributes_json, via |

---

## 3. profile

### 3.1 结构

```python
class Profile(BaseModel):
    user_id: str
    basic: BasicInfo               # 姓名/出生年/所在地/性别
    interests: list[str]           # 兴趣爱好
    occupation: Occupation         # 职业/行业/职级
    family_structure: FamilyHint   # 家庭情况（粗）
    extra: dict                    # 其他键值对
    user_corrections: list[Correction]  # 用户主动纠错过的记录
    schema_version: int            # 演化版本号
    updated_at: datetime
```

### 3.2 写入时机

| 时机 | 谁触发 |
|---|---|
| 用户聊到画像信息 | Celery `extract_profile_task` |
| 用户直接编辑画像 | API `PATCH /api/profile` |
| 纠错管线 | `correction_engine._apply_profile()` |

### 3.3 合并策略

**v0.97 教训**：profile 字段类型不一（int / str / list / dict）很多 bug。新版必须：

1. 每个字段在 `ACCUMULATIVE_FIELDS` 还是 `OVERRIDE_FIELDS` 必须明确列出
2. 累积字段（如 interests）走 LLM 辅助 dedup + merge
3. 覆盖字段（如 occupation）后写覆盖前写，但记录 `user_corrections`
4. 类型迁移（如 `age: int` → `birthday: str`）走显式 migration

### 3.4 读取规则

| 场景 | 读哪些字段 |
|---|---|
| profile_basic | basic.{姓名/出生年/所在地/性别} —— 几乎每轮 |
| profile | profile_basic + interests + occupation + extra |
| self_summary | 全部 |

---

## 4. event

### 4.1 结构

```python
class Event(BaseModel):
    id: str
    user_id: str
    type: Literal["experience","plan","milestone","challenge","reflection"]
    title: str                # 短标题（≤ 20 字）
    content: str              # 详细描述
    occurred_at: datetime | None
    status: Literal["active","deprecated"]  # 软删用
    source_message_id: str | None
    created_at: datetime
```

### 4.2 5 类 event

| Type | 例子 |
|---|---|
| experience | "周六和儿子去吃饭" |
| plan | "下周要面试 X 公司" |
| milestone | "通过驾考"、"升职" |
| challenge | "最近压力大"、"工作不顺" |
| reflection | "意识到自己更适合做..." |

### 4.3 读取规则

- 时间近的优先（occurred_at desc）
- `event_policy=summary`：返回 title + 时间
- `event_policy=background_pain_points`：仅 challenge type，且不在 explicit pool（只能作为背景理解）
- `event_policy=none`：不读

### 4.4 软删而非硬删

纠错管线把 event.status='deprecated'，读取时过滤。**理由**：可恢复 + 可审计。

---

## 5. episodic（Mem0 / Qdrant）

### 5.1 形状

每条 episodic = 一段从对话里抽出的"事实片段"，带 embedding。

例子：
```
"用户儿子喜欢台球（第二运动）"
"用户每周和儿子打台球+吃大餐"
"用户在家里泡茶"
```

### 5.2 写入

Celery `extract_memory_task` → 调用 `mem0.add(text, user_id=...)` → Mem0 内部走 LLM 做事实抽取 + dedup + 写入 Qdrant。

**关键**：`infer=False` 让 Mem0 不二次抽取（v0.97 教训：infer=True 时 Mem0 会把已抽好的事实再"理解"一遍，引入噪音）。

### 5.3 读取

`mem0.search(query, user_id=..., limit=N)` → 按相似度返回。

**过滤步骤**（在 service 层做，不依赖 Mem0）：
1. 排除 `deprecated_episodic_ids`（来自 memory_deprecations 表）
2. 排除文本命中 `banned_entities` 的条目
3. 按 usage 标签分桶（EXPLICIT_OK / BACKGROUND_ONLY / ...）

### 5.4 数量管理

- 每用户上限：建议 2000 条（超出后定期 compact）
- 单次 retrieval 上限：limit \* 2（为后续过滤留余量）

---

## 6. relationship

### 6.1 结构

```python
class Relationship(BaseModel):
    id: str
    user_id: str
    name: str                    # "妻子张三"、"儿子"、"小李"
    role: str                    # 妻子/儿子/同事/朋友
    attributes: dict             # {年龄:35, 职业:..., 性格:...}
    via: str | None              # 通过谁认识，构图用
    created_at: datetime
```

### 6.2 v0.97 大坑

- `via` 指向自己（self-loop）→ SocialGraph 渲染崩
- 同一个人重复入库（"妻子" vs "我老婆" vs "她"）→ 关系图节点爆炸

**新版强制约束**：
1. via 不能 = name
2. 去重必须做（LLM 辅助识别"我老婆"="妻子张三"）
3. 仅 relationship_topic intent 才往池子里塞，避免泛滥

### 6.3 读取

- intent=relationship_topic：拉与当前消息中人物名匹配的关系
- intent=self_summary：拉前 10 个

---

## 7. 跨层一致性

### 7.1 用户改名场景

```
1. 用户："我叫张三，不是李四"  (intent=correction)
2. correction_engine:
   - profile.basic.name 改为"张三"
   - profile.user_corrections 加一条
   - relationships 里 name="李四" 的全部更新（如果是用户本人）
   - episodic 软删提到"李四"的条目
   - banned_entities 加 "李四"
3. 下一轮：所有层读出的都是"张三"
```

### 7.2 跨层去重

定期任务（建议每周一次）：

- compact_memories：合并相似 episodic
- clean_relationships：清理孤立 / 循环关系

---

## 8. 实现要点

### 8.1 Repository 模式

```python
class ProfileRepository(Protocol):
    async def get(self, user_id: str) -> Profile | None: ...
    async def upsert(self, profile: Profile) -> None: ...
    async def merge(self, user_id: str, partial: dict) -> Profile: ...

class EventRepository(Protocol):
    async def list_recent(self, user_id: str, limit: int) -> list[Event]: ...
    async def list_by_type(self, user_id: str, type: str) -> list[Event]: ...
    async def insert(self, event: Event) -> None: ...
    async def deprecate(self, event_id: str, reason: str) -> None: ...

class EpisodicRepository(Protocol):
    async def add(self, user_id: str, text: str, metadata: dict) -> str: ...
    async def search(self, user_id: str, query: str, limit: int) -> list[EpisodicHit]: ...
    async def soft_delete(self, mem_id: str) -> None: ...

class RelationshipRepository(Protocol):
    ...
```

API/Service 层依赖 Protocol，infra 层实现具体存储。便于换 Mem0 → Chroma / 换 SQLite → Postgres。

### 8.2 memory_context.load 的并发

```python
async def load(self, user_id: str, route: MemoryRoute) -> MemoryContext:
    # 并发拉 4 层（asyncio.gather）
    profile_task = self.profile_repo.get(user_id) if "profile" in route.load_layers else noop()
    events_task = self.event_repo.list_recent(user_id, 10) if "events" in route.load_layers else noop()
    episodic_task = self.episodic_repo.search(user_id, route.query, route.max_explicit_memories * 2) if "episodic" in route.load_layers else noop()
    relationships_task = self.relationship_repo.list_for_intent(user_id, route) if "relationships" in route.load_layers else noop()

    profile, events, episodic, relationships = await asyncio.gather(...)

    # 过滤 banned / deprecated
    banned = await self.banned_repo.list(user_id)
    deprecated = await self.deprecation_repo.list_episodic(user_id)
    episodic = [e for e in episodic if e.id not in deprecated and not hits_banned(e.text, banned)]

    # 打 usage 标签
    return MemoryContext(
        route=route,
        stable_profile=tag_profile_basic(profile),
        relevant_relationships=tag_for(relationships, route),
        relevant_events=tag_for(events, route),
        relevant_memories=tag_for(episodic, route),
        background_only=split_background(episodic),
        snapshot_stats=collect_stats(profile, events, episodic, relationships, banned),
    )
```

### 8.3 usage 标签

| Tag | 含义 |
|---|---|
| EXPLICIT_OK | 可以在回复里明说 |
| BACKGROUND_ONLY | 仅作背景理解，不主动复述 |
| FOLLOW_UP_ONCE | 可以轻问一次"还在不"，用户不接就放下 |
| AVOID_UNLESS_ASKED | 用户问起再说，否则不要主动提（如健康/财务） |

打标签的逻辑写在 `services/memory_context/usage_tagger.py`，纯逻辑可单测。

---

## 9. 测试要求

| 用例 | 验证 |
|---|---|
| profile merge 累积 vs 覆盖 | interests 累积，occupation 覆盖 |
| relationship via 不指向自己 | 强校验 raise |
| episodic 过滤 banned | 写"小鹏" + ban "小鹏" → 不出现在 context |
| episodic 过滤 deprecated | 软删后下次 context 不出现 |
| memory_context.load 并发 | mock 4 个 repo，验证 asyncio.gather 正确组装 |
| usage_tagger 分桶 | profile_basic → stable_profile，events → relevant_events |

---

## 与 legacy MindMem v0.97 的差异

| 项 | legacy MindMem v0.97 | MindEngine |
|---|---|---|
| Profile 字段合并 | 散在 profile_engine.py | Repository 层封装，字段策略表 |
| Episodic 过滤 banned/deprecated | 散在 memory_context.py | filter.py 独立纯函数 |
| Relationship via=self 校验 | 修复后才加 | domain 层 validator |
| 4 层并发拉 | 串行 | asyncio.gather 并发 |
| 替换 Mem0 难度 | 高（耦合死） | Repository 抽象，可替换 |

---

## 11. 演化方向

- v2：episodic 加 importance score（不是所有事实都同等权重）
- v2：events 加 entity link（自动关联到 relationship）
- v2：profile 字段策略 LLM 自动生成（输入新字段名，LLM 判断 accumulate vs override）
