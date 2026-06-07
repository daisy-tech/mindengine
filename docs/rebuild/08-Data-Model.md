# 数据模型 · MindEngine 数据库与向量库

> 责任：列出 MindEngine 后端所有持久化结构。
> 颗粒度：表 / 列 / 类型 / 索引 / 约束。表名/字段名可微调，但结构必须等价 legacy MindMem v0.97。

> 🔄 **修订 v1.1（2026-06）**：存储栈从「SQLite + Mem0 + Qdrant」改为 **Postgres + pgvector**（决策 D1）。
> - 关系数据与向量数据**同库**（Postgres），同事务、同备份。
> - episodic 不再依赖 Mem0/Qdrant，改为 `episodic_memories` 表 + `vector` 列（pgvector）。
> - 一次性消除坑 1.2（web/celery 共用 engine）、9.2（SQLite on NFS 锁）、9.3（同用户串行）、10.3（dialect 差异）。
> - 详见 §3（向量存储）与 §10（迁移）。

---

## 1. 存储总览

| 系统 | 用途 | 实现 |
|---|---|---|
| 关系库 | profile / event / relationship / message / conversation / deprecation / banned | **Postgres** |
| 向量库 | episodic（语义记忆） | **Postgres + pgvector**（同库，`vector` 列 + HNSW 索引） |
| 文件 | 评测落盘报告 | `/app/eval/exports/`（NFS 可挂） |
| 队列 | Celery broker / backend | Redis |
| Cache | LLM / intent 调用结果（可选） | Redis |

> 🔄 v1.1：infra 服务从「Postgres? 无（SQLite 文件）+ Mem0 + Qdrant + Redis」净简化为 **Postgres + Redis** 两个有状态服务。

---

## 2. SQLAlchemy 模型清单

### 2.1 用户与认证

```python
class User(Base):
    __tablename__ = "users"
    id          = Column(String(36), primary_key=True)  # uuid
    email       = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)  # argon2id（见下方安全说明）
    display_name  = Column(String(100))
    personality   = Column(String(16), default="balanced")  # enum 用 string，见 §6.3
    schema_version = Column(Integer, default=1)
    is_active    = Column(Boolean, default=True)
    is_dev       = Column(Boolean, default=False)  # eval_user 标记
    created_at   = Column(DateTime, default=utcnow)
```

> 🔄 **修订 v1.1 · 安全基线（决策 D6）**：
> - **口令哈希用 `argon2id`**（`argon2-cffi`），不要裸 bcrypt/sha；存"算法$参数$盐$哈希"自描述串，便于后续提参数。
> - 鉴权失败要限流（同 IP/同 email 滑窗），避免暴力撞库。
> - 涉及"重置/灌库"的 dev 端点（`seed_persona` 等）**不能只靠 `DEV_MODE` env 字符串**：见 [09-LLM-Strategy.md](./09-LLM-Strategy.md) §9 与 [02-TDD.md](./02-TDD.md) §7（`DEV_MODE` 默认 `false` + 启动期硬开关 + 路由级 allowlist）。误配=用户四层记忆可被清空，按高危对待。

### 2.2 会话与消息

```python
class Conversation(Base):
    __tablename__ = "conversations"
    id           = Column(String(64), primary_key=True)  # 业务前缀 conv_xxx
    user_id      = Column(String(36), ForeignKey("users.id"), index=True)
    title        = Column(String(200))
    archived     = Column(Boolean, default=False)
    created_at   = Column(DateTime, default=utcnow, index=True)
    updated_at   = Column(DateTime, default=utcnow, onupdate=utcnow)

class Message(Base):
    __tablename__ = "messages"
    id           = Column(String(64), primary_key=True)
    conversation_id = Column(String(64), ForeignKey("conversations.id"), index=True)
    user_id      = Column(String(36), index=True)
    role         = Column(Enum("user","assistant","system"))
    content      = Column(Text)
    meta_json    = Column(Text)   # PromptMeta 序列化（assistant 必填）
    error        = Column(Text)   # 出错时记录
    created_at   = Column(DateTime, default=utcnow, index=True)
    INDEX (conversation_id, created_at)
```

### 2.3 四层记忆

