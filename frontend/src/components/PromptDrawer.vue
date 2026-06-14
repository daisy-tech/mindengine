<script setup lang="ts">
/**
 * 「本轮 prompt_meta」抽屉。
 *
 * 把后端 `messages.meta_json`(原始 JSON)翻译成普通用户能看懂的中文卡片:
 *   - 路由判定(意图/来源/置信度/人格/敏感/加载层)
 *   - 激活的记忆(逐条来源 + 摘要)
 *   - 记忆池快照(各层规模)
 *   - System Prompt 段(中文标签 + 自然分段渲染)
 *   - 契约后置(是否截断 / 去问 / 字符数)
 *   - 请求参数(model / 温度 / 思考链)
 *   - 末尾保留「原始 JSON」<details> 给排查用
 *
 * 不依赖外部 markdown 库 —— 后端 system_excerpt 已经是
 * "≪段落标题≫\n正文\n\n≪…≫\n…" 的中文格式,我们按 `\n\n` 切块、
 * 把首行 ≪…≫ 当作小标题就够了。
 */
import { computed, ref, watch } from 'vue';
import {
  getMessagePrompt,
  type PromptArchiveDTO,
} from '@/api/conversations';

interface Props {
  modelValue: boolean;
  meta: Record<string, unknown> | null;
  // 触发本抽屉的那条 assistant 消息 + 它所属会话。两者都为空时
  // 「加载完整 prompt」按钮被禁用(老消息 / 流程异常)。
  conversationId?: string | null;
  messageId?: string | null;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void;
}>();

const show = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
});

// ─── 完整 prompt 归档(按需加载) ────────────────────────────

const archive = ref<PromptArchiveDTO | null>(null);
const archiveLoading = ref(false);
const archiveError = ref<string>('');

const canFetchArchive = computed(
  () => Boolean(props.conversationId && props.messageId),
);

// 关掉抽屉 / 切换消息时重置归档,避免下次打开时短暂闪到上一条。
watch(
  () => [props.modelValue, props.messageId],
  () => {
    archive.value = null;
    archiveError.value = '';
    archiveLoading.value = false;
  },
);

async function loadArchive() {
  if (!props.conversationId || !props.messageId) return;
  archiveLoading.value = true;
  archiveError.value = '';
  try {
    archive.value = await getMessagePrompt(
      props.conversationId,
      props.messageId,
    );
  } catch (e: unknown) {
    // axios error → 取后端 detail;否则用 message
    const detail =
      e && typeof e === 'object' && 'response' in e
        ? // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (e as any).response?.data?.detail ??
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (e as any).message
        : String(e);
    archiveError.value = detail || '加载失败';
  } finally {
    archiveLoading.value = false;
  }
}

async function copyArchiveAsText() {
  if (!archive.value) return;
  // 拼成「Markdown 风格」纯文本,贴到笔记 / LLM eval 工具直接可用
  const a = archive.value;
  const messagesPart = a.llm_messages
    .map((m) => `### ${m.role}\n${m.content}`)
    .join('\n\n');
  const text =
    `# Prompt Archive\n\n` +
    `- conversation: ${a.conversation_id}\n` +
    `- message: ${a.assistant_message_id}\n` +
    `- composed_at: ${a.composed_at}\n\n` +
    `## SYSTEM\n\n${a.system}\n\n` +
    `## MESSAGES (history + 本轮 user)\n\n${messagesPart}\n\n` +
    `## ASSISTANT REPLY\n\n${a.assistant_reply}\n`;
  try {
    await navigator.clipboard.writeText(text);
    archiveError.value = ''; // clear any previous
    // 复用 archiveError 做 toast 不合适,改成轻提示
    archiveCopied.value = true;
    setTimeout(() => (archiveCopied.value = false), 1500);
  } catch {
    archiveError.value = '复制失败,请手动选中复制';
  }
}

const archiveCopied = ref(false);

// ─── 字段中文映射 ─────────────────────────────────────────────────

const INTENT_LABELS: Record<string, string> = {
  casual: '日常闲聊',
  emotional_support: '情感支持',
  memory_challenge: '记忆问询',
  correction: '纠正记忆',
  self_summary: '自我总结',
  relationship_topic: '关系话题',
  knowledge_task: '知识任务',
  procedural_task: '执行任务',
  meta: '元对话',
};

const INTENT_SOURCE_LABELS: Record<string, string> = {
  hard_rule: '硬规则命中',
  cache: '缓存命中',
  small_model: '小模型分类',
  fallback: '兜底(分类失败)',
};

