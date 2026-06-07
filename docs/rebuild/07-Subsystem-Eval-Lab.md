# 子系统 · 评测实验室

> 责任：让"AI 是否在进步"有客观度量。两条互补路径：
> A. **合成评测**：手写 case + 跑全流程对比 expected
> B. **真实聊天评估**：拿真实历史会话跑 L0 结构 + L1 启发式 + 归因

> 🔄 **修订 v1.1（2026-06）· 决策 D5**：
> - L0 + L1 仍是 **0 LLM 成本**的全量粗筛（默认路径不变）。
> - 但"幻觉/复述"这类**语义判断**靠脆弱正则不可靠（坑 6.1/6.2/6.3），且这恰是核心承诺。新增 §3.3a：**对 L1 标红（fail/suspicious）的少量 turn，提前引入便宜小模型 `JUDGE` 复核**（从 v1.3 提前到 v1）。成本可控（只判 flagged turn）。
> - §3.3：`fabrication_under_challenge` 的实体字典改为**结构化已知实体**（profile.basic + relationships.name + banned），不依赖对 episodic 做中文 NER。

---

## 1. 两条路径对比

| 项 | 合成评测 | 真实聊天评估 |
|---|---|---|
| 输入 | 手写 case (history, message, expected) | 用户历史 conversation |
| 是否调 LLM | 是（要跑真实 chat） | **否** |
| 输出 | 通过率、intent 混淆矩阵 | 每轮 final_status + 归因 |
| 频率 | 上线前、PR 时 | 用户日常按需触发 |
| 成本 | 高（每 case 一次 chat 调用） | 0 LLM |
| 价值 | 防回归 | 发现真实失败模式 |

---

## 2. 合成评测

### 2.1 case 格式

```jsonc
{
  "id": "A-x01",
  "personality": "extrovert",
  "history": [
    {"role": "user", "content": "我妻子叫张三"},
    {"role": "assistant", "content": "记住了，张三是个好名字。"}
  ],
  "user": "她最近怎么样",
  "expected": {
    "intents": ["relationship_topic"],
    "optional_intents": ["emotional_support"],
    "must_activate_keywords": ["张三", "妻子"],
    "forbidden_phrases_in_reply": ["听起来", "我能感受到"],
    "forbidden_phrases_in_system": ["（无）"]
  },
  "tags": ["relationship", "post_intro"]
}
```

### 2.2 case 集

| 集 | 数量 | 用途 |
|---|---|---|
| smoke_cases.json | 20 | 上线前快验，2-3 分钟跑完 |
| full_cases.json | 50+ | PR 必跑，5-10 分钟 |

### 2.3 评测脚本

```python
async def run_case(case: EvalCase) -> EvalResult:
    # 1. 用 case 的 history + personality + user 走完整 chat 流程
    pack = await chat_orchestrator.run_synthetic(
        history=case.history,
        message=case.user,
        personality=case.personality,
    )
    # 2. 与 expected 对比
    checks = []
    checks.append(check_intent(pack.meta.route.intent, case.expected.intents, case.expected.optional_intents))
    checks.append(check_must_activate(pack.meta.activated, case.expected.must_activate_keywords))
    checks.append(check_forbidden(pack.meta.system, pack.reply, case.expected.forbidden_phrases_in_*))
    return EvalResult(case_id=case.id, checks=checks)
```

### 2.4 报告字段

```jsonc
{
  "run_id": "20260607_122334",
  "run_type": "smoke",
  "started_at": "...",
  "finished_at": "...",
  "total": 20,
  "passed": 19,
  "pass_rate": 0.95,
  "intent_confusion_matrix": {
    "casual": {"casual": 5, "memory_challenge": 0},
    "memory_challenge": {"casual": 1, "memory_challenge": 4}
  },
  "cases": [{...}]
}
```

### 2.5 评测用户独立

不要拿用户真实账号跑评测——会污染他们的画像。

- 建一个 `EVAL_USER_ID`（固定 uuid）
- `seed_persona` 接口在 DEV_MODE 下可重置该用户的 4 层记忆
- 评测开始前先 seed，跑完后保留供查

---

## 3. 真实聊天评估（更重要）

### 3.1 数据基础：chat_audit_v1

