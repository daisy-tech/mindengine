# LLM 策略 · 选型 / Prompt / 流式 / 错误处理

> 责任：所有 LLM 调用的统一规约。新版必须从一开始就用这套抽象，否则后期改不动。

---

## 1. 模型选型

### 1.1 当前选型（基于 v0.97 验证）

| 用途 | 模型 | 理由 |
|---|---|---|
| 主聊（chat） | `qwen3.7-max` | 用户感知质量 |
| Intent 分类 | `qwen3.7-plus` | 结构化任务高频，1/3 成本 |
| Profile/Event 抽取 | `qwen3.7-plus` | 结构化抽取，无需推理 |
| Correction 判断 | `qwen3.7-plus` | 结构化 + 阈值控制 |
| Embedding | `text-embedding-v3` (DashScope) | Mem0 默认 |

### 1.2 备选模型

| 角色 | 备选 1 | 备选 2 |
|---|---|---|
| 主聊 | Claude 4.6 sonnet | GPT-5.5 medium |
| 结构化 | GPT-5.5 mini | Claude 4.5 haiku |
| Embedding | text-embedding-3-small (OpenAI) | bge-m3 (本地) |

### 1.3 切换策略

新版从一开始就用 `LLMClient` 抽象，role-based 路由：

```python
class LLMClient(Protocol):
    async def complete(self, system: str, messages: list[Msg], **kw) -> str: ...
    async def stream(self, system: str, messages: list[Msg], **kw) -> AsyncIterator[str]: ...
    async def complete_json(self, system: str, messages: list[Msg], schema: type, **kw) -> BaseModel: ...

class LLMRouter:
    """根据 role 路由到具体 client"""
    def __init__(self, clients: dict[Role, LLMClient]): ...
    def for_role(self, role: Role) -> LLMClient: ...

# 用法
llm = router.for_role("chat")
async for tok in llm.stream(...): ...

llm_struct = router.for_role("structured")
result: IntentResult = await llm_struct.complete_json(system, [msg], schema=IntentResult)
```

切换模型只改 `clients` map，不动业务代码。

---

## 2. Qwen 强制要求

### 2.1 enable_thinking=false

Qwen3 系列默认开启思考链，会：
- 显著延迟首字
- 输出大量推理过程污染 reply
- 破坏结构化 JSON 输出

**强制**：

```python
response = await client.chat.completions.create(
    model=model,
    messages=[...],
    extra_body={"enable_thinking": False},
    stream=True,
)
```

新版用 `infra/llm/qwen_client.py` 封装，default extra_body 内置此项，业务代码不能漏。

### 2.2 流式响应

Qwen DashScope 兼容 OpenAI ChatCompletion stream，但有 vendor quirks：

- 首个 chunk 可能空内容（只带 role）
- 结束 chunk usage 字段在 `chunk.usage`，可能为 None
- 错误事件 `chunk.error` 字段可能出现，要在循环里检

封装层处理掉这些差异，向 service 层只暴露 `AsyncIterator[str]`。

---

## 3. Prompt 框架

### 3.1 分段组装

```
system = [
    BASE_PERSONA,
    TIME_CONTEXT,                    # 当前时间，避免"昨天/今天"歧义
    PROFILE_SECTION,                 # 【你大概知道用户】
    RELATIONSHIPS_SECTION,           # 【你大概认识这些人】
    EVENTS_SECTION,                  # 【最近发生过】
    EXPLICIT_MEMORIES_SECTION,       # 【本轮可以提及的具体记忆】
    BACKGROUND_MEMORIES_SECTION,     # 【你大致还记得这些，作为背景理解】
    TURN_RULES_SECTION,              # 【本轮】intent + 安全提醒
    INTENT_GUIDES[intent],           # 怎么"接住"用户
    PERSONALITY_CONTRACT[personality],  # 字数/反问/引用
    HARD_RULES,                      # 红线
]
```

### 3.2 空段不渲染

每段在内容空时跳过整个块——不要 "【XXX】：（无）" 占位（噪音 + 多 token + 给 LLM 暗示"该提"）。

### 3.3 结构化输出

需要 LLM 返回 JSON 时：

1. 强制 `response_format={"type": "json_object"}`（如果 vendor 支持）
2. Prompt 末尾必须写"只输出 JSON，不解释，不思考过程"
3. Pydantic 解析，schema_version 字段
4. 解析失败兜底（不抛 500）

### 3.4 字数估算

每个段落记录 token 估算（用 tiktoken / qwen tokenizer），便于：

- 限制总长（> 8k 时砍 background 段）
- 评测时报告 cost

---

## 4. PromptPack / PromptMeta