```python
class ProfileRow(Base):
    __tablename__ = "profiles"
    user_id      = Column(String(36), primary_key=True)
    data_json    = Column(Text)            # 整个 Profile 序列化
    schema_version = Column(Integer, default=1)
    updated_at   = Column(DateTime, default=utcnow, onupdate=utcnow)

class Event(Base):
    __tablename__ = "events"
    id           = Column(String(36), primary_key=True)
    user_id      = Column(String(36), index=True)
    type         = Column(Enum("experience","plan","milestone","challenge","reflection"))
    title        = Column(String(200))
    content      = Column(Text)
    occurred_at  = Column(DateTime, index=True)
    status       = Column(Enum("active","deprecated"), default="active", index=True)
    source_message_id = Column(String(64))
    created_at   = Column(DateTime, default=utcnow)
    INDEX (user_id, occurred_at)
    INDEX (user_id, status)

class Relationship(Base):
    __tablename__ = "relationships"
    id           = Column(String(36), primary_key=True)
    user_id      = Column(String(36), index=True)
    name         = Column(String(100))
    role         = Column(String(50))     # 妻子/儿子/朋友/同事...
    attributes_json = Column(Text)
    via          = Column(String(36))     # FK to relationships.id; not self
    status       = Column(Enum("active","deprecated"), default="active")
    created_at   = Column(DateTime, default=utcnow)
    INDEX (user_id, name)
    CHECK (via IS NULL OR via != id)

# 🔄 修订 v1.1：episodic 改为 Postgres 同库表 + pgvector 列（取代 Mem0/Qdrant）
class EpisodicMemory(Base):
    __tablename__ = "episodic_memories"
    id           = Column(String(36), primary_key=True)
    user_id      = Column(String(36), index=True)
    text         = Column(Text)                       # 事实片段原文
    embedding    = Column(Vector(1024))               # pgvector 列，dim=1024
    status       = Column(String(16), default="active", index=True)  # active/deprecated（软删）
    source_message_id = Column(String(64))
    source       = Column(String(32))                 # extract_memory / migration / ...
    created_at   = Column(DateTime, default=utcnow, index=True)
    # 向量近邻索引见 §7（HNSW / ivfflat），按 user_id 预过滤
    INDEX (user_id, status)
```

> 🔄 v1.1：episodic 不再外置于向量库，与四层其余数据同在 Postgres，软删（status='deprecated'）与 banned 过滤都在同一事务/同一查询里完成。向量存储细节见 §3。

### 2.4 纠错管线

```python
class MemoryDeprecation(Base):
    __tablename__ = "memory_deprecations"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    user_id      = Column(String(36), index=True)
    source       = Column(Enum("episodic","event","profile","entity"))
    ref_id       = Column(String(100), index=True)
    original_text= Column(Text)
    new_text     = Column(Text)
    reason       = Column(Text)
    correction_conversation_id = Column(String(64))
    correction_turn_id         = Column(String(64))
    llm_confidence = Column(Float)
    action       = Column(Enum("deprecate","update","audit_only"))
    deprecated_at = Column(DateTime, default=utcnow, index=True)
    restored_at  = Column(DateTime)
    INDEX (user_id, source)
    INDEX (user_id, deprecated_at)

class BannedEntity(Base):
    __tablename__ = "banned_entities"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    user_id      = Column(String(36), index=True)
    entity       = Column(String(50), index=True)
    reason       = Column(Text)
    created_at   = Column(DateTime, default=utcnow)
    UNIQUE (user_id, entity)
```

### 2.5 评测

```python
class EvalRun(Base):
    """合成评测的批运行记录。真实聊天评估走文件，不入库。"""
    __tablename__ = "eval_runs"
    id           = Column(String(36), primary_key=True)
    run_type     = Column(Enum("smoke","full","custom"))
    started_at   = Column(DateTime, default=utcnow)
    finished_at  = Column(DateTime)
    total        = Column(Integer)
    passed       = Column(Integer)
    pass_rate    = Column(Float)
    report_json  = Column(Text)
    created_by   = Column(String(36))    # user_id
```

---

## 3. 向量存储（Postgres + pgvector）

> 🔄 **修订 v1.1（决策 D1/D4）**：episodic 从 Mem0+Qdrant 改为 Postgres `episodic_memories` 表 + pgvector `embedding` 列。
> **为什么砍 Mem0**：legacy 关掉 `infer`（坑 4.2）后，Mem0 ≈ "embed + 存 + 召回"，自身"智能"全关，却背着额外依赖、版本漂移、独立配置面。事实抽取本就在 service 层做完，向量层只需"存+近邻召回"，pgvector 足够。
> **为什么砍 Qdrant**：单机、低并发、每用户上限 2000 条 episodic，远未触及专用向量库的性能边界；同库省一个有状态服务、省一份备份、省跨库一致性烦恼。