每个 assistant message 持久化 `prompt_meta`（见 [02-TDD.md](./02-TDD.md) §3.4）。

`chat_audit_v1` pack 结构：

```jsonc
{
  "version": "chat_audit_v1",
  "exported_at": "...",
  "conversation": {
    "id": "...",
    "title": "...",
    "user_id": "...",
    "turn_count": 15,
    "message_count": 30
  },
  "memory_snapshot": null | {导出时刻全库快照（默认不附）},
  "turns": [
    {
      "turn_id": "...",
      "index": 0,
      "input": {"user_message": "...", "history_before": [...]},
      "output": {"assistant_reply": "...", "error": null, "ts": "..."},
      "audit": {
        "available": true,
        "prompt_meta": {...},
        "derived": {
          "pool_stats": {...},
          "activation_stats": {...},
          "consistency_checks": [...]  // L0 结果
        }
      },
      "evaluation": {...}  // 旧字段，可省
    }
  ],
  "summary": {
    "turns_total": 15,
    "turns_with_audit": 15,
    "auto_checks": {...}  // L0 聚合
  }
}
```

### 3.2 L0 结构自检（无 LLM）

对每轮 `prompt_meta` 做结构性检查：

| 检查 | severity | 说明 |
|---|---|---|
| `prompt_meta.exists` | high | meta 缺失即 fail |
| `route.intent_in_valid_set` | high | intent ∈ 9 类 |
| `route.personality_in_valid_set` | high | personality ∈ 3 类 |
| `explicit_in_system_section` | medium | system prompt 含"【本轮可以提及的具体记忆】" |
| `background_in_system_section` | medium | system 含"【你大致还记得这些...】" |
| `activated_consistent_with_pools` | medium | activated 数 == sum(pools) |
| `assistant_reply_nonempty` | high | reply 不为空 |
| `prompt_meta.system_nonempty` | high | system 文本非空 |

**v0.97 教训**：section 标题字符串硬编码，prompt 改了标题就 false positive。新版用结构化字段索引：

- composer 输出 `PromptPack { system, sections: {base, profile, explicit, ...} }`
- L0 检查直接看 `sections.explicit is not None`，不字符串匹配

### 3.3 L1 启发式规则（无 LLM）

| 规则 | severity | 简述 |
|---|---|---|
| `error_reply` | high | reply 包含 "[出错"、"系统错误" 等异常文本 |
| `recall_for_challenge` | high | intent=memory_challenge 时，用户关键词在池里都找不到 |
| `recall_for_self_summary` | high | self_summary 时池子为空 |
| `data_vs_activation_gap` | medium | snapshot_stats 显示池里有但 activated 没装 |
| `generic_reply` | medium | reply 太短/太通用（"嗯""好的""明白"） |
| `reply_off_topic` | medium | 用户句与回复字符 2-gram 重叠率 0% 且未反问 + intent 白名单豁免 |
| `correction_persisted` | high | 纠错后下一轮还出现被禁实体 |
| `correction_no_concrete_ack` | high | correction 回复未含用户给出的正确事实 |
| `fabrication_under_challenge` | high | memory_challenge 时，reply 含池外的具体实体 |
| `followup_overflow` | low | followup_pool 一次问太多条 |
| `personality_signature` | medium | 字数/反问/引用违反人格契约 |

每条规则签名：

```python
def rule_xxx(turn: dict, prev_turn: dict | None, pack_context: dict) -> RuleResult:
    """
    返回：{id, status: pass|fail|suspicious|skip, severity, detail, attribution}
    """
```

> 🔄 **v1.1 · `fabrication_under_challenge` 实体字典来源（决策 D5）**：不要对 episodic 做中文 NER（无模型 NER 不可靠，坑 6.3）。字典 = **结构化已知实体**：`profile.basic`（姓名/地名）+ `relationships.name` + `banned_entities`。reply 中出现"具体地名/人名"但不在该字典、也不在本轮 `activated` 池 → suspicious，交 §3.3a 的 `JUDGE` 复核。

### 3.3a 🔄 标红 turn 的小模型复核（v1.1 新增 · 决策 D5）

