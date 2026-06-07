# MindEngine 前端 · 小白

Vue 3 + Vite + Pinia + Element Plus 实现的 5 视图前端：

- `LoginView` — 注册/登录
- `ChatView` — SSE 流式聊天 + 人格切换 + 多会话
- `MemoryView` — Profile / Events / Episodic / Relationships / Banned / Deprecations
- `SocialGraphView` — 关系图谱 SVG 视图
- `EvalView` — 合成评测 + 真实聊天评估（chat_audit_v1）

聊天气泡每条 assistant 消息可点开 PromptDrawer 透视 `prompt_meta`。

## 开发

```bash
cd frontend
npm install
npm run dev   # 默认 http://localhost:5173 → 反代到 http://localhost:8000
```

后端默认监听 `:8000`（`uvicorn app.main:app` 或 `docker compose up backend`）。

## 构建

```bash
npm run build  # 输出到 dist/
```

环境变量 `VITE_API_BASE` 可指定后端 base URL（生产构建时使用），开发时通过 vite 代理直连。

## 品牌资产

`public/xiaobai-avatar.png` 与 `docs/rebuild/xiaobai-avatar.png` byte-identical 复制（MD5 一致），不要重画。