### 3.1 数据隔离

无独立 collection 概念。隔离靠 `episodic_memories.user_id` + Repository 层强制 user_id 过滤（见 §8）。**所有向量检索必须按 user_id 预过滤**（`WHERE user_id = :uid AND status='active'`），再做近邻排序。

### 3.2 向量配置

| 项 | 值 |
|---|---|
| embedding model | `text-embedding-v3` (DashScope) 或 `text-embedding-3-small`（OpenAI），见 [09-LLM-Strategy.md](./09-LLM-Strategy.md) §1 模型常量表 |
| dim | 1024 |
| distance | cosine（pgvector `vector_cosine_ops`） |
| index | HNSW（pgvector ≥ 0.5.0），或 ivfflat 兜底 |

### 3.3 检索 SQL（示意）

```sql
SELECT id, text, 1 - (embedding <=> :query_vec) AS score
FROM episodic_memories
WHERE user_id = :uid AND status = 'active'
ORDER BY embedding <=> :query_vec
LIMIT :limit;          -- limit 取 max_explicit * 2，给后续 banned 过滤留余量（坑 4.4）
```

banned 过滤可在 SQL 内（`AND text NOT LIKE ...` 粗筛）或 service 层精筛，二者择一并测；deprecated 直接靠 `status='active'` 排除。

### 3.4 软删

不物理删向量行，置 `status='deprecated'`（与 events 一致），读取时 `WHERE status='active'` 过滤。`MemoryDeprecation` 表记审计与可恢复信息。

**为什么**：可回滚、可审计；pgvector 删行也会留 dead tuple，软删 + 周期性 `compact`/`VACUUM` 更可控。

---

## 4. 文件存储

### 4.1 评测报告

```
/app/eval/exports/
├── reviews/
│   └── <user_id>/
│       └── <conversation_id>.json     # eval_review_v1 完整包
├── synthetic/
│   └── <run_id>.json                  # 合成评测报告
└── cases/
    ├── smoke_cases.json
    └── full_cases.json
```

### 4.2 文件权限

所有写入文件 chmod 0644；目录 chmod 0755。

### 4.3 audit 导出

走 API `GET /api/conversations/{id}/audit` 直接生成、不落盘（保护用户隐私），由用户主动下载。

---

## 5. Redis

### 5.1 Celery

```
mindengine:queue:default      # 任务队列
mindengine:queue:correction   # 单独队列，纠错任务可独立 worker
mindengine:result:<task_id>   # 结果，TTL 1h
```

### 5.2 缓存（可选）

```
mindengine:cache:intent:<sha1(message)>  # intent 分类结果，TTL 24h
mindengine:cache:llm:<sha1(prompt)>      # LLM 完整 response，TTL 1h（仅相同 prompt）
```

- 🔄 **修订 v1.1（决策 D4）**：`intent` 分类是确定性纯函数（temperature=0，同输入同输出），**生产可安全开启 intent cache**，是首字延迟优化的有效手段（见 [03-Subsystem-Memory-Router.md](./03-Subsystem-Memory-Router.md) §8）。key 用 `sha1(normalized_message + recent_history_window)`，命中率取决于历史窗口口径。
- `llm:` 主聊缓存（非确定性、含记忆上下文）只在测试 / dev 用，生产关掉避免脏数据。

---

## 6. Schema 演化

### 6.1 用 Alembic

```bash
alembic revision --autogenerate -m "add memory_deprecations table"
alembic upgrade head
```

### 6.2 字段演化

Profile / PromptMeta 都用 `schema_version` 字段：

```python
class Profile(BaseModel):
    schema_version: int = 1
    ...

def load_profile(row) -> Profile:
    data = json.loads(row.data_json)
    version = data.get("schema_version", 0)
    if version < 1:
        data = migrate_v0_to_v1(data)
    return Profile(**data)
```

**v0.97 教训**：profile 字段类型在不同 Celery 写入中飘移（int → str → list），没有版本号难修。新版强制 schema_version 字段。

### 6.3 enum 演化

不要在 DB 用真 enum 类型——用 string + Pydantic 枚举。要加新值时只需改代码不改库。

---

## 7. 索引清单

