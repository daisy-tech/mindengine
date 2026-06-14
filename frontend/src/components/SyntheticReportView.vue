<script setup lang="ts">
/**
 * 合成评测报告的可读视图。
 *
 * 后端把 SyntheticReport.cases 吐成一坨结构化 JSON,直接 <pre> 出来对人很
 * 不友好。这个组件做的事:
 *
 *   1. 顶部摘要:通过率/总耗时/各人格分布
 *   2. 失败 case 默认展开,逐条解释「输入是啥、期望啥、实际啥、错在哪条 check」
 *   3. 通过 case 折叠成一行紧凑列表
 *   4. 意图混淆矩阵小表(可折叠)
 *
 * 每条 check 的 name + detail 都翻成中文(见 ``CHECK_LABELS`` /
 * ``humanizeDetail``)—— 后端 detail 形如 ``"got 'casual', expected
 * ['memory_challenge']"``,直接给人看不行。
 */
import { computed } from 'vue';
import type { SyntheticReport } from '@/api/eval';

interface CaseCheck {
  name: string;
  passed: boolean;
  detail: string;
}

interface CaseHistory {
  role: 'user' | 'assistant';
  content: string;
}

interface CaseExpected {
  intents?: string[];
  optional_intents?: string[];
  must_activate_keywords?: string[];
  must_contain?: string[];
  forbidden_phrases_in_reply?: string[];
  forbidden_phrases_in_system?: string[];
}

interface CaseRow {
  id: string;
  personality: string;
  tags?: string[];
  user?: string;
  history?: CaseHistory[];
  expected?: CaseExpected;
  passed: boolean;
  intent_actual: string;
  intent_source: string;
  reply: string;
  checks: CaseCheck[];
  elapsed_ms: number;
  error: string | null;
}

const props = defineProps<{ report: SyntheticReport }>();

const CHECK_LABELS: Record<string, string> = {
  intent: '意图分类',
  must_activate: '关键信息激活',
  must_contain: '回复必含词',
  forbidden_in_reply: '回复禁用词',
  forbidden_in_system: '系统提示禁用词',
  no_error: '无运行异常',
};

const PERSONALITY_LABELS: Record<string, string> = {
  balanced: '均衡',
  introvert: '内向',
  lighthearted: '轻松',
};

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

const cases = computed<CaseRow[]>(
  () => (props.report.cases as unknown as CaseRow[]) || [],
);
const failedCases = computed(() => cases.value.filter((c) => !c.passed));
const passedCases = computed(() => cases.value.filter((c) => c.passed));

const totalElapsedMs = computed(() =>
  cases.value.reduce((acc, c) => acc + (c.elapsed_ms || 0), 0),
);

const personalityCounts = computed(() => {
  const out: Record<string, { total: number; passed: number }> = {};
  for (const c of cases.value) {
    const p = c.personality;
    if (!out[p]) out[p] = { total: 0, passed: 0 };
    out[p].total += 1;
    if (c.passed) out[p].passed += 1;
  }
  return out;
});

const confusionRows = computed(() => {
  const cm = props.report.intent_confusion_matrix || {};
  const rows: Array<{
    expected: string;
    breakdown: Array<{ actual: string; count: number; matched: boolean }>;
    total: number;
  }> = [];
  for (const [expected, byActual] of Object.entries(cm)) {
    const total = Object.values(byActual).reduce((a, b) => a + b, 0);
    const breakdown = Object.entries(byActual)
      .sort((a, b) => b[1] - a[1])
      .map(([actual, count]) => ({
        actual,
        count,
        matched: actual === expected,
      }));
    rows.push({ expected, breakdown, total });
  }
  return rows;
});

function intentLabel(value: string): string {
  return INTENT_LABELS[value] ?? value;
}

function personalityLabel(value: string): string {
  return PERSONALITY_LABELS[value] ?? value;
}

/**
 * 把后端 check.detail 那种 ``"got 'casual', expected ['memory_challenge']"``
 * 翻译成人话。失败时拆出"实际/期望"做高亮。
 */
