<script setup lang="ts">
import { onMounted, ref } from 'vue';
import {
  addBanned,
  deleteEpisodic,
  getProfile,
  listBanned,
  listDeprecations,
  listEpisodic,
  listEvents,
  listRelationships,
  type BannedEntityDTO,
  type DeprecationDTO,
  type EpisodicMemory,
  type Event,
  type Profile,
  type Relationship,
} from '@/api/memory';

const profile = ref<Profile | null>(null);
const events = ref<Event[]>([]);
const episodic = ref<EpisodicMemory[]>([]);
const relationships = ref<Relationship[]>([]);
const banned = ref<BannedEntityDTO[]>([]);
const deprecations = ref<DeprecationDTO[]>([]);
const loading = ref(false);

const newBanned = ref('');

async function loadAll() {
  loading.value = true;
  try {
    const [p, e, ep, rel, ban, dep] = await Promise.all([
      getProfile(),
      listEvents({ limit: 100 }),
      listEpisodic(100),
      listRelationships(),
      listBanned(),
      listDeprecations(100),
    ]);
    profile.value = p;
    events.value = e;
    episodic.value = ep;
    relationships.value = rel;
    banned.value = ban;
    deprecations.value = dep;
  } catch (err) {
    console.error(err);
    ElMessage.error('加载记忆数据失败');
  } finally {
    loading.value = false;
  }
}

onMounted(loadAll);

async function dropEpisodic(id: string) {
  await ElMessageBox.confirm('删除这条情景记忆？这是软删除，会进入审计。', '确认', {
    confirmButtonText: '删除',
    cancelButtonText: '取消',
    type: 'warning',
  })
    .then(async () => {
      await deleteEpisodic(id);
      ElMessage.success('已删除');
      await loadAll();
    })
    .catch(() => undefined);
}

async function addBannedEntities() {
  const raw = newBanned.value.trim();
  if (!raw) return;
  const entities = raw
    .split(/[,，\s]+/g)
    .map((x) => x.trim())
    .filter(Boolean);
  if (!entities.length) return;
  try {
    const res = await addBanned({ entities, reason: 'user_manual' });
    ElMessage.success(`已加入 ${res.inserted} 项`);
    newBanned.value = '';
    await loadAll();
  } catch (err) {
    console.error(err);
    ElMessage.error('加入失败');
  }
}

// 字段中文映射 + 取值显示。
// 后端 schema 字段名(name / birth_year / gender / location 等)对最终
// 用户没意义,直接展示会让画像看起来像调试日志(参见用户截图反馈)。
const FIELD_LABELS: Record<string, string> = {
  name: '姓名',
  birth_year: '出生年',
  gender: '性别',
  location: '所在地',
  title: '职位',
  industry: '行业',
  level: '级别',
  structure: '家庭构成',
  notes: '备注',
};

const GENDER_LABELS: Record<string, string> = {
  male: '男',
  female: '女',
  other: '其它',
};

function displayValue(key: string, v: unknown): string {
  if (v === null || v === undefined || v === '') return '';
  if (key === 'gender' && typeof v === 'string') {
    return GENDER_LABELS[v] ?? v;
  }
  if (Array.isArray(v)) {
    return v.length ? v.join('、') : '';
  }
  if (typeof v === 'object') {
    const inner = Object.values(v as Record<string, unknown>)
      .filter((x) => x !== null && x !== undefined && x !== '')
      .join(' · ');
    return inner;
  }
  return String(v);
}

interface KvRow {
  label: string;
  value: string;
}

function kvRows(o: Record<string, unknown> | undefined | null): KvRow[] {
  if (!o) return [];
  const out: KvRow[] = [];
  for (const [k, v] of Object.entries(o)) {
    const value = displayValue(k, v);
    if (!value) continue; // 隐藏 null / 空数组 / 空字符串
    out.push({ label: FIELD_LABELS[k] ?? k, value });
  }
  return out;
}

function basicRows(): KvRow[] {
  return kvRows(profile.value?.basic);
}
function occupationRows(): KvRow[] {
  return kvRows(profile.value?.occupation);
}
function familyRows(): KvRow[] {
  return kvRows(profile.value?.family_structure);
}