| 表 | 索引 | 理由 |
|---|---|---|
| messages | (conversation_id, created_at) | 拉历史 |
| messages | user_id | 跨会话查 |
| conversations | (user_id, created_at) | 列表分页 |
| events | (user_id, occurred_at) | 时间线 |
| events | (user_id, status) | 过滤软删 |
| relationships | (user_id, name) | 关系查询 |
| memory_deprecations | (user_id, source) | 按层查 |
| memory_deprecations | (user_id, deprecated_at) | 最近纠错 |
| banned_entities | (user_id, entity) UNIQUE | 去重 |
| episodic_memories | (user_id, status) | 召回前预过滤 |
| episodic_memories | HNSW(embedding vector_cosine_ops) | 🔄 v1.1：pgvector 近邻检索 |

---

## 8. 数据隔离（**重要**）

> 跨 user_id 查询是 bug。新版必须：

1. 所有 Repository 方法**强制要求 user_id 参数**
2. 通过 BaseRepository 注入 `current_user_id`，二级方法不能漏过滤
3. 评估实验室单独维护 eval_user，不可与真实用户混

代码层防御：

```python
class BaseRepository:
    def __init__(self, session, user_id):
        self.session = session
        self.user_id = user_id  # 所有 query 自动加 .filter_by(user_id=self.user_id)

class EventRepository(BaseRepository):
    def list_recent(self, limit=10):
        return self.session.query(Event).filter_by(user_id=self.user_id)\
                .order_by(Event.occurred_at.desc()).limit(limit).all()
```

> 🔄 **修订 v1.1 · 隔离必须有测试（决策 D6）**：
> 数据隔离是"声明"不够，要"可证伪"。新增强制测试：
> 1. 任一 Repository 方法构造时缺 `user_id` → 直接 raise（不允许"全局 query"入口）。
> 2. 写一条 user A 的记忆，用 user B 的 Repository 检索四层 + episodic 向量召回 → 必须 0 命中。
> 3. correction/banned/deprecation 同样按 user_id 隔离断言。
> 这条作为 §9 不变式（[02-TDD.md](./02-TDD.md) §9）的一员，纳入 selftest。

---

## 9. 备份策略

| 数据 | 频率 | 方法 |
|---|---|---|
| Postgres（含 episodic 向量） | 每日 | `pg_dump`（逻辑）或 PITR/基础备份；🔄 v1.1：关系+向量同库，一次备份覆盖全部记忆 |
| Redis | 不备份 | 队列数据可重建 |
| 评测文件 | 不备份 | 可重算 |

> 🔄 v1.1：原"SQLite tar gz + Qdrant snapshot"两套备份合并为单一 Postgres 备份。pgvector 的向量随表一起 dump。

---

## 与 legacy MindMem v0.97 的差异

| 项 | legacy MindMem v0.97 | MindEngine |
|---|---|---|
| 关系库 | SQLite | 🔄 **Postgres** |
| 向量库 | Mem0 + Qdrant（独立服务）| 🔄 **Postgres + pgvector**（同库） |
| Message.meta 字段名 | `meta` (易和 SQLAlchemy 内部冲突) | `meta_json` |
| Profile 存储 | profiles 表多字段 | 单 data_json + schema_version |
| 关系 self-loop | 无 CHECK | DB CHECK + Domain validator |
| Schema 演化 | 无版本号 | 强制 schema_version |
| Repository 抽象 | 无 | BaseRepository 强制 user_id（+ 隔离测试）|
| episodic 软删 | Qdrant 不删 + deprecation 表 | status='deprecated' + deprecation 表（同库事务）|
| 口令哈希 | 未明确 | 🔄 argon2id |

---

## 10. 🔄 数据迁移（v1.1 · 决策 D7）

> ✅ **已定（2026-06）：策略 A — Greenfield 全弃，不迁移 legacy 真实数据，从 0 开始。**

### 10.1 含义与影响

- **不**写迁移脚本，不搬 legacy SQLite / Mem0 / Qdrant 数据。
- 新库从空开始；用户重新注册、记忆重新积累。
- **真实聊天评估**在初期没有 legacy 真实样本可跑——靠两条补足：
  1. **合成评测**（[07-Subsystem-Eval-Lab.md](./07-Subsystem-Eval-Lab.md) §2）作为上线前/PR 的主力。
  2. 新系统上线后**自然积累**的真实会话，逐步喂入真实聊天评估。
- legacy v0.97 仅作**行为对齐基准**（字段对齐、selftest 复刻），不作数据来源。

### 10.2 验收对齐仍然成立

虽然不迁数据，[11-Roadmap.md](./11-Roadmap.md) M5 的"真实聊天评估字段与 legacy 对齐"依然要做——用**新攒的会话**或**合成 case** 产出 `chat_audit_v1`，比对字段 schema 与 `final_status` 口径是否一致即可，不需要 legacy 的具体数据。