**动机**：L1 正则对"幻觉/未复述/偏题"是粗筛，误报漏报都有（坑 6.1/6.2/6.3）。全量上 LLM judge 太贵，但**只对 L1 标红的 turn** 复核，量小、成本可控，且这些 turn 恰是最该看准的。

**流程**：

```
for turn in turns:
    l1 = run_l1_rules(turn)             # 0 LLM，全量
    if JUDGE_ENABLED and l1.has_flag(("fail","suspicious"), severity>=high):
        verdict = judge(turn)            # 便宜 JUDGE 模型，语义判 1 次
        turn.review.judge = verdict      # {agrees: bool, corrected_status, reason}
```

- 模型：`JUDGE`（见 [09-LLM-Strategy.md](./09-LLM-Strategy.md) §1 常量表，便宜档）。
- 默认 `JUDGE_ENABLED=false`，可按需在评测时打开（真实聊天评估默认仍 0 LLM；想要更准时显式开）。
- judge 只**复核/降噪**，不覆盖 L0 high fail（结构问题确定性高）。
- 复核结果进 `review.judge`，并参与 `final_status`：L1 标 suspicious 但 judge 判 OK → 降为 ok；L1 漏报但 judge 判幻觉 → 升 bad。
- 成本：仅 flagged turn × 1 次便宜调用，一个会话通常 < 5 次。

**测试**：mock judge 返回 `agrees=false` → flagged turn 的 final_status 被 judge 修正。

### 3.4 归因体系

每条 fail/suspicious 附 `attribution` 字段（用户视角的归因码）：

| 码 | 名称 | 例子规则 |
|---|---|---|
| A | 数据缺失 | recall_for_* |
| B | 路由错判 | （intent 错） |
| C | 召回未触达 | recall_for_challenge（池里有但 activated 没装） |
| D | LLM 行为偏离契约 | personality_signature |
| E | LLM 偏离指引 | generic_reply, reply_off_topic |
| F | 数据污染 | correction_persisted（应删未删） |
| G | 幻觉 | fabrication_under_challenge |

聚合所有 turn 的 attribution，给出 `root_cause_top`：

```jsonc
"root_cause_top": [["D", 6], ["E", 5], ["A", 1]]
```

### 3.5 final_status 决策

```python
def final_status(l0, l1) -> Literal["good","ok","suspicious","bad","skip"]:
    if l0.skip or l1.skip == "skip":
        return "skip"
    # high 失败直接 bad
    if l0.high_fail or any(r.severity == "high" and r.status == "fail" for r in l1):
        return "bad"
    # medium 失败但 L1 全过 → ok（v0.97 修复点）
    if l0.medium_fail and all(r.status in ("pass","skip") for r in l1):
        return "ok"
    # L1 有 suspicious → suspicious
    if any(r.status == "suspicious" for r in l1):
        return "suspicious"
    return "good"
```

**v0.97 教训**：原本 medium fail 就 suspicious，太严。修后 medium fail + L1 pass = ok，避免假阴性。

### 3.6 报告 review pack（eval_review_v1）

```jsonc
{
  ...chat_audit_v1 全部字段,
  "evaluated_at": "...",
  "review": {
    "schema": "eval_review_v1",
    "evaluable_turns": 15,
    "structure_pass_rate": 1.0,
    "final_ok_rate": 0.4,
    "counters": {
      "l0_pass": 15, "l0_warn": 0, "l0_fail": 0, "l0_skip": 0,
      "l1_ok": 6, "l1_suspicious": 3, "l1_bad": 6, "l1_skip": 0,
      "final_good": 6, "final_ok": 0, "final_suspicious": 3, "final_bad": 6, "final_skip": 0
    },
    "rule_stats": {
      "personality_signature": {"pass": 8, "suspicious": 0, "fail": 6, "skip": 1},
      ...
    },
    "root_cause_top": [["D", 6], ["E", 5]]
  },
  "turns": [
    {
      ...原 turn 字段,
      "review": {
        "l0_status": "pass",
        "l1_status": "bad",
        "final_status": "bad",
        "rules": [...],
        "suggested_root_cause": ["D"],
        "snapshot_status": "at_turn"
      }
    }
  ]
}
```

---

## 4. 服务端落盘

```
backend/eval/exports/reviews/{user_id}/{conv_id}.json
```

