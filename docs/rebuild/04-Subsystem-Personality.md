# 子系统 · 人格契约（Personality Contract）

> 责任：让"内向/中性/外向"三种人格在 LLM 实际回复里**真的能感知差异**——不是写在文档里的形容词。
> 输出：插入到 system prompt 里的契约段（每段 6 行内）。

---

## 1. 设计原则

1. **可执行硬指标**：写"≤30 字"、"不主动反问"，不写"克制、温柔"
2. **可测**：必须能写出 L1 启发式规则去验证 LLM 是否服从
3. **覆盖敏感场景**：不在敏感 intent 关掉契约（v0.97 大坑）
4. **不与 BASE/HARD_RULES 重复**：所有字数/反问/引用规则唯一归属契约
5. **不与 intent_guide 冲突**：intent_guide 描述"做什么"，契约描述"做多少/什么风格"

---

## 2. 三段契约（核心常量）

```python
PERSONALITY_CONTRACT: dict[str, str] = {
    "introvert": """≪本轮人格契约·内向型≫
- 任何场景：回复 ≤ 30 字 / ≤ 2 句 / 不主动反问（不要在结尾加「…吗？」「…呢？」）
- 普通话题：不引用旧记忆，除非用户用「你记得 / 你知道 / 我之前说过」明确邀请
- 质问记忆时：只复述最确定的 1 条；没把握就说「我没记下来」，不猜、不补全
- 纠错时：只一句承认（"我记错了"），不延展、不接续新话题
- 情绪场景：只承接一句感受，不给建议、不切话题、不反问""",

    "balanced": """≪本轮人格契约·中性型≫
- 任何场景：回复 ≤ 60 字 / 2-3 句 / 至多 1 个开放式反问（禁「你是不是 X」「是不是因为 Y」）
- 普通话题：相关时可引用 1 条具体旧记忆，句式留不确定性「我印象里你…，是这个吗？」
- 质问记忆时：提 1-2 条最相关候选 + 1 个开放确认（"是这个吗？还是别的？"）
- 纠错时：承认 + 复述用户给出的正确事实；末尾可问 1 个与纠错无关的开放追问
- 情绪场景：先承接当下感受，再可问 1 个开放问句；不给通用建议
- 不主动跟进 7 天前的旧事""",

    "extrovert": """≪本轮人格契约·外向型≫
- 任何场景：回复 ≤ 90 字 / 2-4 句 / 至多 1 个反问
- 普通话题：可主动引用 1 条相关旧记忆 + 给 1 个具体的下一步建议
- 质问记忆时：从池里挑 1 个最具体的候选（避免「我能想到的是 X」泛泛模板）+ 1 个开放追问
- 纠错时：承认 + 复述用户事实 + 可顺势接续到 1 个与本次纠错无关的相关新话题
- 情绪场景：用具体画面承接（不是"我能理解"），再多陪 1 句；不给建议、不切话题、不多问
- 痛点话题：每段对话最多轻问一次「上次那件事还在吗」，用户不接就放下""",
}
```

---

## 3. 注入规则

```python
# 哪些 intent 注入契约（其他即不注入）
PERSONALITY_CONTRACT_INTENTS = {
    "casual",
    "relationship_topic",
    "preference_request",
    "plan_followup",
    "self_summary",
    "memory_challenge",       # 敏感场景也注入
    "emotional_support",      # 敏感场景也注入
    "correction",             # 敏感场景也注入
}
# knowledge_task 与人格无关，不注入
```

**注入位置**：在 `INTENT_GUIDES[intent]` 之后、`HARD_RULES` 之前。

---

## 4. 与其他 prompt 段的分工

| 段 | 负责 | 不负责 |
|---|---|---|
| BASE_PERSONA | 人设、emoji 禁令、禁用句式 7 类 | 字数 / 句数 / 反问 / 引用 |
| INTENT_GUIDES[intent] | 当前 intent 该怎么"接住"用户 | 字数 / 反问 / 引用 |
| **PERSONALITY_CONTRACT** | **字数 / 句数 / 反问 / 引用** | 通用人设 |
| HARD_RULES | 不可破红线（不编造、不暴露敏感） | 反问 / 引用 |

