# 子系统 · Memory Router v1.5

> 责任：在每轮回复前，决定"这一轮该装哪几层记忆、最多几条、是否敏感模式"。
> 输出：`MemoryRoute` 对象。

> 🔄 **修订 v1.1（2026-06）**：
> - §4.4：**intent 历史窗口口径统一为"最近 2 轮 user+assistant"**（决策 D8，原文档 2/2-3 轮不一致）。注意这与 §5.4 的"episodic query 只用当前消息"是**两件事**：前者喂给分类器判 intent，后者喂给向量检索，不要混。
> - §8：首字延迟拆**显式预算表 + 投机加载 + intent 缓存**（决策 D4，800ms 偏紧且 intent 在关键路径串行）。
> - §5.2 后追加渐进启用提示（决策 D8，见 [05](./05-Subsystem-Memory-Layers.md) §11）。

---

## 1. 三层架构

```
┌──────────────────────────────────────────┐
│ Layer 1: 硬规则（rules.py）                │
│   - 关键词 / regex 命中 → 直接返回          │
│   - 覆盖 correction / knowledge_task 等     │
│     极典型句式，不让小模型抢                  │
└──────────────────────────────────────────┘
                  │ 未命中
                  ▼
┌──────────────────────────────────────────┐
│ Layer 2: 小模型 intent 分类                │
│   - qwen3.7-plus (INTENT 常量/同档结构化)     │
│   - JSON 输出，temperature=0                │
│   - 输入：当前消息 + 最近 2-3 轮            │
│   - 输出：intent + confidence + reason      │
└──────────────────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────┐
│ Layer 3: 策略查表（policy.py）             │
│   intent → MemoryRoute                    │
│   {depth, load_layers, max_explicit,      │
│    sensitive_mode, event_policy, ...}     │
└──────────────────────────────────────────┘
                  │
                  ▼
              MemoryRoute
```

---

## 2. Intent 体系（v1 共 9 类）

| Intent | 描述 | 典型句式 |
|---|---|---|
| `casual` | 闲聊 | "你好"、"今天天气真好"、"嗯嗯" |
| `self_summary` | 自我总结 | "你都知道我什么"、"总结下我的情况" |
| `memory_challenge` | 质问记忆 | "你记得 X 吗"、"我喜欢什么 Y" |
| `relationship_topic` | 关系话题 | "我妻子最近很累"、"我儿子今天" |
| `emotional_support` | 情绪支持 | "好烦"、"心情不好"、"压力大" |
| `plan_followup` | 计划跟进 | "上次说的 X 怎么样了"、"那个面试..." |
| `preference_request` | 偏好建议 | "你有什么好推荐的"、"你觉得 X 怎么样" |
| `correction` | 纠错 | "不对，是 X"、"我记错了" |
| `knowledge_task` | 知识/工具 | "如何泡茶"、"什么是 OKR" |

**为什么是 9 类**：少一类会模糊 LLM 决策；多一类边界更不清。9 类是 v1.5 多轮调试出来的稳定值。

**何时该考虑加新类**：v0.97 没踩到，但可能场景：
- `task_planning`（"帮我规划周末"）—— 现在归入 preference_request
- `meta_question`（"你怎么记忆"）—— 现在归入 casual
- 若评测发现某子类频繁误判到通用类，再独立出来

---

## 3. Layer 1：硬规则

### 3.1 设计原则

- **只覆盖极典型句式**——能保证 99%+ 准确，否则交给小模型
- **错放对（漏报）允许，错放错（误报）不允许**——硬规则一旦命中就是终决
- 用正则 + 关键词词表，不引入第三方分词器

### 3.2 推荐覆盖

| Intent | 关键词/正则 |
|---|---|
| `correction` | `^(不对\|错了\|不是\|你记错\|我记错)` 或 `应该是.*不是` 等 |
| `knowledge_task` | `如何\|怎么\|什么是\|是什么\|为什么` 开头 + 不含人称代词 |
| `memory_challenge` | `(还)?记得\|知道我.*吗\|猜.*我.*吗` |

