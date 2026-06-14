# MindEngine

带长期记忆的 AI 聊天后端 + Web 前端。AI 伙伴叫 **小白（XiaoBai）**。

当前版本：**v2.0.0**（含 v2.0.1–v2.0.2.8 补丁，详见 [CHANGELOG/v2.0.0.md](CHANGELOG/v2.0.0.md)）。

技术栈：FastAPI · Vue 3 · Postgres + pgvector · Redis · Celery · 阿里云 DashScope（Qwen，OpenAI 兼容接口）。

---

## 功能一览

| 模块 | 能力 |
| --- | --- |
| **聊天** | SSE 流式回复；客户端断连后 assistant 消息仍落库（含 `prompt_meta`）；首句自动填会话标题 |
| **记忆路由** | 三层 Memory Router（硬规则 → intent 分类 → 策略查表）；按 intent 决定加载哪些记忆层 |
| **四层记忆** | profile / event / episodic / relationship；聊天后 Celery 异步抽取 |
| **记忆去重** | 抽取时 pgvector ANN + 事件标题归一；历史批量清理 `scripts/dedup_memories.py` |
| **记忆纠错** | 用户对话中纠错 → 软删旧记忆 + 实体封禁 + `memory_deprecations` 审计 |
| **Prompt 归档** | 每条 assistant 回复旁路落盘完整 system/user/reply JSON；Prompt 抽屉可加载 |
| **关系图谱** | 多层径向布局（via 二阶关系）、角色着色、`profile.name` 优先作中心节点 |
| **评测实验室** | 合成评测 smoke(20) / full(55)；报告落盘；历史 run 查看/删除 |
| **认证** | JWT + Argon2；`change-password` API；`reset_password.py` CLI |
| **自检** | `selftest/run.py` 端到端 27 项检查（环境 / API / 记忆抽取召回 / 清理） |

前端五个视图：`LoginView` · `ChatView` · `MemoryView` · `SocialGraphView` · `EvalView`。

---

## 系统架构

```
浏览器
  │
  ▼
frontend (nginx :5173)  ──反代──▶  backend (FastAPI :8000)
                                        │
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
              Postgres            Redis              prompt/eval 落盘
            (+ pgvector)         (broker/cache)         (可选 volume)
                    ▲
                    │
              celery + celery-beat
              (memory.extract_* / correction)
```

| 服务 | 镜像 / 构建 | 端口 | 职责 |
| --- | --- | --- | --- |
| `postgres` | `pgvector/pgvector:pg16` | 5432 | 业务数据 + episodic 向量（HNSW） |
| `redis` | `redis:7-alpine` | 6379 | Celery broker、intent 缓存 |
| `backend` | `mindengine-backend:latest` | 8000 | HTTP API、SSE 聊天 |
| `celery` | 同上 | — | 记忆抽取、纠错后台任务 |
| `celery-beat` | 同上 | — | 定时任务（如有） |
| `frontend` | `mindengine-frontend:latest` | 5173→80 | 静态 SPA + `/api` 反代 |

LLM 调用经 `LLMRouter` 按角色路由（chat / extract / intent / judge），默认对接 DashScope `qwen-plus` 等模型；所有调用显式 `enable_thinking=false`。

---

## 目录结构

```
mindengine/
├── backend/
│   ├── app/
│   │   ├── api/              # FastAPI 路由（auth, chat, conversations, memory, eval, health）
│   │   ├── domain/           # Pydantic 领域模型（memory, personality, route, prompt, correction）
│   │   ├── services/         # 业务逻辑（memory_router, prompt_composer, memory_extract, eval_synthetic, …）
│   │   ├── infra/            # DB / LLM / 仓储适配（repositories, dashscope, pgvector）
│   │   └── workers/          # Celery 任务定义 + runners（抽取、纠错执行体）
│   ├── scripts/              # 运维 CLI（dedup_memories, reset_password, smoke_m4）
│   ├── selftest/             # 端到端自检 + refresh-and-run.sh 热更新脚本
│   ├── eval/cases/           # 合成评测用例 JSON（smoke_cases, full_cases）
│   ├── alembic/              # 数据库迁移
│   ├── tests/                # pytest 单元 / 集成测试
│   └── docker-compose.yml    # 全栈编排（从此目录启动）
├── frontend/
│   ├── src/views/            # 五个主视图
│   ├── src/components/       # PromptDrawer, SyntheticReportView, …
│   └── refresh-and-deploy.sh # 构建 dist 并同步进 frontend 容器
├── docs/rebuild/             # 架构设计文档（PRD、TDD、子系统设计）— 参考用
└── CHANGELOG/                # 版本发布记录
```

分层约定（`services/` 不直接 import ORM；`infra/` 实现 `Protocol`；`api/` / `workers/` 是入口）见 `docs/rebuild/02-TDD.md` §1.2。

---

## 快速开始

### 1. 配置环境变量

```bash
cd backend
cp .env.example .env
```

至少修改：