**矛盾检查**：每次改 BASE / GUIDES / HARD_RULES 时，必须 grep 一遍是否出现字数 / 句数 / 反问 / 引用相关字眼。如果出现，迁到契约。

**v0.97 教训**：BASE 写过"单句≤30字；1-3 句"，与外向契约"2-4 句"直接冲突，LLM 取了 BASE 的，外向人格出不来。

---

## 5. BASE_PERSONA 推荐版本（与契约分工后）

> AI 自称规范见 [12-Brand-XiaoBai.md](./12-Brand-XiaoBai.md)。

```python
BASE_PERSONA = """你是小白（XiaoBai），温柔知性的女性聊天伙伴。
- 像熟识朋友，自然、克制、有分寸；情绪稳定，不卖萌、不轻浮
- 几乎不用 emoji；不用括号旁白（如「（悄悄记下）」「（笑）」「（停顿）」）
- 不机械复述用户刚说过的话；不主动报名字；不假设身份/性别/关系
- 回复字数、句数、是否反问、是否引用旧记忆 —— 全部由本轮人格契约决定
- 禁用句式（出现即低质）：
  · 「听起来你/看起来你/我能感受到你」标签式共情
  · 「我帮你/会更好地理解/如果你愿意分享」客服腔
  · 「加油/会好起来/你已经很棒了/未来可期」鸡汤
  · 「你心情不太好/很辛苦/很不容易」标签式断言
  · 「你是不是因为……」封闭式预设
  · 「……的呢/……哦」卖萌结尾
  · 「我帮你记下来了/我会记住」记忆系统自语
"""
```

---

## 6. HARD_RULES 推荐版本

```python
HARD_RULES = """≪硬边界（不可破）≫
- 不编造任何记忆里没有的事实；不替用户/关系人推断心理状态
- 不主动暴露健康/财务/家庭矛盾等敏感信息；不连续提同一痛点
- 敏感场景（情绪/纠错/质问）：执行本轮人格契约里对应子分支"""
```

**关键**：末行**指向**契约，不重复定义。

---

## 7. 评估配套：personality_signature 规则

为了量化"LLM 是否真服从契约"，L1 规则中必有一条 `personality_signature`：

### 7.1 输入

- `prompt_meta.route.personality`
- `prompt_meta.route.intent`
- `prompt_meta.activated`（这轮提供给 LLM 的记忆列表）
- 用户消息 / AI 回复

### 7.2 检查项（按 personality 不同）

| 项 | 内向 | 中性 | 外向 |
|---|---|---|---|
| 总字数（中文字符数） | ≤ 30 | ≤ 60 | ≤ 90 |
| 反问次数（数 `?` / `？`） | 0 | ≤ 1 | ≤ 1 |
| 主动引用旧记忆 | 仅在 self_summary / relationship_topic / memory_challenge / correction 允许 | 任意场景允许引 1 条 | 任意场景允许 |

### 7.3 主动引用判定

**v0.97 教训**：不能数 `prompt_meta.activated` 数量——那是"给 LLM 备的弹药"，不是"LLM 实际开的枪"。

**新版**：
- 数 reply 文本里出现的 banned/已知人名地名数量（更准）
- 或：若仅做规则启发式，可以用"reply 字数 - 用户消息字数 > 阈值 && intent 不在白名单"近似

**白名单**：`self_summary` / `relationship_topic` / `memory_challenge` / `correction` —— 这些 intent 引用记忆是被允许的甚至要求的。

### 7.4 跳过场景

- `knowledge_task`：与人格无关，跳过
- `prompt_meta.route` 缺字段：跳过
- AI 报错（`output.error`）：跳过

---

## 8. 单元测试要求

### 8.1 Composer 注入正确性

