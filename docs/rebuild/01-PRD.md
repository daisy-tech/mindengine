# MindEngine · 产品需求文档（PRD）

> 版本：rebuild · v1.0 · **MindEngine**
> 行为基准：legacy MindMem v0.97 参考实现
> 受众：产品 / 工程负责人 / MindEngine 重构实施者

---

## 1. 一句话

**MindEngine 是一个会"长出记忆"的聊天伙伴**：你和**小白**聊得越多，它越像一个真实记得你的朋友，而不是每次对话从零开始的工具。

它不是 ChatGPT 包装层 —— 区别在于"**记忆是一等公民**"：每次回复前，AI 都在做一道"我现在该想起什么"的判断题。

---

## 2. 目标用户与场景

### 2.1 用户画像

- 想要一个**能记住自己的 AI**，不只是工具
- 接受"AI 偶尔记错，但能纠正"，不接受"AI 永远健忘"
- 在意"AI 是不是真的懂我"，会主动测它的记忆（"你还记得我老家在哪吗"）

### 2.2 典型场景

| 场景 | 用户期望 |
|---|---|
| 第一次聊 | 自然问候，不强行套近乎；可以问一些事实（姓名、所在地） |
| 重复访问 | "上次我们聊到 X" 不要每次都说；该提的时候提 |
| 情绪低落时 | 不要"加油 / 你能行"鸡汤；不要立刻给建议；先承接感受 |
| 测记忆 | 真记得就具体说出来；不确定就承认"我记不太清，是 X 吗"；**绝不编造** |
| 纠正 AI 错误 | AI 立刻承认 + 复述正确事实；不要"哦哦下次注意"；不要继续重复错误信息 |
| 谈论关系人（妻子/儿子/同事） | AI 应该记得这些关系；提到时不需要每次解释"你妻子叫什么" |
| 知识性问题（如何泡茶） | 像知乎专家，不要"我帮你查查"绕弯，直接给步骤 |

### 2.3 反目标（不做的事）

- ❌ 不做日程提醒、不发主动 push（不是 Siri / 不是 Replika）
- ❌ 不做多模态（图片 / 语音 / 视频）—— v1 只做文字
- ❌ 不做多用户共享记忆（每个账号完全独立）
- ❌ 不做付费墙 / 商业化 —— 当前阶段是个人 PoC

---

## 3. 产品功能清单

### 3.1 核心功能（必须做）

| ID | 功能 | 一句话描述 |
|---|---|---|
| F-01 | 流式聊天 | 用户发消息 → 服务端 SSE 实时吐字 → 回复完成后异步抽取记忆 |
| F-02 | 四层记忆 | profile / event / episodic / relationship 分层存储，各有读写规则 |
| F-03 | 智能记忆装填 | 每轮回复前自动判断"该装哪几层记忆"，避免上下文爆炸 |
| F-04 | 三种人格 | 内向 / 中性 / 外向，体现在回复字数、反问数、是否主动引用记忆 |
| F-05 | 多会话管理 | 用户可建多个会话，每个会话独立上下文 |
| F-06 | 在线记忆纠错 | 用户发现 AI 记错时直接说"不对，是 X"，AI 立刻修正并永不再说错 |
| F-07 | 记忆透明度 | 每条 AI 回复都能"穿透"看本轮用了哪些记忆、为什么 |
| F-08 | 评测实验室 | 合成 case 评测 + 真实聊天评估，量化"AI 是否真的在进步" |

### 3.2 二级功能（v1 应有）

| ID | 功能 |
|---|---|
| F-10 | 用户画像直接编辑（手动改姓名/职业等） |
| F-11 | 社会关系图谱可视化（用户和谁、谁和谁的关系） |
| F-12 | 长期事件时间线 |
| F-13 | 会话标题自动生成（首轮后） |
| F-14 | 导出某次会话的"评估包"（含审计元数据） |

### 3.3 工程功能（隐藏在功能里）

| ID | 功能 |
|---|---|
| E-01 | 每条 assistant 消息附带 `prompt_meta`（路由决策 / 记忆快照 / LLM 请求） |
| E-02 | 异步抽取记忆（不阻塞回复） |
| E-03 | 异步更新画像（合并新信息到旧画像） |
| E-04 | 记忆纠错的 LLM 判断（小模型判断 + 阈值过滤 + 软删） |
| E-05 | 实体硬封禁（被纠正过的实体不再写入也不再召回） |

---

## 4. 功能详细需求

### 4.1 F-01 流式聊天

**输入**：用户消息 + 会话 ID + 当前人格选择

**输出**：SSE 流，含
- `delta`：每个 token / 字
- `final`：包含完整 reply + `prompt_meta`

**关键约束**：
- 首字延迟 ≤ 800ms（含 Memory Router 路由 + intent 分类）
- 不展示思考过程（`enable_thinking=False`）
- 网络断了能重试（前端 ReadableStream，非 EventSource —— 因为历史长后 URL 会超长）

