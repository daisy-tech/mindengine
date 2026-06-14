<script setup lang="ts">
import { onMounted, ref } from 'vue';
import {
  deleteChatAudit,
  deleteStoredSynthetic,
  getChatAudit,
  getStoredSynthetic,
  listChatAuditStored,
  listStoredSynthetic,
  listSynthetic,
  startSynthetic,
  type CaseFileInfo,
  type ChatAuditReview,
  type StoredReviewSummary,
  type StoredSyntheticSummary,
  type SyntheticReport,
} from '@/api/eval';
import { listConversations, type ConversationDTO } from '@/api/conversations';
import SyntheticReportView from '@/components/SyntheticReportView.vue';

const cases = ref<CaseFileInfo[]>([]);
const stored = ref<StoredReviewSummary[]>([]);
const conversations = ref<ConversationDTO[]>([]);
const lastReport = ref<SyntheticReport | null>(null);
const lastReview = ref<ChatAuditReview | null>(null);
const reviewingId = ref('');
const runningCase = ref('');
const reviewLoading = ref(false);

const storedSynth = ref<StoredSyntheticSummary[]>([]);
const viewingRunId = ref('');

async function refreshAll() {
  try {
    const [cs, st, convs, ss] = await Promise.all([
      listSynthetic(),
      listChatAuditStored(),
      listConversations(),
      listStoredSynthetic(),
    ]);
    cases.value = cs;
    stored.value = st;
    conversations.value = convs;
    storedSynth.value = ss;
  } catch (e) {
    console.error(e);
    ElMessage.error('加载评测数据失败');
  }
}

onMounted(refreshAll);

async function runCase(name: string) {
  runningCase.value = name;
  // Smoke ≈ 1~2 分钟,full ≈ 3~6 分钟,UI 一直没反馈用户会以为卡死,
  // 这里给一个不消失的 loading message,跑完后手动关掉。
  const loadingMsg = ElMessage({
    message: `${name} 评测进行中,LLM 串行跑全部 case,大约 1~6 分钟,请勿关闭页面…`,
    type: 'info',
    duration: 0,
    showClose: true,
  });
  try {
    lastReport.value = await startSynthetic(name);
    viewingRunId.value = lastReport.value.run_id;
    ElMessage.success(
      `${name} 评测完成:${lastReport.value.passed}/${lastReport.value.total} 通过 ` +
        `(${(lastReport.value.pass_rate * 100).toFixed(1)}%) · 已自动保存`,
    );
    // 跑完后刷新历史列表,新 run_id 立刻出现在「历史评测」里。
    storedSynth.value = await listStoredSynthetic();
  } catch (e: unknown) {
    const detail =
      e && typeof e === 'object' && 'response' in e
        ? // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (e as any).response?.data?.detail ?? (e as any).message
        : String(e);
    ElMessage.error(detail || '评测失败');
  } finally {
    runningCase.value = '';
    loadingMsg.close();
  }
}

async function viewStoredRun(runId: string) {
  try {
    lastReport.value = await getStoredSynthetic(runId);
    viewingRunId.value = runId;
    ElMessage.success('已加载该次评测结果');
  } catch (e) {
    console.error(e);
    ElMessage.error('加载失败');
  }
}

async function dropStoredRun(runId: string) {
  await ElMessageBox.confirm('删除这次评测的落盘报告?不影响历史/当前会话。', '确认', {
    confirmButtonText: '删除',
    cancelButtonText: '取消',
    type: 'warning',
  })
    .then(async () => {
      await deleteStoredSynthetic(runId);
      ElMessage.success('已删除');
      if (viewingRunId.value === runId) {
        lastReport.value = null;
        viewingRunId.value = '';
      }
      storedSynth.value = await listStoredSynthetic();
    })
    .catch(() => undefined);
}

function fmtFinishedAt(s: string): string {
  if (!s) return '—';
  try {
    return new Date(s).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return s;
  }
}

async function review(id: string, force = false) {
  reviewingId.value = id;
  reviewLoading.value = true;
  try {
    lastReview.value = await getChatAudit(id, force);
    ElMessage.success('审阅完成');
    await refreshAll();
  } catch (e: unknown) {
    const detail =
      e && typeof e === 'object' && 'response' in e
        ? // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (e as any).response?.data?.detail ?? (e as any).message
        : String(e);
    ElMessage.error(detail || '审阅失败');
  } finally {
    reviewLoading.value = false;
  }
}

async function dropStored(id: string) {
  await ElMessageBox.confirm('删除这份评测落盘？', '确认', {
    confirmButtonText: '删除',
    cancelButtonText: '取消',
    type: 'warning',
  })
    .then(async () => {
      await deleteChatAudit(id);
      ElMessage.success('已删除');
      if (reviewingId.value === id) {
        lastReview.value = null;
      }
      await refreshAll();
    })
    .catch(() => undefined);
}
</script>

