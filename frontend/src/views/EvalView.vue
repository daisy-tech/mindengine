<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  deleteChatAudit,
  getChatAudit,
  listChatAuditStored,
  listSynthetic,
  startSynthetic,
  type CaseFileInfo,
  type ChatAuditReview,
  type StoredReviewSummary,
  type SyntheticReport,
} from '@/api/eval';
import { listConversations, type ConversationDTO } from '@/api/conversations';

const cases = ref<CaseFileInfo[]>([]);
const stored = ref<StoredReviewSummary[]>([]);
const conversations = ref<ConversationDTO[]>([]);
const lastReport = ref<SyntheticReport | null>(null);
const lastReview = ref<ChatAuditReview | null>(null);
const reviewingId = ref('');
const runningCase = ref('');
const reviewLoading = ref(false);

async function refreshAll() {
  try {
    const [cs, st, convs] = await Promise.all([
      listSynthetic(),
      listChatAuditStored(),
      listConversations(),
    ]);
    cases.value = cs;
    stored.value = st;
    conversations.value = convs;
  } catch (e) {
    console.error(e);
    ElMessage.error('加载评测数据失败');
  }
}

onMounted(refreshAll);

async function runCase(name: string) {
  runningCase.value = name;
  try {
    lastReport.value = await startSynthetic(name);
    ElMessage.success(`合成评测完成：${lastReport.value.passed}/${lastReport.value.total}`);
  } catch (e: unknown) {
    const detail =
      e && typeof e === 'object' && 'response' in e
        ? // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (e as any).response?.data?.detail ?? (e as any).message
        : String(e);
    ElMessage.error(detail || '评测失败');
  } finally {
    runningCase.value = '';
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

        <div v-if="lastReport" class="card" style="margin-top:18px">
          <h3 style="margin:0;color:var(--xb-purple-dark)">
            上次结果 · {{ lastReport.run_type }}
          </h3>
          <div style="margin:6px 0;color:var(--xb-muted)">
            通过率 <strong>{{ (lastReport.pass_rate * 100).toFixed(1) }}%</strong>
            （{{ lastReport.passed }}/{{ lastReport.total }}）
          </div>
          <details>
            <summary style="cursor:pointer">展开 case-by-case</summary>
            <pre style="font-size:11px;max-height:280px;overflow:auto">
{{ JSON.stringify(lastReport.cases, null, 2) }}
            </pre>
          </details>
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
