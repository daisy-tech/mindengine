# 数据模型 · MindEngine 数据库与向量库

> 责任：列出 MindEngine 后端所有持久化结构。
> 颗粒度：表 / 列 / 类型 / 索引 / 约束。表名/字段名可微调，但结构必须等价 legacy MindMem v0.97。

---

## 1. 存储总览

| 系统 | 用途 | 实现 |
|---|---|---|
| 关系库 | profile / event / relationship / message / conversation / deprecation / banned | SQLite (v1) → Postgres (v2 易迁) |
| 向量库 | episodic（语义记忆） | Mem0 → Qdrant |
| 文件 | 评测落盘报告 | `/app/eval/exports/` |
| 队列 | Celery broker / backend | Redis |
| Cache | LLM 调用结果（可选） | Redis |

---

## 2. SQLAlchemy 模型清单

### 2.1 用户与认证

```python
class User(Base):
    __tablename__ = "users"
    id          = Column(String(36), primary_key=True)  # uuid
    email       = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    display_name  = Column(String(100))
    personality   = Column(Enum("introvert","balanced","extrovert"), default="balanced")
    schema_version = Column(Integer, default=1)
    is_active    = Column(Boolean, default=True)
    is_dev       = Column(Boolean, default=False)  # eval_user 标记
    created_at   = Column(DateTime, default=utcnow)
```

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
```

> episodic 不在 SQLite。见 §3。

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

## 3. 向量库（Mem0 + Qdrant）

### 3.1 collection 命名

```
mem0_user_<user_id_without_dashes>
```

每用户独立 collection，绝对隔离。

### 3.2 向量配置

| 项 | 值 |
|---|---|
| embedding model | `text-embedding-v3` (DashScope) 或 `text-embedding-3-small` |
| dim | 1024 |
| distance | cosine |
| index | HNSW (默认) |

### 3.3 每条记忆 payload

```jsonc
{
  "memory": "用户喜欢周末和儿子打台球",
  "metadata": {
    "user_id": "...",
    "created_at": "2026-06-07T11:24:00Z",
    "source_message_id": "msg_xxx",
    "source": "extract_memory"
  }
}
```

### 3.4 软删

不真删向量，而是用 `MemoryDeprecation` 表存 mem_id，读取时过滤。

**为什么**：Mem0 的硬删不可逆，且会造成索引碎片。

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

只在测试 / dev 环境用，生产建议关掉避免脏数据。

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

---

## 9. 备份策略

| 数据 | 频率 | 方法 |
|---|---|---|
| SQLite | 每日 | cron tar gz to NFS |
| Qdrant | 每日 | snapshot api |
| Redis | 不备份 | 队列数据可重建 |
| 评测文件 | 不备份 | 可重算 |

---

## 与 legacy MindMem v0.97 的差异

| 项 | legacy MindMem v0.97 | MindEngine |
|---|---|---|
| Message.meta 字段名 | `meta` (易和 SQLAlchemy 内部冲突) | `meta_json` |
| Profile 存储 | profiles 表多字段 | 单 data_json + schema_version |
| 关系 self-loop | 无 CHECK | DB CHECK + Domain validator |
| Schema 演化 | 无版本号 | 强制 schema_version |
| Repository 抽象 | 无 | BaseRepository 强制 user_id |
| Mem0 collection 命名 | mem0_user_<user_id> | 同（保留兼容） |