function humanizeDetail(check: CaseCheck): string {
  if (check.passed || !check.detail) return check.detail || '';
  const d = check.detail;

  // intent: "got 'casual', expected ['memory_challenge']"
  const intentMatch = d.match(/^got '([^']*)', expected (\[.*\])$/);
  if (intentMatch) {
    let expected: string[] = [];
    try {
      expected = JSON.parse(intentMatch[2].replace(/'/g, '"'));
    } catch {
      /* leave as-is */
    }
    const actualLabel = intentLabel(intentMatch[1]);
    const expectedLabels = expected.map(intentLabel).join(' / ');
    return `识别为「${actualLabel}」,但期望是「${expectedLabels}」`;
  }

  const missingActivate = d.match(/^missing \[(.+)\]$/);
  if (missingActivate && check.name === 'must_activate') {
    return `未激活的关键词:${cleanList(missingActivate[1])}`;
  }

  const replyMissing = d.match(/^reply missing \[(.+)\]$/);
  if (replyMissing) {
    return `回复未包含必含词:${cleanList(replyMissing[1])}`;
  }

  const leaked = d.match(/^leaked \[(.+)\]$/);
  if (leaked) {
    return `回复混入了禁用词:${cleanList(leaked[1])}`;
  }

  const sysLeaked = d.match(/^system leaked \[(.+)\]$/);
  if (sysLeaked) {
    return `系统提示中出现了禁用词:${cleanList(sysLeaked[1])}`;
  }

  if (check.name === 'no_error') {
    return d.replace(/^chat error:\s*/, '运行异常:');
  }

  return d;
}

function cleanList(raw: string): string {
  return raw.replace(/'/g, '').replace(/"/g, '').replace(/,\s*/g, '、');
}

function relevantChecks(c: CaseRow): CaseCheck[] {
  // 失败的 check 排前,然后只保留有 detail 或失败的;通过且无 detail 的(name=must_activate
  // 等没期望)对人来说没价值,藏掉。
  return c.checks
    .filter((x) => !x.passed || x.detail)
    .sort((a, b) => Number(a.passed) - Number(b.passed));
}

function failedCheckCount(c: CaseRow): number {
  return c.checks.filter((x) => !x.passed).length;
}

function fmtMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function fmtSec(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds - m * 60);
  return `${m} 分 ${s} 秒`;
}

const totalElapsedSec = computed(() => totalElapsedMs.value / 1000);
</script>

<template>
  <div class="report">
    <!-- 顶部摘要 -->
    <div class="summary-row">
      <div class="stat">
        <div class="stat-label">通过率</div>
        <div
          class="stat-value"
          :class="{ ok: report.pass_rate >= 0.85, bad: report.pass_rate < 0.7 }"
        >
          {{ (report.pass_rate * 100).toFixed(1) }}%
        </div>
        <div class="stat-sub">{{ report.passed }} / {{ report.total }} 通过</div>
      </div>
      <div class="stat">
        <div class="stat-label">用例集</div>
        <div class="stat-value mono">{{ report.run_type }}</div>
        <div class="stat-sub">run_id {{ report.run_id }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">耗时</div>
        <div class="stat-value">{{ fmtSec(totalElapsedSec) }}</div>
        <div class="stat-sub">单 case 平均 {{ Math.round(totalElapsedMs / Math.max(report.total, 1)) }}ms</div>
      </div>
      <div class="stat">
        <div class="stat-label">人格分布</div>
        <div class="personality-grid">
          <div v-for="(v, k) in personalityCounts" :key="k" class="personality-row">
            <span class="dim">{{ personalityLabel(k) }}</span>
            <span class="mono">{{ v.passed }}/{{ v.total }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 失败 case -->
    <section v-if="failedCases.length" class="block">
      <h4 class="block-title fail">
        失败用例 · {{ failedCases.length }} 个
        <span class="block-hint">默认展开,优先看这里</span>
      </h4>
      <el-collapse :model-value="failedCases.map((c) => c.id)">
        <el-collapse-item
          v-for="c in failedCases"
          :key="c.id"
          :name="c.id"
        >
          <template #title>
            <div class="case-title">
              <span class="badge badge-fail">失败</span>
              <span class="case-id">{{ c.id }}</span>
              <span class="dim mono">·</span>
              <span class="dim">{{ personalityLabel(c.personality) }}</span>
              <span class="dim mono">·</span>
              <span class="dim">{{ failedCheckCount(c) }} 项不通过</span>
              <span class="dim mono">·</span>
              <span class="dim">{{ fmtMs(c.elapsed_ms) }}</span>
            </div>
          </template>

          <div class="case-body">
            <!-- 输入 -->
            <div class="row">
              <div class="row-label">用户输入</div>
              <div class="row-value">
                <div v-if="c.history && c.history.length" class="history-block">
                  <div
                    v-for="(h, idx) in c.history"
                    :key="idx"
                    class="history-line"
                    :class="h.role"
                  >
                    <span class="role-tag">{{ h.role === 'user' ? '我' : '小白' }}</span>
                    <span>{{ h.content }}</span>
                  </div>
                </div>
                <div class="user-msg">{{ c.user }}</div>
              </div>
            </div>

            <!-- 模型回复 -->
            <div class="row">
              <div class="row-label">小白回复</div>
              <div class="row-value">
                <div v-if="c.error" class="error-bubble">运行异常:{{ c.error }}</div>
                <div v-else class="reply-bubble">{{ c.reply || '(空)' }}</div>
                <div class="reply-meta dim">
                  识别意图 <strong>{{ intentLabel(c.intent_actual) }}</strong>
                  <span v-if="c.intent_source" class="mono">（{{ c.intent_source }}）</span>
                </div>
              </div>
            </div>

            <!-- 失败原因 -->
            <div class="row">
              <div class="row-label">检查结果</div>
              <div class="row-value">
                <div
                  v-for="chk in relevantChecks(c)"
                  :key="chk.name"
                  class="check-line"
                  :class="{ pass: chk.passed, fail: !chk.passed }"
                >
                  <span class="check-mark">{{ chk.passed ? '✓' : '✗' }}</span>
                  <span class="check-name">{{ CHECK_LABELS[chk.name] ?? chk.name }}</span>
                  <span v-if="chk.detail" class="check-detail">
                    {{ humanizeDetail(chk) }}
                  </span>
                </div>
              </div>
            </div>

            <!-- 期望(仅当失败时铺开) -->
            <details class="raw-details">
              <summary>原始期望 + 原始 detail</summary>
              <pre class="raw-pre">{{ JSON.stringify({
                expected: c.expected,
                checks: c.checks,
                tags: c.tags,
              }, null, 2) }}</pre>
            </details>
          </div>
        </el-collapse-item>
      </el-collapse>
    </section>

    <!-- 通过 case -->
    <section v-if="passedCases.length" class="block">
      <h4 class="block-title pass">
        通过用例 · {{ passedCases.length }} 个
        <span class="block-hint">折叠展示</span>
      </h4>
      <details>
        <summary class="passed-summary">展开查看通过列表</summary>
        <div class="passed-grid">
          <div v-for="c in passedCases" :key="c.id" class="passed-row">
            <span class="badge badge-pass">通过</span>
            <span class="case-id">{{ c.id }}</span>
            <span class="dim">{{ personalityLabel(c.personality) }}</span>
            <span class="dim">{{ intentLabel(c.intent_actual) }}</span>
            <span class="dim mono">{{ fmtMs(c.elapsed_ms) }}</span>
          </div>
        </div>
      </details>
    </section>

    <!-- 混淆矩阵 -->
    <section v-if="confusionRows.length" class="block">
      <details>
        <summary class="block-title">意图混淆矩阵 ({{ confusionRows.length }} 行) · 折叠</summary>
        <div class="cm-table">
          <div v-for="row in confusionRows" :key="row.expected" class="cm-row">
            <div class="cm-expected">
              期望 <strong>{{ intentLabel(row.expected) }}</strong>
              <span class="dim mono">×{{ row.total }}</span>
            </div>
            <div class="cm-breakdown">
              <span
                v-for="b in row.breakdown"
                :key="b.actual"
                class="cm-cell"
                :class="{ matched: b.matched }"
              >
                {{ intentLabel(b.actual) }} <span class="mono">×{{ b.count }}</span>
              </span>
            </div>
          </div>
        </div>
      </details>
    </section>
  </div>
</template>

<style scoped>
.report {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.summary-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.stat {
  background: var(--xb-card);
  border: 1px solid var(--xb-border);
  border-radius: 10px;
  padding: 12px 14px;
}
.stat-label {
  font-size: 11px;
  color: var(--xb-muted);
  margin-bottom: 4px;
}
.stat-value {
  font-size: 22px;
  font-weight: 600;
  color: var(--xb-text);
}
.stat-value.ok {
  color: #16a34a;
}
.stat-value.bad {
  color: #dc2626;
}
.stat-sub {
  font-size: 11px;
  color: var(--xb-muted);
  margin-top: 4px;
}

.personality-grid {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.personality-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--xb-text);
}

.block {
  background: var(--xb-card);
  border: 1px solid var(--xb-border);
  border-radius: 12px;
  padding: 14px 16px;
}
.block-title {
  margin: 0 0 10px;
  font-size: 14px;
  color: var(--xb-text);
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.block-title.fail {
  color: #dc2626;
}
.block-title.pass {
  color: #16a34a;
}
.block-hint {
  font-size: 11px;
  color: var(--xb-muted);
  font-weight: 400;
}

.case-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  flex-wrap: wrap;
}
.case-id {
  font-family: 'JetBrains Mono', Menlo, monospace;
  font-size: 12px;
  color: var(--xb-text);
}
.badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
}
.badge-fail {
  background: #fee2e2;
  color: #b91c1c;
}
.badge-pass {
  background: #dcfce7;
  color: #15803d;
}
.dim {
  color: var(--xb-muted);
  font-size: 12px;
}
.mono {
  font-family: 'JetBrains Mono', Menlo, monospace;
}

.case-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 6px 4px 2px 4px;
}
.row {
  display: grid;
  grid-template-columns: 88px 1fr;
  gap: 12px;
}
.row-label {
  font-size: 12px;
  color: var(--xb-muted);
  padding-top: 4px;
}
.row-value {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.history-block {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  background: #f9fafb;
  border-radius: 8px;
  border: 1px solid var(--xb-border);
}
.history-line {
  font-size: 12px;
  color: var(--xb-text);
}
.history-line.user {
  color: var(--xb-text);
}
.history-line.assistant {
  color: var(--xb-purple-dark);
}
.role-tag {
  display: inline-block;
  width: 36px;
  font-size: 11px;
  color: var(--xb-muted);
}

.user-msg {
  font-size: 13px;
  font-weight: 500;
  color: var(--xb-text);
  background: var(--xb-purple-soft);
  padding: 8px 12px;
  border-radius: 8px;
}
.reply-bubble {
  font-size: 13px;
  background: #f3f4f6;
  padding: 8px 12px;
  border-radius: 8px;
  white-space: pre-wrap;
}
.error-bubble {
  background: #fef2f2;
  color: #b91c1c;
  border: 1px solid #fca5a5;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 13px;
}
.reply-meta {
  font-size: 11px;
}

.check-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 13px;
}
.check-line.fail .check-name {
  color: #b91c1c;
  font-weight: 500;
}
.check-line.pass .check-name {
  color: var(--xb-muted);
}
.check-mark {
  width: 14px;
  font-weight: 700;
}
.check-line.pass .check-mark {
  color: #16a34a;
}
.check-line.fail .check-mark {
  color: #dc2626;
}
.check-name {
  min-width: 110px;
}
.check-detail {
  color: var(--xb-text);
  font-size: 12px;
}

.raw-details {
  margin-top: 4px;
}
.raw-details summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--xb-muted);
}
.raw-pre {
  background: #f9fafb;
  border: 1px solid var(--xb-border);
  border-radius: 6px;
  padding: 8px;
  font-size: 11px;
  margin-top: 6px;
  max-height: 240px;
  overflow: auto;
}

.passed-summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--xb-muted);
}
.passed-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 4px 12px;
  margin-top: 8px;
}
.passed-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  padding: 2px 0;
}

.cm-table {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 8px;
}
.cm-row {
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: 12px;
  font-size: 12px;
  padding: 6px 8px;
  border-radius: 6px;
  background: #f9fafb;
}
.cm-expected {
  color: var(--xb-text);
}
.cm-breakdown {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.cm-cell {
  background: #fff;
  border: 1px solid var(--xb-border);
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 11px;
  color: var(--xb-muted);
}
.cm-cell.matched {
  background: #dcfce7;
  border-color: #86efac;
  color: #15803d;
  font-weight: 500;
}
</style>