// 关系表格的 attributes 列:仅展示有效键值,中文优先。
function relAttrText(attrs: Record<string, unknown> | undefined | null): string {
  if (!attrs) return '';
  return Object.entries(attrs)
    .map(([k, v]) => `${k}：${typeof v === 'object' ? JSON.stringify(v) : v}`)
    .filter((s) => !s.endsWith('：') && !s.endsWith('：null'))
    .join(' · ');
}

// ──────────────────────────────────────────── 审计列展示用 helpers ──
// 后端 source / action / reason 都是英文/技术串(deprecate, dedup_cli: ...)
// 直接展示用户看不懂(参见用户截图反馈),这里统一翻成中文。

const DEP_SOURCE_LABELS: Record<string, string> = {
  episodic: '情景',
  event: '事件',
  profile: '画像',
  entity: '实体',
};

const DEP_ACTION_LABELS: Record<string, string> = {
  deprecate: '废弃',
  update: '更新',
  audit_only: '仅审计',
};

function depSourceText(src: string | undefined | null): string {
  if (!src) return '';
  const cn = DEP_SOURCE_LABELS[src];
  return cn ? `${cn} (${src})` : src;
}

function depActionText(act: string | undefined | null): string {
  if (!act) return '';
  return DEP_ACTION_LABELS[act] ?? act;
}

// 把 dedup_cli 落进 reason 字段的英文模板翻成中文。后端三种模板:
//   "dedup_cli: similar to <uuid> (sim=0.969 ≥ 0.95)"        — 情景 ANN 命中
//   "dedup_cli: exact match with <uuid> ('宅家学习AI')"      — 事件标题完全相同
//   "dedup_cli: substring match with <uuid> ('宅家学习AI')"  — 事件标题相互包含
// 其它来源(将来手动纠错管道写入的 reason)原样展示。
function depReasonText(reason: string | undefined | null): string {
  if (!reason) return '';
  const r = reason.trim();

  const simMatch = r.match(/^dedup_cli:\s*similar to\s+\S+\s*\(sim=([\d.]+)\s*≥\s*([\d.]+)\)/);
  if (simMatch) {
    const simPct = (parseFloat(simMatch[1]) * 100).toFixed(1);
    const thrPct = (parseFloat(simMatch[2]) * 100).toFixed(0);
    return `批量去重：与已保留记忆语义重复（相似度 ${simPct}%，阈值 ${thrPct}%）`;
  }

  const exactMatch = r.match(/^dedup_cli:\s*exact match with\s+\S+\s*\('(.+)'\)/);
  if (exactMatch) {
    return `批量去重：与已保留事件「${exactMatch[1]}」标题完全相同`;
  }

  const subMatch = r.match(/^dedup_cli:\s*substring match with\s+\S+\s*\('(.+)'\)/);
  if (subMatch) {
    return `批量去重：与已保留事件「${subMatch[1]}」标题相互包含`;
  }

  return r;
}