<template>
  <div class="eval-shell">
    <el-tabs type="border-card">
      <el-tab-pane label="合成评测">
        <div class="eval-grid">
          <div v-for="c in cases" :key="c.name" class="card">
            <h3 style="margin:0;color:var(--xb-purple-dark)">{{ c.name }}</h3>
            <p style="color:var(--xb-muted);margin:6px 0">用例数：{{ c.case_count }}</p>
            <p style="font-size:11px;color:#9ca3af;word-break:break-all">{{ c.path }}</p>
            <el-button
              type="primary"
              size="small"
              :loading="runningCase === c.name"
              @click="runCase(c.name)"
            >
              开始评测
            </el-button>
          </div>
        </div>

        <div v-if="storedSynth.length" class="card" style="margin-top:18px">
          <h3 style="margin:0 0 8px;color:var(--xb-purple-dark)">
            历史评测 · {{ storedSynth.length }}
          </h3>
          <p style="color:var(--xb-muted);font-size:12px;margin:0 0 8px">
            每次跑完会自动保存,点「查看」立刻浏览,无需重跑
          </p>
          <el-table :data="storedSynth" size="small" stripe>
            <el-table-column prop="run_type" label="用例集" width="140" />
            <el-table-column label="完成时间" width="200">
              <template #default="{ row }">{{ fmtFinishedAt(row.finished_at) }}</template>
            </el-table-column>
            <el-table-column label="通过率" width="120">
              <template #default="{ row }">
                <span
                  :class="{
                    'rate-good': row.pass_rate >= 0.85,
                    'rate-bad': row.pass_rate < 0.7,
                  }"
                >
                  {{ (row.pass_rate * 100).toFixed(1) }}% ({{ row.passed }}/{{ row.total }})
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="run_id" label="run_id" />
            <el-table-column label="操作" width="160">
              <template #default="{ row }">
                <el-button
                  size="small"
                  :type="viewingRunId === row.run_id ? 'success' : 'primary'"
                  @click="viewStoredRun(row.run_id)"
                >
                  {{ viewingRunId === row.run_id ? '当前' : '查看' }}
                </el-button>
                <el-button size="small" type="danger" link @click="dropStoredRun(row.run_id)">
                  删除
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <div v-if="lastReport" style="margin-top:18px">
          <SyntheticReportView :report="lastReport" />
        </div>
      </el-tab-pane>

      <el-tab-pane label="真实聊天评估">
        <div class="eval-grid">
          <div class="card">
            <h3 style="margin:0;color:var(--xb-purple-dark)">我的会话</h3>
            <p style="color:var(--xb-muted);margin:6px 0;font-size:12px">
              选择一个会话生成或重跑 chat_audit_v1 报告
            </p>
            <el-table :data="conversations" size="small" style="margin-top:8px">
              <el-table-column prop="title" label="标题" />
              <el-table-column prop="updated_at" label="最近更新" width="180" />
              <el-table-column label="操作" width="180">
                <template #default="{ row }">
                  <el-button size="small" @click="review(row.id, false)">审阅</el-button>
                  <el-button size="small" type="primary" @click="review(row.id, true)">重跑</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <div class="card">
            <h3 style="margin:0;color:var(--xb-purple-dark)">已落盘评测</h3>
            <el-table :data="stored" size="small" style="margin-top:8px">
              <el-table-column prop="conversation_id" label="会话" />
              <el-table-column prop="evaluable_turns" label="评估轮次" width="100" />
              <el-table-column label="ok 率" width="100">
                <template #default="{ row }">
                  {{ (row.final_ok_rate * 100).toFixed(0) }}%
                </template>
              </el-table-column>
              <el-table-column label="操作" width="160">
                <template #default="{ row }">
                  <el-button size="small" @click="review(row.conversation_id, false)">查看</el-button>
                  <el-button size="small" type="danger" link @click="dropStored(row.conversation_id)">
                    删除
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </div>

        <div v-if="lastReview" class="card" style="margin-top:18px" v-loading="reviewLoading">
          <h3 style="margin:0;color:var(--xb-purple-dark)">
            最近一次审阅 · {{ reviewingId }}
          </h3>
          <div style="margin:6px 0;color:var(--xb-muted)">
            structure pass {{ (lastReview.review.structure_pass_rate * 100).toFixed(0) }}%
            · final ok {{ (lastReview.review.final_ok_rate * 100).toFixed(0) }}%
            · {{ lastReview.review.evaluable_turns }} 轮可评
          </div>
          <details open>
            <summary style="cursor:pointer">归因 root_cause_top</summary>
            <pre style="font-size:11px">{{ JSON.stringify(lastReview.review.root_cause_top, null, 2) }}</pre>
          </details>
          <details>
            <summary style="cursor:pointer">逐轮明细 turns</summary>
            <pre style="font-size:11px;max-height:300px;overflow:auto">{{
              JSON.stringify(lastReview.turns, null, 2)
            }}</pre>
          </details>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