- 覆盖式（最新一份）
- chmod 0644 让 NFS 跨用户可读
- API `GET /api/eval/chat-audit/{conv_id}?force=false` 默认命中磁盘直接返回，否则评估 + 落盘

### 4.1 防路径穿越

`{user_id}` 和 `{conv_id}` 必须严格 `[A-Za-z0-9_-]+`，清洗结果 != 原值即 raise。

### 4.2 列表 API

`GET /api/eval/chat-audit-stored` 返回所有已落盘的轻量摘要：

```jsonc
{
  "items": [
    {
      "conversation_id": "conv_xxx",
      "evaluated_at": "2026-06-07T11:24:00Z",
      "turns_total": 15,
      "evaluable_turns": 15,
      "final_ok_rate": 0.4,
      "counters": {"final_ok": 6, "final_suspicious": 3, "final_bad": 6, "turns_skipped": 0}
    }
  ]
}
```

前端用这个数据给会话列表打✓标 + 三色 mini-bar。

---

## 5. 模块结构

```
services/eval_synthetic/
├── case_loader.py        # 加载 smoke/full json
├── runner.py             # 跑 case
├── reporter.py           # 聚合报告
└── persona_seed.py       # eval persona 灌库

services/eval_chat_review/
├── l0_rules.py           # 结构自检
├── l1_rules.py           # 启发式规则（11 条以上）
├── reviewer.py           # 组装 L0+L1+归因+final_status
├── attribution.py        # 归因码映射
└── store.py              # 落盘/读盘/列表/删除
```

---

## 6. 测试要求

### 6.1 L0 规则

每条规则给至少 1 个 pass case + 1 个 fail case + 1 个 skip case。

### 6.2 L1 规则

| 规则 | 必备测试 case |
|---|---|
| recall_for_challenge | 用户问"X" + 池里没"X" → fail; 池里有"X" → pass |
| personality_signature | 内向超长 → fail; 中性正常 → pass; knowledge_task → skip |
| correction_persisted | 纠错后下一轮有禁词 → fail; 没禁词 → pass |
| fabrication_under_challenge | reply 含池外实体 → fail; 全在池内 → pass |
| reply_off_topic | 短事实回应（intent 在白名单）→ pass; 闲聊偏题 → suspicious |

### 6.3 store

- save_review 写入文件 + 权限 0644
- load_review 能读回 + 包含 evaluated_at
- list_stored 摘要含 counters
- delete_review 真删
- 路径穿越被挡

---

## 7. 与合成评测的串通

发现真实聊天的失败模式 → 在 `full_cases.json` 增 case，下次 PR 跑能复现 + 防回归。

```jsonc
{
  "id": "A-x07",
  "user": "你忘了我们家养什么了吗",
  "expected": {
    "intents": ["memory_challenge"],
    "must_contain": ["猫"],
    "forbidden": ["小鹏", "我没注意到", "你可以告诉我"]
  },
  "tags": ["fabrication_check", "from_real_chat"]
}
```

---

## 8. 已知限制 & v2 计划

| 项 | v1 | v2 |
|---|---|---|
| 启发式规则数 | 11+ | 20+ |
| LLM Judge | 🔄 v1.1：**对 L1 标红 turn 可选复核**（§3.3a，默认关、按需开） | 全量语义判分 / 多 judge 投票 |
| 跨会话趋势 | 单会话 | 用户级评分卡 + trend |
| 实体词典 | 🔄 v1.1：profile.basic + relationships.name + banned（结构化）| 从 episodic 做模型 NER 扩充 |
| 报告对比 | 单次 | v0.96 vs v0.97 自动 diff |

---

## 与 legacy MindMem v0.97 的差异

| 项 | legacy MindMem v0.97 | MindEngine |
|---|---|---|
| L0 字符串匹配 prompt 段 | 易 false positive | 结构化 sections 字段 |
| final_status 严格度 | 太严（已修） | 保留修复 |
| 落盘权限 | 0600（NFS 不可读） | 0644 |
| 模块拆分 | eval_chat_review.py 797 行 | 拆为 l0_rules/l1_rules/reviewer/store/attribution |
| 列表 API | 不存在 | 新增 |
| 评估缓存 | 仅内存 | 磁盘持久 + 命中读盘秒返回 |