### 3.3 实现要点

```python
@dataclass
class HardRuleHit:
    intent: str
    confidence: float = 1.0
    reason: str = ""

def match_hard_rule(message: str) -> HardRuleHit | None:
    for rule in HARD_RULES:
        if rule.matches(message):
            return HardRuleHit(intent=rule.intent, reason=rule.name)
    return None
```

---

## 4. Layer 2：小模型 intent 分类

### 4.1 模型与参数

| 项 | 值 |
|---|---|
| Model | `qwen3.7-plus` (DashScope) |
| Temperature | 0 |
| Top-P | 1.0 |
| Max tokens | 200 |
| `enable_thinking` | **false**（必须） |
| 超时 | 5s |
| 失败兜底 | intent=`casual`, confidence=0.0 |

### 4.2 Prompt（推荐结构）

```
你是 intent 分类器。把用户消息归类到以下 9 类之一：
- casual: 闲聊、寒暄、表情
- self_summary: 用户让你总结他的信息
- memory_challenge: 用户在测你的记忆
- relationship_topic: 谈论某个具体的人
- emotional_support: 表达情绪（负面为主）
- plan_followup: 询问之前事项进展
- preference_request: 要求推荐/建议
- correction: 纠正你给的错误信息
- knowledge_task: 询问通用知识或工具用法

输出严格 JSON：
{"intent": "<class>", "confidence": <0-1>, "reason": "<10 字内>"}

不要解释，不要 markdown，不要思考过程。

历史最近 2 轮：
{history}

用户当前消息：
{message}
```

### 4.3 JSON 解析要鲁棒

- 解析失败兜底 `{intent: "casual", confidence: 0.0}`
- 模型偶尔会用 ` ```json {...} ``` ` 包裹，要剥
- intent 不在 9 类内则兜底 `casual`

### 4.4 历史窗口（🔄 v1.1 统一口径）

**intent 分类**喂 **最近 2 轮 user+assistant**（足够判断上下文，不要塞整个会话——成本+稀释）。

> 🔄 口径统一：本节、§4.2 prompt 模板、intent cache key 一律按"最近 2 轮"。**勿与 §5.4 混淆**——§5.4 是 episodic 向量检索的 query 构建，只用当前消息（必要时拼上一句），与喂分类器的历史窗口是两条独立逻辑。

---

## 5. Layer 3：策略查表

### 5.1 输出对象

```python
class MemoryRoute(BaseModel):
    intent: str                     # 9 类之一
    intent_source: Literal["hard_rule", "small_model", "fallback"]
    intent_confidence: float
    memory_depth: Literal["minimal", "safe_focused", "focused", "wide"]
    load_layers: list[str]          # 子集 of ["profile_basic","profile","relationships","events","episodic"]
    sensitive_mode: bool
    max_explicit_memories: int      # 上限 0-5
    event_policy: Literal["none","summary","background_pain_points"]
    personality: Literal["introvert","balanced","extrovert"]
    query: str                      # 用于 episodic 检索的查询串
    reasons: list[str]              # 调试用决策痕迹
```

### 5.2 策略表（按 intent）

| intent | depth | load_layers | max_explicit | sensitive | event_policy |
|---|---|---|---|---|---|
| casual | minimal | profile_basic, episodic | 0-1 | F | none |
| self_summary | wide | profile, relationships, events, episodic | 5 | F | summary |
| memory_challenge | focused | profile, relationships, events, episodic | 3 | T | summary |
| relationship_topic | focused | profile_basic, relationships, events, episodic | 1-2 | F | summary |
| emotional_support | safe_focused | profile_basic, episodic | 1 | T | background_pain_points |
| plan_followup | focused | profile_basic, events | 1-2 | F | summary |
| preference_request | focused | profile_basic, episodic | 2 | F | summary |
| correction | safe_focused | profile_basic, episodic | 2 | T | summary |
| knowledge_task | minimal | profile_basic | 0 | F | none |