// 时间统一格式化到秒,本地时区,避免暴露 ISO 串里的毫秒和 Z 后缀。
function depTimeText(s: string | undefined | null): string {
  if (!s) return '';
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd} ${hh}:${mi}:${ss}`;
}
</script>

<template>
  <div class="mem-tabs" v-loading="loading">
    <el-tabs type="card">
      <el-tab-pane label="画像">
        <div class="card mem-section" v-if="basicRows().length">
          <h3>基础信息</h3>
          <dl class="kv-list">
            <template v-for="r in basicRows()" :key="r.label">
              <dt>{{ r.label }}</dt>
              <dd>{{ r.value }}</dd>
            </template>
          </dl>
        </div>
        <div class="card mem-section" v-if="occupationRows().length">
          <h3>职业</h3>
          <dl class="kv-list">
            <template v-for="r in occupationRows()" :key="r.label">
              <dt>{{ r.label }}</dt>
              <dd>{{ r.value }}</dd>
            </template>
          </dl>
        </div>
        <div class="card mem-section" v-if="profile?.interests?.length">
          <h3>兴趣</h3>
          <div class="tag-row">
            <el-tag v-for="t in profile.interests" :key="t" type="info">{{ t }}</el-tag>
          </div>
        </div>
        <div class="card mem-section" v-if="familyRows().length">
          <h3>家庭</h3>
          <dl class="kv-list">
            <template v-for="r in familyRows()" :key="r.label">
              <dt>{{ r.label }}</dt>
              <dd>{{ r.value }}</dd>
            </template>
          </dl>
        </div>
        <div class="card mem-section" v-if="profile?.user_corrections?.length">
          <h3>纠错记录</h3>
          <el-table :data="profile.user_corrections" size="small">
            <el-table-column prop="field_path" label="字段" width="200" />
            <el-table-column prop="old_value" label="原值" />
            <el-table-column prop="new_value" label="新值" />
            <el-table-column prop="happened_at" label="时间" width="200" />
          </el-table>
        </div>
        <div v-if="!profile" class="empty-hint">
          还没有画像数据，先去聊几轮试试
        </div>
      </el-tab-pane>

      <el-tab-pane :label="`事件 (${events.length})`">
        <el-table :data="events" stripe size="small">
          <el-table-column prop="type" label="类型" width="120" />
          <el-table-column prop="title" label="标题" />
          <el-table-column prop="content" label="内容" />
          <el-table-column prop="occurred_at" label="发生时间" width="200" />
          <el-table-column prop="status" label="状态" width="100" />
        </el-table>
      </el-tab-pane>

      <el-tab-pane :label="`情景 (${episodic.length})`">
        <el-table :data="episodic" stripe size="small">
          <el-table-column prop="text" label="内容" min-width="320" show-overflow-tooltip />
          <el-table-column prop="source" label="来源" width="120" />
          <el-table-column prop="created_at" label="时间" width="200" />
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-button link type="danger" size="small" @click="dropEpisodic(row.id)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane :label="`关系 (${relationships.length})`">
        <el-table :data="relationships" stripe size="small">
          <el-table-column prop="name" label="姓名 / 称谓" width="180" show-overflow-tooltip />
          <el-table-column prop="role" label="关系" width="120" />
          <el-table-column label="附加属性" min-width="240" show-overflow-tooltip>
            <template #default="{ row }">{{ relAttrText(row.attributes) || '—' }}</template>
          </el-table-column>
          <el-table-column prop="via" label="经由" width="120" />
          <el-table-column prop="created_at" label="录入时间" width="200" />
        </el-table>
        <div class="empty-hint" v-if="!relationships.length" style="margin-top:8px">
          还没有关系数据。如果姓名看起来不对(比如英文/拼音/截短),
          检查一下你聊天里是怎么提到这个人的;LLM 抽取器只会按你说的写,
          没说全名就只能写称谓(妻子/儿子)。
        </div>
      </el-tab-pane>

      <el-tab-pane :label="`封禁 (${banned.length})`">
        <div class="card mem-section">
          <h3>新增封禁实体</h3>
          <div style="display:flex;gap:8px">
            <el-input v-model="newBanned" placeholder="多个实体用逗号或空格分隔" />
            <el-button type="primary" @click="addBannedEntities">加入</el-button>
          </div>
        </div>
        <el-table :data="banned" stripe size="small">
          <el-table-column prop="entity" label="实体" width="180" />
          <el-table-column prop="reason" label="原因" />
          <el-table-column prop="created_at" label="时间" width="200" />
        </el-table>
      </el-tab-pane>

      <el-tab-pane :label="`审计 (${deprecations.length})`">
        <el-table :data="deprecations" stripe size="small">
          <el-table-column label="来源" width="140">
            <template #default="{ row }">{{ depSourceText(row.source) }}</template>
          </el-table-column>
          <el-table-column label="原因" min-width="360" show-overflow-tooltip>
            <template #default="{ row }">{{ depReasonText(row.reason) }}</template>
          </el-table-column>
          <el-table-column label="动作" width="100">
            <template #default="{ row }">{{ depActionText(row.action) }}</template>
          </el-table-column>
          <el-table-column label="弃用时间" width="180">
            <template #default="{ row }">{{ depTimeText(row.deprecated_at) }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
