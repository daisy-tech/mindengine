# MindEngine 后端重构依赖包

> 本目录是 **MindEngine** 项目"**后端从 0 到 1 重构**"所需的全部设计依据。
> 当前 monorepo 内 `backend/` 目录是 legacy **MindMem** v0.97 的参考实现，**架构思路正确，但实现细节遗漏多、补丁层层**，
> 因此从 0 重写一个干净版（用 Claude Opus 4.7），目标：
> - 同等功能完整性（v1.2.3）
> - 更干净的模块边界、更稳的数据一致性、更可测的代码
> - 允许重设 API（前端 `frontend/` 不动，后续小改适配）

---

## 命名约定（避免混淆）

| 名称 | 含义 | 何时使用 |
|---|---|---|
| **MindEngine** | 新项目正式名称（后端 + 后续整体品牌） | 本文档全部设计、新仓库、新 API、新部署 |
| **MindMem** | 旧项目/参考实现（本 monorepo 现有代码） | 仅指 legacy 代码、git tag `v0.97`、行为对齐基准 |
| **小白（XiaoBai）** | AI 聊天伙伴名称（中文「小白」，英文 XiaoBai） | system prompt、前端 UI、用户可见文案 |
| **MemoBot** | legacy AI 名称（**已废弃**） | 仅反查旧代码时使用，MindEngine 不得出现 |
| `mindengine-server/` | 新后端仓库推荐目录名 | 新建独立 repo 时使用 |
| `mindmem/` | legacy 仓库目录名（与 MindEngine 新项目无关） | 反查 legacy 代码路径时使用 |
| `v0.97` / `v1.2.3` | **v0.97** = legacy 代码 git tag；**v1.2.3** = 功能完整度目标（人格契约、评估落盘等） | 两者指同一套参考实现，只是 tag 名 ≠ 功能版本号 |

> **给 Opus 4.7**：你要写的是 **MindEngine**，AI 伙伴叫 **小白（XiaoBai）**。文中 MindMem / MemoBot 一律指 legacy 参考实现，不是目标名称。

---

## 阅读顺序

| # | 文档 | 给谁看 | 什么时候读 |
|---|---|---|---|
| 00 | [README.md](./README.md) | 所有人 | 入场 |
| 01 | [01-PRD.md](./01-PRD.md) | 产品/工程 | 重写前 · 理解"做什么" |
| 02 | [02-TDD.md](./02-TDD.md) | 工程 | 重写前 · 看总架构 |
| 03 | [03-Subsystem-Memory-Router.md](./03-Subsystem-Memory-Router.md) | 工程 | 实现 Layer 2/3 之前 |
| 04 | [04-Subsystem-Personality.md](./04-Subsystem-Personality.md) | 工程 | 实现 prompt composer 之前 |
| 05 | [05-Subsystem-Memory-Layers.md](./05-Subsystem-Memory-Layers.md) | 工程 | 实现记忆抽取/读取之前 |
| 06 | [06-Subsystem-Correction.md](./06-Subsystem-Correction.md) | 工程 | M4 阶段实现 |
| 07 | [07-Subsystem-Eval-Lab.md](./07-Subsystem-Eval-Lab.md) | 工程 | M4 阶段实现 |
| 08 | [08-Data-Model.md](./08-Data-Model.md) | 工程 | 建库前 |
| 09 | [09-LLM-Strategy.md](./09-LLM-Strategy.md) | 工程 | 写第一行 LLM 调用前 |
| 10 | **[10-Lessons-Learned.md](./10-Lessons-Learned.md)** | **必读** | **重写前 + 实现每个模块前都对照一次** |
| 11 | [11-Roadmap.md](./11-Roadmap.md) | PM/工程 | 排期 |
| 12 | [12-Brand-XiaoBai.md](./12-Brand-XiaoBai.md) | 工程/设计 | 实现 prompt + 前端前 · **小白命名与头像规范** |
| — | [xiaobai-avatar.png](./xiaobai-avatar.png) | Coding Agent | **品牌头像原文件**（直接 Read / 复制到新项目） |

---

## 重构的"必须保留"

1. **四层记忆**：profile / event / episodic / relationship —— 不要降级成两层或三层
2. **Memory Router v1.5 三层架构**：硬规则 / 小模型 intent / 策略查表 —— 是已经验证的最优解
3. **人格契约**：可执行硬指标（字数 / 反问 / 引用），不是文字描述
4. **在线记忆纠错**：用户在对话中纠错时 AI 必须立刻软删旧记忆 + 实体硬封禁
5. **审计可追溯**：`prompt_meta` 必须随每条 assistant 消息持久化（前端透明 + 真实聊天评估的基础）
6. **评测实验室**：合成评测 + 真实聊天评估 双路并行
7. **零 thinking-mode**：所有 Qwen 调用必须显式 `enable_thinking=False`
8. **小白（XiaoBai）品牌**：AI 伙伴统一称「小白」，头像见 `docs/rebuild/xiaobai-avatar.png`（见 [12-Brand-XiaoBai.md](./12-Brand-XiaoBai.md)）