const PERSONALITY_LABELS: Record<string, string> = {
  balanced: '均衡',
  introvert: '内向',
  lighthearted: '轻松',
};

const SECTION_LABELS: Record<string, string> = {
  base_persona: '基础人设',
  hard_rules: '硬规则',
  personality_contract: '人格契约',
  time_context: '当前时间',
  profile: '用户画像',
  relationships: '人物关系',
  events: '近期事件',
  explicit_memories: '显式记忆',
  background_memories: '背景记忆',
  intent_guide: '意图指引',
  turn_rules: '本轮纪律',
};

const MEMORY_LAYER_LABELS: Record<string, string> = {
  profile: '画像',
  events: '事件',
  episodic: '情景',
  relationships: '关系',
};

const SOURCE_LABELS: Record<string, string> = {
  profile: '画像',
  event: '事件',
  episodic: '情景',
  relationship: '关系',
};

// ─── 类型 ──────────────────────────────────────────────────────────

interface RouteData {
  intent?: string;
  intent_source?: string;
  intent_confidence?: number;
  personality?: string;
  sensitive_mode?: boolean;
  load_layers?: string[];
  max_explicit_memories?: number;
  memory_depth?: string | number;
  reasons?: string[];
}

interface ActivatedItem {
  source?: string;
  ref_id?: string;
  excerpt?: string;
}

interface SnapshotStats {
  profile_total?: number;
  event_total?: number;
  episodic_total?: number;
  relationship_total?: number;
  banned_entities?: string[];
}

interface ContractEnforced {
  truncated?: boolean;
  removed_question?: boolean;
  raw_char_count?: number | null;
  final_char_count?: number | null;
}

interface LLMRequest {
  model?: string;
  temperature?: number | null;
  max_tokens?: number | null;
  enable_thinking?: boolean;
}

interface PromptSegment {
  title: string;
  body: string;
}

// ─── 解析 ──────────────────────────────────────────────────────────

const route = computed<RouteData>(() => (props.meta?.route as RouteData) ?? {});
const activated = computed<ActivatedItem[]>(
  () => (props.meta?.activated as ActivatedItem[]) ?? [],
);
const snapshot = computed<SnapshotStats>(
  () => (props.meta?.snapshot_stats as SnapshotStats) ?? {},
);
const sectionKeys = computed<string[]>(
  () => (props.meta?.section_keys as string[]) ?? [],
);
const systemExcerpt = computed<string>(
  () => (props.meta?.system_excerpt as string) ?? '',
);
const contractEnforced = computed<ContractEnforced>(
  () => (props.meta?.contract_enforced as ContractEnforced) ?? {},
);
const llmRequest = computed<LLMRequest>(
  () => (props.meta?.llm_request as LLMRequest) ?? {},
);
const composedAt = computed<string>(
  () => (props.meta?.composed_at as string) ?? '',
);
const estimatedTokens = computed<number>(
  () => (props.meta?.estimated_tokens as number) ?? 0,
);
const modelName = computed<string>(() => (props.meta?.model as string) ?? '');

/**
 * Backend writes system_excerpt as "≪段标题≫\n正文\n\n≪…≫\n正文 …",
 * so split on blank-line boundaries and lift the leading ≪…≫ line into
 * a section header. Anything without a recognizable header just renders
 * as plain text.
 */
const promptSegments = computed<PromptSegment[]>(() => {
  const raw = systemExcerpt.value;
  if (!raw) return [];
  const blocks = raw.split(/\n{2,}/g);
  return blocks
    .map((block) => block.trim())
    .filter(Boolean)
    .map((block) => {
      const lines = block.split('\n');
      const headerMatch = lines[0].match(/^≪(.+?)≫\s*$/);
      if (headerMatch) {
        return {
          title: headerMatch[1],
          body: lines.slice(1).join('\n').trim(),
        };
      }
      return { title: '', body: block };
    });
});

// ─── 工具 ──────────────────────────────────────────────────────────

function intentLabel(v?: string): string {
  return v ? INTENT_LABELS[v] ?? v : '—';
}
function intentSourceLabel(v?: string): string {
  return v ? INTENT_SOURCE_LABELS[v] ?? v : '—';
}
function personalityLabel(v?: string): string {
  return v ? PERSONALITY_LABELS[v] ?? v : '—';
}
function sectionLabel(v: string): string {
  return SECTION_LABELS[v] ?? v;
}
function layerLabel(v: string): string {
  return MEMORY_LAYER_LABELS[v] ?? v;
}
function sourceLabel(v?: string): string {
  return v ? SOURCE_LABELS[v] ?? v : '—';
}

