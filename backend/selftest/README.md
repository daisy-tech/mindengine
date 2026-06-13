# MindEngine 自测脚本

跑完整 docker-compose 栈之后,用这个脚本一键过一遍核心功能。
不替代 `pytest` 单测;它是面向 **运行中的真实栈** 的端到端探针。

---

## 目录里有什么

```
backend/selftest/
├── __init__.py
├── README.md      ← 本文件
└── run.py         ← 主脚本(单文件,只依赖 backend 已装的库)
```

---

## 快速开始

### 推荐用法:一键脚本(无需重 build)

```bash
# 工作目录 backend/ 下
./selftest/refresh-and-run.sh
```

它做的事(每步都很短,30s 内跑完):

1. `docker compose up -d --force-recreate backend celery celery-beat`
   ← 让容器读最新 `.env`(常见坑:改了 `OPENAI_API_KEY` 但容器没重启)
2. `docker compose cp` 把当前最新的 `app/api/memory.py` /
   `app/infra/db/factory.py` / `selftest/` 拷进容器
3. `docker compose restart backend celery` ← 让 fastapi / celery 重新加载
4. `docker compose exec backend python -m selftest.run`

任何额外参数会原样转给 `python -m selftest.run`:

```bash
./selftest/refresh-and-run.sh --keep-data
./selftest/refresh-and-run.sh --skip-extraction --skip-recall
./selftest/refresh-and-run.sh --no-llm-soft --extraction-wait 60
```

### 手动用法(如果只想跑 selftest,不动栈)

```bash
docker compose cp ./selftest backend:/app/selftest
docker compose exec backend python -m selftest.run
```

> 镜像未来 rebuild 后会自动包含 `selftest/`(已加进 Dockerfile 的 COPY 步骤),
> 上面那条 `cp` 只是为了**当下不重 build 也能用**。

退出码:`0` = 没有 HARD 失败;`1` = 有 HARD 失败。
WARN(LLM 软失败)永远不影响退出码。

---

## 检查项一览

| Tier | 检查 | 失败级别 | 排查方向 |
| --- | --- | --- | --- |
| 1 | `/healthz` | HARD | backend 进程没起 |
| 1 | `/readyz`(postgres + pgvector) | HARD | DB 没起 / pgvector 没装 |
| 1 | `OPENAI_API_KEY` 非空且 ≠ `replace-me` | HARD | `.env` 没写 / 容器没重启拿到新 env |
| 1 | alembic 已迁移(`users` 表存在) | HARD | `docker compose exec backend alembic upgrade head` |
| 2A | register / me / change-password / 旧密码失效 / 新密码登录 | HARD | auth 路由 / DB |
| 2B | 建会话 + SSE 聊天 + D2 持久化 + assistant.meta.route + /audit | HARD(SSE 默认 SOFT) | `MockLLMClient: no scripted turns` → API key 没拿到;`compose_failed` → 模型 id 错或 LLM 限流 |
| 2C | PATCH profile / GET profile 回读 / banned-entities 写读 | HARD | memory router / repo |
| 2D | events / episodic / relationships / deprecations 只读 GET | HARD | repo 层 |
| 2E | `/api/eval/synthetic` 列表 / chat-audit-stored | HARD | eval 路由 |
| 2F | 发 3 句生活信息 → 等 celery 抽取 → profile/episodic 增长 | SOFT(可 `--no-llm-soft`) | `docker compose logs celery`,关注 `Future attached to a different loop`(已修)与 LLM 限流 |
| 2G | 注入 sentinel name → 下一轮提问回复包含 | SOFT | LLM 措辞,不一定 100% 命中,看到回复内容自己判断 |
| 3 | 清理:删测试用户 + 关联表 9 张 | — | `--keep-data` 关闭 |

---

## 你可能撞到的常见症状 → 直接修法

| 症状 | 真因 | 修 |
| --- | --- | --- |
| `MockLLMClient: no scripted turns` | celery 拿到的 `OPENAI_API_KEY` 还是 `replace-me` | `docker compose up -d --force-recreate backend celery celery-beat` |
| Tier 1 `users 表不存在` | 新 DB 没迁移 | `docker compose exec backend alembic upgrade head` |
| Tier 2F 永远等不到记忆 | celery 报 `Future attached to a different loop`,或 LLM 报 401 / 模型 id 不存在 | 看 `docker compose logs celery`;loop 问题已通过 `NullPool` 修;模型问题改 `app/domain/llm.py::DEFAULT_MODELS` |
| SSE chat 直接报错事件 | LLM key/base_url 错,或 DashScope 模型名拼错 | 先 `curl -i $OPENAI_BASE_URL/chat/completions ...` 探活 |
| Tier 3 清理失败 | 表里有外键我没枚举到 | 看报错的表名加进 `_USER_SCOPED_TABLES` |

---

## 前端手动清单(2 分钟过一遍)

后端自测过了之后,人肉过一下前端 UI。打开 `http://<ECS_IP>:5173`:

### 1. 登录页(`LoginView.vue`)
- [ ] 注册新用户 → 自动登录跳转聊天
- [ ] 错密码 → 红色错误提示,不跳转
- [ ] 点"忘记密码?" → 弹出说明,文案里有 `scripts/reset_password.py` 命令
- [ ] 中文 placeholder / 按钮文案正常,无英文残留

### 2. 聊天页(`ChatView.vue`)
- [ ] 发"你好" → **流式逐字**出现回复(不是一次性整段)
- [ ] 切换人格 introvert / balanced / extrovert → 下一条回复风格变化
- [ ] 回复区可以滚动,左侧会话列表能新建/切换/删除
- [ ] 刷新页面后历史消息还在

### 3. 记忆页(`MemoryView.vue`)
- [ ] 自测脚本的 `--keep-data` 跑完后,这里能看到 profile / events / episodic
- [ ] 编辑 profile → 保存 → 刷新仍在
- [ ] banned entities 列表能加能删

### 4. 关系图(`SocialGraphView.vue`)
- [ ] 至少不白屏 / 不报错(空数据时显示提示文案即可)

### 5. 评测页(`EvalView.vue`)
- [ ] 列出至少一个 synthetic case 文件
- [ ] 选一条历史会话点 chat-audit → 能拿到结果(LLM 启用时)或合理的"未启用"提示

### 6. 全局
- [ ] 顶部右侧"修改密码" → 弹窗 → 改密 → 用新密码重登成功
- [ ] 任何 toast / dialog 都是中文
- [ ] 浏览器 Console 无红色报错(忽略 dev tool / extension 噪音)

---

## 可选:把 selftest 加到镜像里(下次 build 自动包含)

`backend/Dockerfile` 已增加 `COPY selftest /app/selftest`;
首次 `docker compose build backend` 之后就不再需要 `docker compose cp`。