### 4.2 F-02 四层记忆

每层的"形状"和职责完全不同：

| 层 | 存储 | 写入时机 | 读取时机 | 例子 |
|---|---|---|---|---|
| **profile** | SQLite JSON | 异步任务（Celery） | 几乎每次 | 姓名、出生年、所在地、职业 |
| **event** | SQLite | 异步任务 | 按 intent | "上周和儿子吃饭"、"今天面试" |
| **episodic** | Mem0 + Qdrant | 异步任务 | 按 intent | 散在对话里的零碎事实片段 |
| **relationship** | SQLite | 异步任务 | 关系话题时 | "妻子叫张三"、"儿子上小学" |

**写入分工**：
- 同步任务（chat handler 之内）：**只**写 SQLite 的 Message / Conversation
- 异步任务（Celery worker）：写四层记忆，避免阻塞流式回复

### 4.3 F-03 智能记忆装填（Memory Router v1.5）

**问题**：直接把全部记忆塞进 prompt → 上下文爆炸 + 噪音稀释 + 成本爆炸

**解法**：三层路由
1. **硬规则**：识别 `correction` / `knowledge_task` 等明显意图
2. **小模型 intent 分类**（Qwen3.7-plus）：补充判断 `casual` / `emotional_support` / `memory_challenge` 等
3. **策略查表**：根据 intent 决定"该装哪几层、最多几条、是否敏感模式"

**关键决策**：
- intent 分类必须**结构化输出 JSON**，不能让 LLM 自由发挥
- 硬规则优先级最高（避免小模型把"是怀宁不是岳西" 错判为 casual 而漏掉 correction）

详见 [03-Subsystem-Memory-Router.md](./03-Subsystem-Memory-Router.md)

### 4.4 F-04 三种人格

不是"语气贴纸"——是**可执行的行为契约**：

| 人格 | 总长 | 反问 | 主动引用记忆 |
|---|---|---|---|
| 内向 | ≤30 字 | 不主动反问 | 不主动；用户邀请才提 |
| 中性 | ≤60 字 / 2-3 句 | 至多 1 开放反问 | 相关时引 1 条带不确定性 |
| 外向 | ≤90 字 / 2-4 句 | 至多 1 反问 + 1 建议 | 主动引 1 条 + 给建议 |

**关键决策**：
- 契约要写成 LLM 看得懂的硬指标（"≤30 字"），不是"克制、温柔"这种形容词
- 不同 intent 有不同子分支（普通 / 质问记忆 / 纠错 / 情绪 4 种）
- 敏感场景（如情绪支持）也要注入契约，不要因为"敏感场景被覆盖"而让人格消失

详见 [04-Subsystem-Personality.md](./04-Subsystem-Personality.md)

### 4.5 F-06 在线记忆纠错

**触发**：intent classifier 把当前消息识别为 `correction`

**流水线**：
1. 抽取纠错目标（"安徽岳西不是怀宁" → 目标实体="岳西"）
2. 按目标在四层记忆里搜候选
3. 小模型 LLM 判断每个候选：`deprecate` / `update` / `audit_only`（仅审计，置信度低）
4. 高置信度 → 软删 + 落 `memory_deprecations` 表
5. 抽取"被否定的实体"加入用户 banned_entities
6. **同一轮内跳过** `extract_*` 异步任务，避免把错误信息再次入库

详见 [06-Subsystem-Correction.md](./06-Subsystem-Correction.md)

### 4.6 F-07 记忆透明度

每条 assistant 消息都附带 `prompt_meta`：

```yaml
prompt_meta:
  composed_at: 2026-06-07T11:24:00Z
  model: qwen3.7-max
  route:
    intent: memory_challenge
    intent_source: small_model
    intent_confidence: 0.92
    memory_depth: focused
    personality: extrovert
    sensitive_mode: true
    max_explicit_memories: 3
    load_layers: [profile, relationships, events, episodic]
  context_layers:                # 路由决定后实际装填的池子
    stable_profile: [...]
    relevant_relationships: [...]
    relevant_events: [...]
    relevant_memories: [...]
    background_only: [...]
  activated: [...]               # 渲染到 system 段的记忆并集（含 background）
  snapshot_stats:                # 池子大小汇总，用于评估时无需重放
    profile_total: 12
    episodic_total: 78
    banned_entities: ['小鹏']
  system: "<完整 system prompt 文本>"
  llm_request:
    model: qwen3.7-max
    messages: [...]
```

**用途**：
- 前端"穿透"面板展示
- 真实聊天评估时不重放 Router（直接读 `snapshot_stats`，省 tokens）
- 故障排查（"那次 AI 为什么不记得猫"可定位到具体一轮 prompt）

### 4.7 F-08 评测实验室

两套互补的评测：