```python
class PromptPack(BaseModel):
    system: str
    sections: dict[str, str | None]   # 各段独立保存，方便 L0 检查
    estimated_tokens: int
    meta: PromptMeta

class PromptMeta(BaseModel):
    schema_version: int = 1
    composed_at: datetime
    model: str
    route: MemoryRoute
    context_layers: ContextLayersSummary
    activated: list[MemoryRef]
    snapshot_stats: SnapshotStats
    system_excerpt: str    # 仅前 500 字，避免存全文
    llm_request: LLMRequestRef  # model/temperature/stop tokens
```

> **重要**：`PromptMeta.system_excerpt` 而非整段 `system`，避免 Message.meta_json 爆炸。
> 真要全文则单独 export 接口生成。

---

## 5. 错误处理

### 5.1 异常分类

| 异常 | 处理 |
|---|---|
| LLMRateLimitError | 重试 3 次（指数退避），失败后向用户返回友好提示 |
| LLMTimeoutError | 同上 |
| LLMInvalidJSONError | 结构化任务降级到兜底（intent=casual, action=audit_only） |
| LLMContentFilterError | 向用户返回中性"抱歉这话题我跳过" |
| LLMNetworkError | 同 rate limit |

### 5.2 流式中错误

如果流到一半挂了：

- 已 yield 的 tokens 拼成 partial reply
- 落 message 时 error=具体异常
- 前端展示 "[内容中断，请重发]"
- L1 规则 `error_reply` 会捕获

### 5.3 重试策略

```python
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8),
       retry=retry_if_exception_type((LLMRateLimitError, LLMTimeoutError)))
async def call_llm(...): ...
```

---

## 6. 成本与可观测

### 6.1 trace 字段

每次 LLM 调用记录：

```jsonc
{
  "trace_id": "...",
  "user_id": "...",
  "role": "chat" | "structured",
  "model": "qwen3.7-max",
  "input_tokens": 1234,
  "output_tokens": 89,
  "latency_ms": 1023,
  "cost_usd": 0.0021,
  "success": true,
  "extra": {...}  // 比如 intent_classifier 的输入输出 hash
}
```

落到 SQLite 表 `llm_traces` 或外部 logging。

### 6.2 cost 估算

```python
def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = COST_TABLE[model]
    return input_tokens / 1000 * pricing.input + output_tokens / 1000 * pricing.output
```

每日聚合：
- 单用户日 cost
- 单 intent 日 cost
- 单模型日 cost

---

## 7. 单元测试策略

### 7.1 不调真实 LLM

所有 service 层测试用 `MockLLMClient`：

```python
class MockLLMClient:
    def __init__(self, responses: list[str | dict]): ...
    async def stream(self, ...): for r in self.responses: yield r
    async def complete_json(self, ..., schema): return schema(**self.responses.pop(0))
```

### 7.2 黄金样本对比

prompt_composer 的输出走"golden snapshot" 对比：

```python
def test_compose_casual_balanced():
    actual = compose(make_ctx("casual", "balanced", ...))
    snapshot = load_snapshot("compose_casual_balanced.txt")
    assert actual == snapshot
```

更新 snapshot 走 `pytest --snapshot-update`，PR 时 reviewer 看 diff。

---

## 8. 调试支持

### 8.1 "Why this reply" 调试接口

```
GET /api/dev/debug/messages/{message_id}
→ {
  "message": {...},
  "prompt_meta": {...},
  "rendered_system": "<完整 system 文本>",
  "llm_traces": [...]
}
```

DEV_MODE 下可调，生产关。

### 8.2 重放接口

```
POST /api/dev/debug/replay
{"message_id": "xxx", "override": {"model": "qwen-max"}}
→ 用同一份 prompt 换模型重跑，对比新旧 reply
```

用于测试模型升级。

---

## 9. 安全

| 项 | 措施 |
|---|---|
| API key 泄露 | env var 注入，不入仓 |
| Prompt injection | 用户消息前加 "用户原话：" 包装，明确边界 |
| 越权调用 | 所有 LLM 调用走 LLMRouter，无法绕过限流 |
| 敏感内容 | 由 LLM vendor 自带内容过滤 + 应用层捕获 ContentFilter |

---

## 与 legacy MindMem v0.97 的差异

| 项 | legacy MindMem v0.97 | MindEngine |
|---|---|---|
| LLM 调用入口 | 散在 5+ 个 .py 文件 | 统一 LLMRouter |
| enable_thinking | 后期补丁式加 | 默认内置在 client |
| JSON 兜底 | 散在各 service 自己写 | client.complete_json 统一处理 |
| Cost 追踪 | 无 | trace 表 |
| 重试 | 各处实现各异 | tenacity 装饰器统一 |
| Prompt 段化 | 拼接字符串 | sections 字段化 + 黄金样本 |
