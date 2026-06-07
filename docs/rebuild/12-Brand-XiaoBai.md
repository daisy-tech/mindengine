# 品牌 · 小白（XiaoBai）

> MindEngine 的 AI 聊天伙伴正式名称：**小白**（英文标识 **XiaoBai**）。
> legacy MindMem 使用 **MemoBot**，MindEngine 重构后统一改名，**头像沿用现有设计，不重画**。

---

## 1. 命名对照

| 语境 | legacy MindMem | MindEngine |
|---|---|---|
| 中文名 | MemoBot（无正式中文名） | **小白** |
| 英文/代码标识 | MemoBot / memobot | **XiaoBai** / xiaobai |
| system prompt 自称 | 「你是 MemoBot…」 | 「你是小白（XiaoBai）…」 |
| 前端侧边栏标题 | MemoBot | 小白 |
| 前端副标题 | AI 记忆伴侣 | AI 记忆伴侣（可保留） |
| API 文档 title | MemoBot API | MindEngine API |
| 头像文件名 | `memobot-avatar.png` | `xiaobai-avatar.png`（**同一张图**，仅改名） |

> **给 Opus 4.7**：项目名是 **MindEngine**，AI 伙伴名是 **小白 / XiaoBai**。两者不要混用。

---

## 2. 头像资产（必须保留）

### 2.1 权威资产（已随文档打包）

重构时**直接读本目录下的文件**，无需再去 legacy 仓库找图：

| 项 | 值 |
|---|---|
| **重构依赖包路径** | `docs/rebuild/xiaobai-avatar.png` ← **用这个** |
| legacy 原始路径 | `frontend/public/memobot-avatar.png`（内容相同） |
| 格式 | WebP（VP8），扩展名为 `.png` |
| 尺寸 | 800 × 1448 px |
| 大小 | ~40 KB |
| 视觉 | 紫调渐变背景上的卡通女性头像，圆角友好，与侧边栏 `#a78bfa` 主题色协调 |

**重构要求**：从 `docs/rebuild/xiaobai-avatar.png` **原样复制**到新项目 `frontend/public/`，禁止换图、禁止 AI 重生成、禁止换风格。

### 2.2 MindEngine 部署路径

新建前端项目时，把本目录的头像拷过去：

```
docs/rebuild/xiaobai-avatar.png          # 源（重构文档自带）
        ↓ 原样复制
frontend/public/xiaobai-avatar.png       # 目标（MindEngine 前端静态资源）
```

> **给 Coding Agent**：先 `Read` 或打开 `docs/rebuild/xiaobai-avatar.png` 确认视觉，再写入新项目的 `public/` 目录。不要用占位图替代。

### 2.3 前端引用点（M5 适配时需改）

legacy 代码中 4 处引用 `memobot-avatar.png`，MindEngine 前端应统一改为 `xiaobai-avatar.png` 并同步改文案：

| 文件 | 用途 | 尺寸/样式 |
|---|---|---|
| `App.vue` | 侧边栏 logo | 72×72，`border-radius: 50%`，紫色阴影 |
| `ChatView.vue` | 空会话占位图 | `.empty-avatar` |
| `ChatMessage.vue` | assistant 消息头像 | `el-avatar` 40px |
| `LoginView.vue` | 登录页顶部 | `.avatar` |

`App.vue` 侧边栏文案同步：

```html
<!-- legacy -->
<h2>MemoBot</h2>
<p>AI 记忆伴侣</p>

<!-- MindEngine -->
<h2>小白</h2>
<p>AI 记忆伴侣</p>
```

`alt` 属性统一：`alt="小白"`。

### 2.4 favicon（可选，非必须）

legacy 使用默认 `vite.svg`。若后续要换 favicon，应从 `xiaobai-avatar.png` 裁切 32×32 / 192×192，**不要另找图**。

---

## 3. Prompt 中的自称

`BASE_PERSONA` 第一句必须使用小白，参见 [04-Subsystem-Personality.md](./04-Subsystem-Personality.md) §5。

抽取/纠错 prompt 里凡写「MemoBot」的地方，MindEngine 一律改为「小白」：

| 模块 | legacy 写法 | MindEngine 写法 |
|---|---|---|
| `prompt_composer` BASE_PERSONA | 你是 MemoBot | 你是小白（XiaoBai） |
| `correction_engine` judge prompt | 纠正了 MemoBot 的某个说法 | 纠正了小白的某个说法 |
| `celery_worker` extract prompt | 向 MemoBot 透露 | 向小白透露 |
| `selftest` 断言 | `"MemoBot" in prompt` | `"小白" in prompt` |

---

## 4. 验收

| # | 检查项 | 通过条件 |
|---|---|---|
| 1 | 头像文件存在 | 新项目 `public/xiaobai-avatar.png` 与 `docs/rebuild/xiaobai-avatar.png` 字节一致 |
| 2 | 四处 UI 显示正常 | 侧边栏 / 登录 / 空态 / 聊天气泡均显示头像 |
| 3 | prompt 自称 | BASE_PERSONA 含「小白」，不含「MemoBot」 |
| 4 | 侧边栏标题 | 显示「小白」，不显示「MemoBot」 |
| 5 | API 文档 | OpenAPI title 为 MindEngine，不是 MemoBot |

---

## 5. 与 legacy 的差异

| 项 | legacy MindMem | MindEngine |
|---|---|---|
| AI 名称 | MemoBot | 小白（XiaoBai） |
| 头像 | memobot-avatar.png | xiaobai-avatar.png（同图） |
| 项目名 | MindMem | MindEngine |
| 三者关系 | 混用较多 | 严格三分：MindEngine=产品，小白=AI，头像=品牌视觉 |