**A. 合成评测**：手写 case（smoke 20 / full 50），每 case 含 (history, message, expected)，跑全流程 chat → 与 expected 对比 → 出报告

**B. 真实聊天评估**：拿用户真实历史会话的 `chat_audit_v1` 包，跑 L0 结构检查 + L1 启发式规则 + 归因，0 LLM 成本

详见 [07-Subsystem-Eval-Lab.md](./07-Subsystem-Eval-Lab.md)

---

## 5. 非功能需求

### 5.1 性能

| 指标 | 目标 | 备注 |
|---|---|---|
| 首字延迟 | ≤ 800ms | 含 intent 分类 |
| Chat 接口总耗时（30 字 reply） | ≤ 3s | |
| 评测实验室真实聊天评估 | ≤ 50ms | 命中磁盘缓存时 |
| Celery 抽取记忆延迟 | ≤ 10s | 不阻塞用户 |

### 5.2 数据一致性

- 用户改名后，下一轮回复立即用新名字
- 用户纠错后，下一轮该错误**绝不**再出现
- 异步任务失败要有重试和告警

### 5.3 可观测性

- 每个 LLM 调用都要有 trace（model / tokens / latency / cost）
- 每个 Celery 任务都要有状态（pending / running / success / fail）
- 每个用户的"记忆健康度"可一键检查（profile 完整度 / episodic 数量 / banned 数量）

### 5.4 安全 & 合规

- 用户数据完全隔离（按 user_id 切分，cross-user 查询是 bug）
- 默认不收集任何日志到外部
- 敏感信息（健康 / 财务 / 家庭矛盾）即便用户提了，AI 也不主动复述

---

## 6. 用户旅程（典型 7 天）

```
Day 0  注册 → 默认中性人格 → 自我介绍（姓名/职业/所在地）
       → 4 层记忆只写了 profile，event/episodic/relationship 都还空

Day 1  聊一些日常生活 → episodic 开始填充
       → 提到家人 → relationship 创建节点
       → profile 已有 4-6 条事实

Day 2  情绪支持场景：AI 应承接，不出"加油"鸡汤
       → 若违反，用户在前端打"差评"
       → 评测实验室能从 chat_audit 抓到这次违规

Day 3  开始测记忆："你记得 X 吗"
       → AI 召回 → 用户感受到"它真记得"
       → 若错则纠错 → correction pipeline 触发

Day 4  尝试切换到外向人格
       → 对比内向 / 外向回复差异
       → 用户感受到契约真的生效

Day 5+ 长期使用 → event 时间线累积 → 用户能看到"我和 AI 共同的记忆轨迹"
```

---

## 7. v1.0 验收标准

| # | 验收项 | 通过条件 |
|---|---|---|
| 1 | 四层记忆都跑通 | 创建一个新用户，聊 10 轮，4 层都有数据 |
| 2 | Memory Router 不会漏 correction | 给 5 句明显纠错语，5/5 识别为 correction |
| 3 | 人格契约真的生效 | 三种人格各跑 10 轮，回复字数中位数差异 ≥ 30 字 |
| 4 | 在线纠错有效 | 让 AI 说错老家，纠正后再问 5 次，5 次都说正确答案 |
| 5 | 评测实验室能跑 | smoke 20 通过率 ≥ 90% |
| 6 | 真实聊天评估对齐 | 同一份 `chat_audit_v1` 跑 review 字段完全一致 |
| 7 | 自测通过 | `selftest.py` 100+ 项全过 |
| 8 | 小白品牌一致 | prompt 自称「小白」、前端显示「小白」、头像与 legacy 源文件一致 |

---

## 8. 已知边界 & 后续版本

| 范围 | v1.0 现状 | v2 计划 |
|---|---|---|
| 多模态 | 不做 | 可考虑加图片输入 |
| 主动消息 | 不做 | 可考虑"AI 主动问候"开关 |
| 多账号共享 | 不做 | 可考虑"夫妻共享"或"家庭账号" |
| LLM Judge 评测 | 不做（成本） | v1.3 对启发式 fail 的 turn 调小模型语义判分 |
| 跨会话趋势 | 不做 | v1.3 用户级评分卡 |

---

## 9. 关键决策回顾（来自 v0.97 教训）

> 详见 [10-Lessons-Learned.md](./10-Lessons-Learned.md)。这里列影响**产品形态**的几个：

1. **每个 assistant 消息必带 prompt_meta** —— 不是 nice-to-have，是评测体系的基石
2. **人格契约 ≠ 人格描述** —— 必须是可执行的硬指标
3. **纠错不能只标"不再用"，要硬封禁实体** —— 否则 LLM 又从其他记忆里推出来
4. **评测实验室必须前端可视化** —— 不能只产 JSON 让人 grep
5. **Memory Router 的 query 不要拼太多历史轮** —— 会稀释向量召回信号