> 这是 v0.97 验证过的稳定值。新版可微调，但**先复刻一致再说**。
>
> 🔄 v1.1（决策 D8）：M2 阶段可**先启用高频 4-5 个 intent + 2 档 depth** 打通闭环，其余按评测数据渐进加；严格复刻全 9 类是 M4 目标。详见 [05-Subsystem-Memory-Layers.md](./05-Subsystem-Memory-Layers.md) §11。

### 5.3 `sensitive_mode` 用途

- **不**再禁止人格契约注入（v0.97 的错误，详见 [10-Lessons-Learned.md](./10-Lessons-Learned.md) §3.1）
- 但用于：
  - 决定 prompt 里要不要加"敏感场景：执行人格契约里对应子分支"提示行
  - 控制 max_explicit 上限不被人格 override 推得太高

### 5.4 query 构建

**v0.97 教训**：原本拼 last 3-5 user message → 召回稀释。

**新版**：

```python
def build_query(message: str, history: list[Msg]) -> str:
    # 1) 去掉问候/表情/语气词
    cleaned = strip_chitchat(message)
    # 2) 提取人物/主题（可用 spaCy / 简单关键词）
    subjects = extract_subjects(cleaned)
    # 3) 如果当前消息很短（"嗯"、"哦"），才用上一句 user 拼
    if len(cleaned) < 4 and history:
        cleaned = history[-1].content + " " + cleaned
    return cleaned
```

---

## 6. 与人格的关系

| 项 | 谁决定 |
|---|---|
| `intent` | router |
| `personality` | 用户前端设置（每会话独立） |
| `max_explicit_memories` | router 策略表（intent） × personality 倍率 |
| `prompt 里的"反问/字数"` | personality contract（不在 route 里） |

**注意**：router 的策略表给的是"上限"，最终是否真的引用记忆由 LLM 看完 prompt 决定。

---

## 7. 实现要点（给重构者）

### 7.1 模块结构

```
services/memory_router/
├── __init__.py
├── domain.py          # MemoryRoute / MemoryDepth / etc.
├── rules.py           # 硬规则
├── intent_classifier.py  # 小模型分类
├── policy.py          # 策略查表
└── router.py          # 组合：rules → classifier → policy
```

### 7.2 依赖注入

```python
class MemoryRouter:
    def __init__(self, classifier: IntentClassifier, policy: Policy):
        self.classifier = classifier
        self.policy = policy

    async def route(
        self, message: str, personality: str,
        history: list[Msg] | None = None,
    ) -> MemoryRoute:
        # Layer 1
        if hit := match_hard_rule(message):
            return self.policy.build_route(
                intent=hit.intent, source="hard_rule",
                confidence=hit.confidence, personality=personality,
                message=message, history=history or [],
            )
        # Layer 2
        try:
            cls = await self.classifier.classify(message, history)
        except (TimeoutError, ValueError):
            cls = ClassifyResult(intent="casual", confidence=0.0)
        # Layer 3
        return self.policy.build_route(
            intent=cls.intent, source="small_model",
            confidence=cls.confidence, personality=personality,
            message=message, history=history or [],
        )
```

### 7.3 测试覆盖（必须）

| Case 类型 | 数量 | 验证 |
|---|---|---|
| 硬规则命中 | 每个 intent ≥ 3 | 命中即返回，不调 classifier |
| 小模型路径 | 每个 intent ≥ 3 | mock classifier 输出，校验 route 字段 |
| 兜底 | 1 | classifier 抛错 → casual / 0.0 |
| Personality 影响 | 3 | 同 intent 不同 personality，max_explicit 是否一致 / 应一致还是变化 |
| Query 构建 | 5 | 短消息、长消息、含表情、含主题词 |

---

## 8. 性能与成本

### 8.1 router 自身开销

| 阶段 | 期望 |
|---|---|
| Layer 1（regex）| < 1ms |
| Layer 2（LLM）| ≤ 300ms p95 |
| Layer 3（dict lookup）| < 1ms |
| 总 router 开销 | ≤ 350ms p95 |