function fmtPercent(v?: number): string {
  if (v === undefined || v === null) return '—';
  return `${(v * 100).toFixed(0)}%`;
}

function fmtTime(s: string): string {
  if (!s) return '—';
  try {
    return new Date(s).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return s;
  }
}

function fmtBool(b: boolean | undefined, yes = '是', no = '否'): string {
  if (b === undefined) return '—';
  return b ? yes : no;
}

function fmtTemperature(t: number | null | undefined): string {
  if (t === null || t === undefined) return '—';
  return t.toFixed(2);
}

function rawJson(): string {
  if (!props.meta) return '';
  try {
    return JSON.stringify(props.meta, null, 2);
  } catch {
    return String(props.meta);
  }
}
</script>

<template>
  <el-drawer v-model="show" title="本轮 prompt_meta" size="560">
    <div v-if="!meta" class="empty-hint">
      这条消息没有 prompt_meta(系统消息或客户端未持久化前的占位)。
    </div>

    <div v-else class="pm-body">
      <!-- 顶部小卡:模型 + 估算 token + 生成时间 -->
      <div class="pm-summary">
        <div>
          <div class="dim">模型</div>
          <div class="mono">{{ modelName || '—' }}</div>
        </div>
        <div>
          <div class="dim">预估 token</div>
          <div class="mono">{{ estimatedTokens || '—' }}</div>
        </div>
        <div>
          <div class="dim">生成时间</div>
          <div>{{ fmtTime(composedAt) }}</div>
        </div>
      </div>

      <!-- 路由判定 -->
      <section class="pm-block">
        <h4>路由判定</h4>
        <dl class="kv-list">
          <dt>意图</dt>
          <dd>
            <strong>{{ intentLabel(route.intent) }}</strong>
            <span class="dim mono">（{{ route.intent }}）</span>
          </dd>
          <dt>判定来源</dt>
          <dd>{{ intentSourceLabel(route.intent_source) }}</dd>
          <dt>置信度</dt>
          <dd>{{ fmtPercent(route.intent_confidence) }}</dd>
          <dt>人格</dt>
          <dd>{{ personalityLabel(route.personality) }}</dd>
          <dt>敏感模式</dt>
          <dd>{{ fmtBool(route.sensitive_mode, '开', '关') }}</dd>
          <dt>记忆深度</dt>
          <dd>{{ route.memory_depth ?? '—' }}</dd>
          <template v-if="route.load_layers?.length">
            <dt>加载层</dt>
            <dd>
              <el-tag
                v-for="l in route.load_layers"
                :key="l"
                size="small"
                type="info"
                style="margin-right: 4px"
              >
                {{ layerLabel(l) }}
              </el-tag>
            </dd>
          </template>
          <template v-if="route.reasons?.length">
            <dt>判定理由</dt>
            <dd class="reasons">
              <div v-for="(r, i) in route.reasons" :key="i" class="mono small">
                · {{ r }}
              </div>
            </dd>
          </template>
        </dl>
      </section>

      <!-- 激活的记忆 -->
      <section class="pm-block">
        <h4>激活的记忆 ({{ activated.length }})</h4>
        <div v-if="!activated.length" class="dim">本轮没有显式激活的记忆</div>
        <div v-else class="activated-list">
          <div v-for="(it, i) in activated" :key="i" class="activated-row">
            <el-tag size="small" type="success" effect="plain">
              {{ sourceLabel(it.source) }}
            </el-tag>
            <span class="excerpt">{{ it.excerpt || '(无摘要)' }}</span>
            <span class="dim mono small">{{ it.ref_id }}</span>
          </div>
        </div>
      </section>

      <!-- 记忆池快照 -->
      <section class="pm-block">
        <h4>记忆池快照</h4>
        <div class="snapshot-grid">
          <div>
            <div class="dim">画像字段</div>
            <div class="big">{{ snapshot.profile_total ?? 0 }}</div>
          </div>
          <div>
            <div class="dim">事件</div>
            <div class="big">{{ snapshot.event_total ?? 0 }}</div>
          </div>
          <div>
            <div class="dim">情景</div>
            <div class="big">{{ snapshot.episodic_total ?? 0 }}</div>
          </div>
          <div>
            <div class="dim">关系</div>
            <div class="big">{{ snapshot.relationship_total ?? 0 }}</div>
          </div>
        </div>
        <div v-if="snapshot.banned_entities?.length" style="margin-top: 8px">
          <span class="dim">封禁实体:</span>
          <el-tag
            v-for="b in snapshot.banned_entities"
            :key="b"
            size="small"
            type="danger"
            effect="plain"
            style="margin-left: 4px"
          >
            {{ b }}
          </el-tag>
        </div>
      </section>

      <!-- Prompt 组成段落 -->
      <section class="pm-block" v-if="sectionKeys.length">
        <h4>Prompt 由这些段拼出 ({{ sectionKeys.length }})</h4>
        <div class="tag-row">
          <el-tag
            v-for="k in sectionKeys"
            :key="k"
            size="small"
            type="info"
            effect="plain"
          >
            {{ sectionLabel(k) }}
          </el-tag>
        </div>
      </section>

      <!-- System Prompt 摘要(分段) -->
      <section class="pm-block" v-if="promptSegments.length">
        <h4>System Prompt 摘要</h4>
        <div class="prompt-doc">
          <div
            v-for="(seg, i) in promptSegments"
            :key="i"
            class="prompt-seg"
          >
            <div v-if="seg.title" class="prompt-seg-title">
              {{ seg.title }}
            </div>
            <pre class="prompt-seg-body">{{ seg.body }}</pre>
          </div>
        </div>
        <div class="dim small" style="margin-top: 6px">
          ⚠ 这是「摘要」(≤500 字)——
          完整 system / user / assistant 三段请点下方「加载完整 prompt」。
        </div>
      </section>

      <!-- 完整 prompt 归档(按需加载) -->
      <section class="pm-block">
        <div
          style="
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 10px;
          "
        >
          <h4 style="margin: 0">完整 Prompt(用于评估迭代)</h4>
          <div style="display: flex; gap: 6px">
            <el-button
              v-if="!archive"
              size="small"
              type="primary"
              :loading="archiveLoading"
              :disabled="!canFetchArchive"
              @click="loadArchive"
            >
              加载完整 Prompt
            </el-button>
            <el-button
              v-else
              size="small"
              type="success"
              :icon-only="false"
              @click="copyArchiveAsText"
            >
              {{ archiveCopied ? '已复制' : '复制为文本' }}
            </el-button>
            <el-button
              v-if="archive"
              size="small"
              link
              @click="loadArchive"
              :loading="archiveLoading"
            >
              重新加载
            </el-button>
          </div>
        </div>

        <div v-if="!canFetchArchive" class="dim small">
          这条消息没有关联 conversation_id / message_id,无法加载归档。
        </div>
        <div v-else-if="archiveError" class="archive-error">
          {{ archiveError }}
        </div>
        <div v-else-if="!archive" class="dim small">
          每条 assistant 回复落盘时同时把完整的 system + user + reply
          写到 ``/app/eval/exports/prompts/{user}/{conv}/{msg}.json``,
          点击上方按钮按需加载 —— 不在抽屉打开时自动拉,避免冷查带宽。
        </div>

        <div v-else class="archive-body">
          <details class="archive-section" open>
            <summary>SYSTEM(完整,{{ archive.system.length }} 字符)</summary>
            <pre class="archive-pre">{{ archive.system }}</pre>
          </details>

          <details class="archive-section" open>
            <summary>
              发给 LLM 的 messages(历史 + 本轮 user,共 {{ archive.llm_messages.length }} 条)
            </summary>
            <div class="archive-messages">
              <div
                v-for="(m, i) in archive.llm_messages"
                :key="i"
                :class="['archive-msg', `role-${m.role}`]"
              >
                <div class="archive-msg-role">{{ m.role }}</div>
                <pre class="archive-msg-body">{{ m.content }}</pre>
              </div>
            </div>
          </details>

          <details class="archive-section" open>
            <summary>
              ASSISTANT REPLY(经契约后置后,{{ archive.assistant_reply.length }} 字符)
            </summary>
            <pre class="archive-pre">{{ archive.assistant_reply }}</pre>
          </details>

          <div class="dim small" style="margin-top: 6px">
            ⓘ composed_at: {{ archive.composed_at }} ·
            user_message_id: <span class="mono">{{ archive.user_message_id }}</span>
          </div>
        </div>
      </section>

      <!-- 契约后置 -->
      <section class="pm-block">
        <h4>人格契约后置(回复审查)</h4>
        <dl class="kv-list">
          <dt>是否截断</dt>
          <dd>{{ fmtBool(contractEnforced.truncated) }}</dd>
          <dt>是否去问</dt>
          <dd>{{ fmtBool(contractEnforced.removed_question) }}</dd>
          <dt>原文字数</dt>
          <dd>{{ contractEnforced.raw_char_count ?? '—' }}</dd>
          <dt>最终字数</dt>
          <dd>{{ contractEnforced.final_char_count ?? '—' }}</dd>
        </dl>
      </section>

      <!-- 请求参数 -->
      <section class="pm-block">
        <h4>LLM 请求参数</h4>
        <dl class="kv-list">
          <dt>模型</dt>
          <dd class="mono">{{ llmRequest.model || '—' }}</dd>
          <dt>温度</dt>
          <dd>{{ fmtTemperature(llmRequest.temperature) }}</dd>
          <dt>max_tokens</dt>
          <dd>{{ llmRequest.max_tokens ?? '—' }}</dd>
          <dt>开启思考链</dt>
          <dd>{{ fmtBool(llmRequest.enable_thinking) }}</dd>
        </dl>
      </section>

      <!-- 原始 JSON 兜底 -->
      <details class="pm-block raw-fallback">
        <summary>原始 meta_json(给排查用)</summary>
        <pre class="raw-pre">{{ rawJson() }}</pre>
      </details>
    </div>
  </el-drawer>