| 变量 | 说明 |
| --- | --- |
| `OPENAI_API_KEY` | DashScope API Key |
| `OPENAI_BASE_URL` | 默认 `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `JWT_SECRET` | 生产环境必须换掉默认值 |
| `ALLOW_DESTRUCTIVE_DEV` | `1` 允许任意登录用户跑合成评测；`0` 仅 `EVAL_USER_ID` |

完整列表见 `backend/app/config.py` 与 `.env.example`。

### 2. 启动全栈

```bash
cd backend
docker compose build
docker compose up -d
```

- Web UI：`http://<host>:5173`
- API 文档：`http://<host>:8000/docs`
- 健康检查：`GET /healthz`、`GET /readyz`

### 3. 验证

```bash
docker compose exec backend python -m selftest.run
# 期望：PASS=27  WARN=0  FAIL=0
```

注册账号 → 登录 → 聊几轮 → 打开「记忆」页确认 profile / episodic 有数据（Celery 抽取需几秒）。

---

## 日常开发与热更新

完整 `docker compose build` 较慢。改 Python / 前端静态文件时，可用热更新脚本把改动 cp 进**正在运行的容器**，再 restart 相关服务。

### 后端 + Celery

```bash
cd backend
./selftest/refresh-and-run.sh          # 同步代码 + 跑 selftest
./selftest/refresh-and-run.sh --keep-data   # 保留测试用户数据
```

脚本会 `docker compose cp` 变更目录到 `backend` / `celery` 容器，清理 `._*` 元数据文件，并重启服务。覆盖范围见脚本内注释（`app/`, `workers/`, `scripts/`, `eval/cases/` 等）。

### 前端

```bash
cd frontend
./refresh-and-deploy.sh                # 需要宿主机有 Node + npm
./refresh-and-deploy.sh --skip-build   # 仅同步已有 dist/ 进容器
```

构建产物通过 `docker compose cp dist/. frontend:/usr/share/nginx/html/` 写入 nginx 容器。

### 常用运维 CLI

在 `backend` 容器内执行：

```bash
# 历史记忆去重（先 dry-run 预览）
docker compose exec backend python -m scripts.dedup_memories --dry-run
docker compose exec backend python -m scripts.dedup_memories

# 重置用户密码（需 shell 权限，不走 HTTP）
docker compose exec backend python scripts/reset_password.py \
  --email user@example.com --password 'new-password'
```

`scripts/` 必须逐文件 cp 到 `/app/scripts/`（不要 `cp ./scripts backend:/app/scripts` 整个目录，会在容器内嵌套成 `/app/scripts/scripts/`）。

---

## 测试

| 层级 | 命令 | 说明 |
| --- | --- | --- |
| 单元测试 | `cd backend && pytest tests/` | 纯逻辑 + fake repo，无需容器 |
| 端到端 | `docker compose exec backend python -m selftest.run` | 依赖运行中的全栈 + LLM Key |
| M4 冒烟 | `docker compose exec backend python scripts/smoke_m4.py` | 纠错 + 评测子系统 |
| 合成评测 | 前端「评测实验室」或 `POST /api/eval/synthetic/{name}/start` | smoke ≈ 30–60s，同步阻塞 |

CI 友好检查：`ruff check app tests`、`pytest tests/unit/`。

---

## 关键 API 端点

| 前缀 | 用途 |
| --- | --- |
| `/auth/*` | 注册、登录、`/me`、`change-password` |
| `/api/chat` | SSE 流式聊天 |
| `/api/conversations/*` | 会话 CRUD、消息列表、`/messages/{id}/prompt`（完整 Prompt 归档） |
| `/api/memory/*` | 四层记忆读写、封禁实体、deprecations 审计 |
| `/api/eval/*` | 合成评测、历史 run、chat audit |

OpenAPI title：`MindEngine API`。前端通过 nginx 反代访问上述路径，无需额外 CORS 配置。

---

## 数据与迁移

- **存储**：Postgres 单库；episodic embedding 在 `episodic_memories.embedding`（pgvector），无独立向量库。
- **软删除**：记忆废弃走 `status='deprecated'` + `memory_deprecations` 审计表，不做物理删除。
- **legacy 迁移**：不支持直接读取旧版 MindMem v0.97 数据库。需自行导出后按字段映射导入；episodic 向量建议重新抽取。详见 CHANGELOG Breaking changes。

---

## 文档索引

| 文档 | 用途 |
| --- | --- |
| [CHANGELOG/v2.0.0.md](CHANGELOG/v2.0.0.md) | v2.0.0 发布说明 + v2.0.1–v2.0.2.8 补丁全记录 |
| [docs/rebuild/](docs/rebuild/) | 架构设计原文（PRD、TDD、记忆层、纠错、评测、数据模型、D1–D9 决策） |
| [backend/README.md](backend/README.md) | 后端分层约定与 M1 脚手架说明 |
| [backend/.env.example](backend/.env.example) | 环境变量模板 |

设计阶段文档描述的是「目标架构」；若与代码有出入，以代码和 CHANGELOG 为准。

---

## 命名约定

| 名称 | 含义 |
| --- | --- |
| **MindEngine** | 本项目（后端 + 前端 + 部署） |
| **小白 / XiaoBai** | AI 伙伴，用户可见名称 |
| **MindMem** | 旧版参考实现（tag `v0.97`），仅作行为对齐基准 |
| **MemoBot** | 已废弃旧名，新代码中不出现 |

---

最后更新：2026-06-14 · MindEngine v2.0.0