成本：单轮 ~150 input tokens + ~30 output tokens × `INTENT` 模型 ≈ $0.0001 / 轮

### 8.2 🔄 首字延迟预算（v1.1 · 决策 D4）

**问题**：PRD §5.1 首字 ≤ 800ms。但 `load_layers` 依赖 intent，所以 **intent LLM 调用卡在关键路径上串行**（hard rule 未命中时）：intent(300ms) → 记忆加载 + 向量检索 → compose → 主聊 TTFT。串起来 800ms 很紧。把总预算拆开看：

| 阶段 | p50 预算 | p95 预算 | 说明 |
|---|---|---|---|
| Layer 1 硬规则 | <1ms | <1ms | 命中则跳过 intent LLM |
| Layer 2 intent LLM | 150ms | 300ms | 未命中硬规则时 |
| 记忆加载（4 层并发 + pgvector 检索）| 40ms | 120ms | asyncio.gather |
| compose | <5ms | 10ms | 纯字符串 |
| 主聊 TTFT | 250ms | 450ms | 取决于 `CHAT` 模型 |
| **合计（硬规则命中）** | ~300ms | ~580ms | 跳过 intent LLM |
| **合计（走 intent LLM 串行）** | ~450ms | ~880ms | ⚠️ p95 略超 800ms |

### 8.3 🔄 优化手段（按收益排序）

1. **硬规则尽量多覆盖典型句**：命中即省掉 intent LLM 那 150-300ms（已是设计原则，§3）。
2. **intent 缓存**（决策 D4）：intent 分类是确定性纯函数（temperature=0），`INTENT_CACHE_ENABLED=true` 生产可安全开。key = `sha1(normalized_message + last_2_turns)`。命中直接省 Layer 2。
3. **投机加载（speculative load）**：未命中硬规则时，**不等 intent 返回**，先用一个"保守超集 `load_layers`"（如 profile_basic + episodic top-k）并发预拉记忆；intent 回来后按真实 route 裁剪/补拉。把"向量检索墙钟"与"intent LLM 墙钟"重叠，p95 可压回 800ms 内。
   - 代价：偶尔多拉一点点记忆（被裁掉），成本可忽略。
   - 实现：`asyncio.gather(intent_task, speculative_load_task)`，intent 到达后 reconcile。
4. **本地小分类器（v2 候选）**：embedding/小模型本地分类，进一步砍 intent 延迟（见 §11 FAQ）。

---

## 9. 灰度与回滚

- 环境变量 `INTENT_CLASSIFIER_ENABLED=false` → 退化为"仅硬规则 + 兜底 casual"
- 完全关掉 Memory Router → 退化为 v1.0 模式（所有 intent 都 casual）
- 灰度发布建议：先开 100% 但 confidence < 0.6 的路由强制 casual fallback

---

## 10. 与 legacy MindMem v0.97 的差异

| 项 | legacy MindMem v0.97 | MindEngine |
|---|---|---|
| 硬规则与小模型顺序 | ✅ 硬规则优先 | 保留 |
| query 拼接历史轮数 | 1（修复后） | 保留 1，但增加"短消息时拼一句"逻辑 |
| Intent 数量 | 9 | 保留 9 |
| `MemoryRoute` 字段 | 大量字段散在 prompt_meta.route | 用 Pydantic 类，schema_version 演化 |
| 测试 | selftest_p0.py 里散落 | 独立 tests/unit/memory_router/ |

---

## 11. FAQ

**Q：为什么不用一个大模型做端到端 + tool call？**
A：成本高、延迟高、不可控。intent 是分类任务，小模型 + 结构化输出最稳。

**Q：Confidence 阈值要多少？**
A：v0.97 没强制阈值，让所有 intent 直接进策略表。新版可在 < 0.5 时降级到 casual（更保守），但务必收集数据后再决定。

**Q：能用 embedding 分类吗？**
A：可以，但需要训练数据。v1 用 LLM 分类是为了零训练成本。embedding 是 v2 候选优化。