</template>

<style scoped>
.empty-hint {
  text-align: center;
  color: var(--xb-muted);
  padding: 80px 24px;
}

.pm-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-bottom: 24px;
}

.pm-summary {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  padding: 12px 14px;
  background: var(--xb-card);
  border: 1px solid var(--xb-border);
  border-radius: 10px;
}
.pm-summary .dim {
  font-size: 11px;
  color: var(--xb-muted);
  margin-bottom: 4px;
}

.pm-block {
  background: var(--xb-card);
  border: 1px solid var(--xb-border);
  border-radius: 10px;
  padding: 14px 16px;
}
.pm-block h4 {
  margin: 0 0 10px;
  font-size: 14px;
  color: var(--xb-purple-dark);
}

.dim {
  color: var(--xb-muted);
}
.mono {
  font-family: 'JetBrains Mono', Menlo, monospace;
}
.small {
  font-size: 11px;
}
.big {
  font-size: 22px;
  font-weight: 600;
  color: var(--xb-text);
}

.kv-list {
  display: grid;
  grid-template-columns: 100px 1fr;
  gap: 6px 12px;
  margin: 0;
}
.kv-list dt {
  color: var(--xb-muted);
  font-size: 12px;
}
.kv-list dd {
  margin: 0;
  font-size: 13px;
  color: var(--xb-text);
  word-break: break-word;
}