## 重构的"必须改掉"

参见 [10-Lessons-Learned.md](./10-Lessons-Learned.md)，最严重的几条：

1. ❌ Prompt 组件耦合 `route` 对象，导致测试要构造大量样板
2. ❌ 异步任务（Celery）写库与 Web 请求写库共用同一 SQLAlchemy 引擎，并发冲突
3. ❌ `prompt_meta` 存储在 `Message.meta` 的子字段里，schema 漂移
4. ❌ Mem0 / Qdrant 直接耦合在业务代码里，难替换难测
5. ❌ 评测脚本和生产代码混在 `backend/scripts/`，部署上线要小心排除
6. ❌ `_SECTION_HEADERS` 硬编码字符串匹配 prompt 段落，每次 prompt 改动就要同步改这里

---

## 推荐项目结构（MindEngine 新仓库）

```
mindengine-server/             # MindEngine 新后端仓库根
├── app/
│   ├── api/                   # FastAPI 路由层（薄）
│   │   ├── chat.py
│   │   ├── conversations.py
│   │   ├── memory.py
│   │   ├── eval.py
│   │   └── correction.py
│   ├── domain/                # 领域模型 (Pydantic)
│   │   ├── memory.py          # 四层记忆 DTO
│   │   ├── personality.py     # 人格契约
│   │   ├── route.py           # MemoryRoute / RoutedMemory
│   │   └── prompt.py          # PromptPack / PromptMeta
│   ├── services/              # 业务逻辑（无 DB / 无网络的纯逻辑优先）
│   │   ├── memory_router/     # intent_classifier + router + context loader
│   │   ├── prompt_composer/   # base + intent_guides + contracts + render
│   │   ├── correction/        # 纠错管线
│   │   └── eval/              # 评测（合成 + 真实）
│   ├── infra/                 # 基础设施适配层（可替换）
│   │   ├── llm/               # LLM client 抽象（OpenAI 兼容 / Anthropic / 本地）
│   │   ├── vector/            # Mem0 / Qdrant / 替代品抽象
│   │   ├── db/                # SQLAlchemy / 迁移
│   │   └── queue/             # Celery / arq / 替代品抽象
│   └── workers/               # 异步任务定义（依赖 services + infra）
├── tests/
│   ├── unit/                  # 纯逻辑测试（无 DB / 无网络）
│   ├── integration/           # 走 DB / mem0 的集成测试
│   └── fixtures/              # 评测 case + chat_audit 样本
├── scripts/                   # 一次性运维脚本（独立于 app/）
├── alembic/                   # DB schema 迁移
└── pyproject.toml
```

> **核心原则**：`services/` 是纯逻辑，`infra/` 是适配，`api/` / `workers/` 是入口。
> 这样 80% 代码（services + domain）可以零基础设施依赖跑 pytest。

---

## 参考实现

MindEngine 的设计文档要**独立可读**，但有不确定的地方可以反查 legacy MindMem 参考实现：

- 参考实现位置：本仓库 `backend/`（legacy 项目名 **MindMem**，仓库目录名常为 `mindmem/`，tag `v0.97`，commit `66bc075`）
- 自测脚本：`backend/scripts/selftest_p0.py` —— **MindEngine 必须复现的所有关键不变式**（112 个 case，能跑通 = 行为对齐）

---

## 重构成功的判据

| # | 判据 | 验证方式 |
|---|---|---|
| 1 | 新后端能让现有前端工作（API 适配后） | 跑通现有 5 类 EvalView Tab |
| 2 | 自测 100+ 项全过 | 移植 `selftest_p0.py` 到 MindEngine 并通过 |
| 3 | 同一份合成 case，通过率 ≥ legacy MindMem | 跑 smoke 20 / full 50，对比 v0.97 baseline |
| 4 | 真实聊天评估 L0/L1 报告字段完全对齐 | 对比同一份 `chat_audit_v1` 输出 |
| 5 | 重写后核心代码 ≤ 7000 行 | wc -l app/services/ + app/domain/，对比 legacy ~10k |
| 6 | services/ 层 100% 可在无 DB 环境跑 unit test | pytest tests/unit/ 不需要任何容器 |

---

最后更新：2026-06-07 · MindEngine rebuild · 行为基准：legacy MindMem v0.97