```python
def test_personality_contract_in_casual_extrovert():
    route = MemoryRoute(intent="casual", personality="extrovert", ...)
    ctx = MemoryContext(route=route, ...)
    prompt, _ = compose(ctx)
    assert "≪本轮人格契约·外向型≫" in prompt
    assert "≪本轮人格契约·内向型≫" not in prompt
    assert "≪本轮人格契约·中性型≫" not in prompt

def test_personality_contract_not_in_knowledge_task():
    route = MemoryRoute(intent="knowledge_task", personality="introvert", ...)
    ...
    prompt, _ = compose(...)
    assert "≪本轮人格契约" not in prompt

def test_personality_contract_in_emotional_support():
    """v0.97 大坑：敏感场景不能屏蔽契约"""
    route = MemoryRoute(intent="emotional_support", personality="extrovert", ...)
    ...
    prompt, _ = compose(...)
    assert "≪本轮人格契约·外向型≫" in prompt
```

### 8.2 三段契约必须真的不同

```python
def test_three_contracts_substantively_different():
    p_intro = compose(introvert_ctx)[0]
    p_balanced = compose(balanced_ctx)[0]
    p_ext = compose(extrovert_ctx)[0]
    # 至少差 50 字符（不是仅仅换个名字）
    assert distance(p_intro, p_ext) > 50
    # 字数阈值不同
    assert "≤ 30 字" in p_intro
    assert "≤ 60 字" in p_balanced
    assert "≤ 90 字" in p_ext
```

### 8.3 personality_signature 规则

```python
def test_signature_introvert_long_reply_fails():
    review = check_signature(
        reply="..." * 50,  # 100+ 字
        meta={"route": {"intent": "casual", "personality": "introvert"}},
    )
    assert review.status == "fail"

def test_signature_extrovert_normal_reply_passes():
    review = check_signature(
        reply="2-3 句话约 60 字的正常外向回复...",
        meta={"route": {"intent": "casual", "personality": "extrovert"}},
    )
    assert review.status == "pass"

def test_signature_knowledge_task_skip():
    review = check_signature(reply="...", meta={"route": {"intent": "knowledge_task", "personality": "introvert"}})
    assert review.status == "skip"
```

---

## 9. 演化策略

### 9.1 加新人格（如"幽默型"）

1. 在 `PERSONALITY_CONTRACT` 加新条目
2. 在 `PersonalityType` 枚举加值
3. 在 `personality_signature` 规则加阈值
4. 前端 `PersonalityPicker` 加选项
5. **测试**：跑 3 个 case 验证差异

### 9.2 调阈值（如"内向放宽到 40 字"）

1. 改契约里"≤ 30 字" → "≤ 40 字"
2. 改 `personality_signature` 规则常量
3. 跑 selftest 验证测试用例同步更新
4. 跑 smoke 评测验证整体通过率不降

### 9.3 不能这样改

- ❌ 把字数指标改写成"短/中/长" → 失去可测性
- ❌ 把契约从 prompt 移到代码后置处理 → LLM 失去主动遵循动机
- ❌ 让某 intent 跳过契约 → v0.97 大坑回归

---

## 10. 已知模型服从率

| Model | 内向 ≤ 30 字 服从率 | 外向用具体记忆服从率 |
|---|---|---|
| qwen3.7-max | ~70% | ~80% |
| qwen-max（无 3.7）| ~50% | ~65% |
| qwen-plus | ~60% | ~70% |
| Claude 4.6 sonnet | 估 90%+（未实测） | 估 85%+ |

**结论**：契约设计正确，但**模型服从率有限**——这是 v1.2.3 没解决的问题，新版可考虑：
- 加 few-shot 示例（contract 段后跟 2-3 个理想样例）
- 加后置硬截断（reply 超字数硬切）
- 用更强模型

参考 [10-Lessons-Learned.md](./10-Lessons-Learned.md) §3.2。

---

## 11. FAQ

**Q：为什么不让用户自定义契约？**
A：v1 不开放——契约的有效性需要评估系统配合调优，开放给用户会失控。v2 可以做"高级设置"。

**Q：为什么三段不只列 4 句话差异，要全文重复？**
A：LLM 看上下文是滑动窗口，分散提示效果差。集中一段 6 行 ≈ 150 tokens，性价比最高。

**Q：契约位置要不要放更靠前？**
A：v0.97 放在 INTENT_GUIDE 之后、HARD_RULES 之前。如果服从率不好，可尝试上移到 BASE 之后。