.reasons {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.activated-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.activated-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  flex-wrap: wrap;
}
.activated-row .excerpt {
  flex: 1;
  min-width: 200px;
  color: var(--xb-text);
}

.snapshot-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}
.snapshot-grid .big {
  font-size: 20px;
}

.tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.prompt-doc {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px 14px;
  background: #f9fafb;
  border-radius: 8px;
  border: 1px solid var(--xb-border);
}
.prompt-seg-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--xb-purple-dark);
  margin-bottom: 4px;
}
.prompt-seg-body {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.55;
  color: var(--xb-text);
}

.raw-fallback summary {
  cursor: pointer;
  color: var(--xb-muted);
  font-size: 12px;
}
.raw-pre {
  margin: 8px 0 0;
  font-size: 11px;
  font-family: 'JetBrains Mono', Menlo, monospace;
  background: #f9fafb;
  border: 1px solid var(--xb-border);
  border-radius: 6px;
  padding: 8px;
  max-height: 320px;
  overflow: auto;
}

/* 完整 prompt 归档区 */
.archive-error {
  color: #b91c1c;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
}
.archive-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.archive-section > summary {
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  color: var(--xb-purple-dark);
  padding: 4px 0;
}
.archive-pre {
  margin: 6px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.55;
  background: #f9fafb;
  border: 1px solid var(--xb-border);
  border-radius: 6px;
  padding: 10px;
  max-height: 500px;
  overflow: auto;
}
.archive-messages {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 6px;
}
.archive-msg {
  border: 1px solid var(--xb-border);
  border-radius: 6px;
  padding: 8px 10px;
  background: #f9fafb;
}
.archive-msg.role-user {
  background: #eef2ff;
  border-color: #c7d2fe;
}
.archive-msg.role-assistant {
  background: #f0fdf4;
  border-color: #bbf7d0;
}
.archive-msg.role-system {
  background: #fefce8;
  border-color: #fde68a;
}
.archive-msg-role {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  color: var(--xb-muted);
  margin-bottom: 4px;
}
.archive-msg-body {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.5;
  color: var(--xb-text);
}
</style>
