<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
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

function fmtKv(o: Record<string, unknown> | undefined | null): string {
  if (!o) return '—';
  return Object.entries(o)
    .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`)
    .join('  ·  ');
}
</script>

<template>
  <div class="mem-tabs" v-loading="loading">
    <el-tabs type="card">
      <el-tab-pane label="画像 (Profile)">
        <div class="card mem-section" v-if="profile">
          <h3>基础信息</h3>
          <div>{{ fmtKv(profile.basic) }}</div>
        </div>
        <div class="card mem-section" v-if="profile?.occupation">
          <h3>职业</h3>
          <div>{{ fmtKv(profile.occupation) }}</div>
        </div>
        <div class="card mem-section" v-if="profile?.interests?.length">
          <h3>兴趣</h3>
          <div class="tag-row">
            <el-tag v-for="t in profile.interests" :key="t" type="info">{{ t }}</el-tag>
          </div>
        </div>
        <div class="card mem-section" v-if="profile?.family_structure">
          <h3>家庭结构</h3>
          <div>{{ fmtKv(profile.family_structure) }}</div>
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
          <el-table-column prop="title" label="标题" width="180" />
          <el-table-column prop="content" label="内容" />
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
          <el-table-column prop="name" label="姓名" />
          <el-table-column prop="role" label="关系" />
          <el-table-column label="属性">
            <template #default="{ row }">{{ fmtKv(row.attributes) }}</template>
          </el-table-column>
        </el-table>
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
          <el-table-column prop="source" label="来源" width="120" />
          <el-table-column prop="ref_id" label="ref_id" width="220" />
          <el-table-column prop="reason" label="原因" />
          <el-table-column prop="action" label="动作" width="120" />
          <el-table-column prop="deprecated_at" label="弃用时间" width="200" />
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
