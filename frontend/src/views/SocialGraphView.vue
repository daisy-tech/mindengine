<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { listRelationships, type Relationship } from '@/api/memory';
import { useAuthStore } from '@/stores/auth';

const auth = useAuthStore();
const rels = ref<Relationship[]>([]);
const loading = ref(true);

onMounted(async () => {
  try {
    rels.value = await listRelationships();
  } catch (e) {
    console.error(e);
    ElMessage.error('加载关系失败');
  } finally {
    loading.value = false;
  }
});

interface Node {
  id: string;
  label: string;
  x: number;
  y: number;
  isMe: boolean;
}

interface Edge {
  from: string;
  to: string;
  label: string;
}

const SIZE = 600;
const RING_R = 220;

const layout = computed<{ nodes: Node[]; edges: Edge[] }>(() => {
  const me: Node = {
    id: 'me',
    label: auth.displayName || '我',
    x: SIZE / 2,
    y: SIZE / 2,
    isMe: true,
  };
  const list = rels.value;
  const n = Math.max(list.length, 1);
  const nodes: Node[] = [me];
  const edges: Edge[] = [];
  list.forEach((r, i) => {
    const angle = (i / n) * 2 * Math.PI - Math.PI / 2;
    const x = SIZE / 2 + RING_R * Math.cos(angle);
    const y = SIZE / 2 + RING_R * Math.sin(angle);
    nodes.push({ id: r.id, label: r.name, x, y, isMe: false });
    edges.push({ from: 'me', to: r.id, label: r.role });
  });
  return { nodes, edges };
});

function nodeById(id: string): Node | undefined {
  return layout.value.nodes.find((n) => n.id === id);
}
</script>

<template>
  <div class="mem-tabs" v-loading="loading">
    <div class="card" style="height:calc(100vh - 64px);display:flex;flex-direction:column">
      <header style="display:flex;align-items:center;justify-content:space-between">
        <h3 style="margin:0;color:var(--xb-purple-dark)">关系图谱</h3>
        <span style="color:var(--xb-muted);font-size:12px">
          基于 /api/memory/relationships
        </span>
      </header>
      <div v-if="!rels.length && !loading" class="empty-hint">还没有关系数据</div>
      <svg
        v-else
        :viewBox="`0 0 ${SIZE} ${SIZE}`"
        class="graph-canvas"
        preserveAspectRatio="xMidYMid meet"
      >
        <line
          v-for="e in layout.edges"
          :key="`${e.from}-${e.to}`"
          :x1="nodeById(e.from)?.x"
          :y1="nodeById(e.from)?.y"
          :x2="nodeById(e.to)?.x"
          :y2="nodeById(e.to)?.y"
          stroke="#a78bfa"
          stroke-width="1.5"
          stroke-opacity="0.7"
        />
        <text
          v-for="e in layout.edges"
          :key="`l-${e.from}-${e.to}`"
          :x="((nodeById(e.from)?.x ?? 0) + (nodeById(e.to)?.x ?? 0)) / 2"
          :y="((nodeById(e.from)?.y ?? 0) + (nodeById(e.to)?.y ?? 0)) / 2 - 4"
          fill="#7c3aed"
          font-size="11"
          text-anchor="middle"
        >
          {{ e.label }}
        </text>
        <g v-for="n in layout.nodes" :key="n.id">
          <circle
            :cx="n.x"
            :cy="n.y"
            :r="n.isMe ? 30 : 24"
            :fill="n.isMe ? '#7c3aed' : '#fff'"
            :stroke="n.isMe ? '#1e1b4b' : '#a78bfa'"
            stroke-width="2"
          />
          <text
            :x="n.x"
            :y="n.y + 4"
            text-anchor="middle"
            :fill="n.isMe ? '#fff' : '#312e81'"
            font-size="13"
            font-weight="600"
          >
            {{ n.label.slice(0, 4) }}
          </text>
        </g>
      </svg>
    </div>
  </div>
</template>
