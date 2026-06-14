<script setup lang="ts">
/**
 * 社交关系图谱(径向多层布局)。
 *
 * 为什么不是纯一阶星图:
 *   `Relationship.via` 字段(后端 lesson 4.3)记录了"通过谁认识"的中间人,
 *   是二阶/多阶关系的核心。之前的星图无视 via,把"我儿子的朋友小孙孙"
 *   也直接画成"我 ←朋友→ 小孙孙",语义错。
 *
 * 现在:
 *   - via=null 的关系是「我」的一阶,排在内圈(半径 220)
 *   - via 指向某个一阶节点的关系,在该一阶节点周围扇形展开(外圈)
 *   - via 指向不存在的节点(没抽到 / via 链断了)按一阶节点处理,
 *     不让它孤儿
 *   - 中心 label 优先用 profile.basic.name(画像名)而不是 user.display_name,
 *     因为前者是用户对外的中文名,后者可能是注册时填的拼音/英文 ID
 *
 * 边的 label = role,节点的 hover tooltip = role + attributes 摘要。
 */
import { computed, onMounted, ref } from 'vue';
import { getProfile, listRelationships, type Relationship } from '@/api/memory';
import { useAuthStore } from '@/stores/auth';

const auth = useAuthStore();
const rels = ref<Relationship[]>([]);
const loading = ref(true);
const profileName = ref<string>('');

onMounted(async () => {
  try {
    // 并行拉:profile 用来选中心节点 label,relationships 用来画图
    const [r, p] = await Promise.all([
      listRelationships(),
      getProfile().catch(() => null),
    ]);
    rels.value = r;
    const basic = (p?.basic as Record<string, unknown> | undefined) ?? undefined;
    const n = basic?.name;
    profileName.value = typeof n === 'string' && n.trim() ? n.trim() : '';
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
  role?: string;
  attrs?: Record<string, unknown>;
  x: number;
  y: number;
  isMe: boolean;
  tier: 0 | 1 | 2;
}

interface Edge {
  from: string;
  to: string;
  label: string;
  tier: 1 | 2;
}

const SIZE = 700;
const TIER1_R = 200; // 「我」一阶半径
const TIER2_R = 110; // 二阶绕一阶节点的子圆半径

/**
 * 中心节点的展示名:
 *   profile.basic.name (画像中文名) > auth.displayName (登录账号) > '我'
 */
const meLabel = computed(() => {
  return profileName.value || auth.displayName || '我';
});

const layout = computed<{ nodes: Node[]; edges: Edge[] }>(() => {
  const list = rels.value;
  const me: Node = {
    id: 'me',
    label: meLabel.value,
    x: SIZE / 2,
    y: SIZE / 2,
    isMe: true,
    tier: 0,
  };

  // 索引一阶 / 二阶 — 一阶 = via 为空 OR via 指向不存在的关系(孤儿提升)
  const byId = new Map<string, Relationship>();
  for (const r of list) byId.set(r.id, r);

  const tier1: Relationship[] = [];
  const tier2ByParent = new Map<string, Relationship[]>();
  for (const r of list) {
    if (r.via && byId.has(r.via)) {
      const arr = tier2ByParent.get(r.via) ?? [];
      arr.push(r);
      tier2ByParent.set(r.via, arr);
    } else {
      tier1.push(r);
    }
  }

  const nodes: Node[] = [me];
  const edges: Edge[] = [];

  // 一阶节点等角分布在 TIER1_R 的圆周上
  const n1 = Math.max(tier1.length, 1);
  tier1.forEach((r, i) => {
    const angle = (i / n1) * 2 * Math.PI - Math.PI / 2;
    const x = SIZE / 2 + TIER1_R * Math.cos(angle);
    const y = SIZE / 2 + TIER1_R * Math.sin(angle);
    nodes.push({
      id: r.id,
      label: r.name,
      role: r.role,
      attrs: r.attributes,
      x,
      y,
      isMe: false,
      tier: 1,
    });
    edges.push({ from: 'me', to: r.id, label: r.role, tier: 1 });

    // 二阶围绕这个一阶节点扇形展开,扇心朝外(远离中心),
    // 避免叠到「我」上去
    const children = tier2ByParent.get(r.id) ?? [];
    const k = children.length;
    if (k === 0) return;
    // 扇形覆盖 ~120° = 2π/3,均匀分布,小于 4 个时角度收敛
    const span = Math.min((Math.PI * 2) / 3, (Math.PI / 3) * k);
    const startAngle = angle - span / 2;
    const stepAngle = k === 1 ? 0 : span / (k - 1);
    children.forEach((c, j) => {
      const a = k === 1 ? angle : startAngle + j * stepAngle;
      const cx = x + TIER2_R * Math.cos(a);
      const cy = y + TIER2_R * Math.sin(a);
      nodes.push({
        id: c.id,
        label: c.name,
        role: c.role,
        attrs: c.attributes,
        x: cx,
        y: cy,
        isMe: false,
        tier: 2,
      });
      edges.push({ from: r.id, to: c.id, label: c.role, tier: 2 });
    });
  });

  return { nodes, edges };
});

function nodeById(id: string): Node | undefined {
  return layout.value.nodes.find((n) => n.id === id);
}

/**
 * 节点圆圈里的文字按字符宽度截断:中文 ≈ 1 单位,英文 ≈ 0.55 单位。
 * 一阶节点容量大些(~5 个中文),二阶小一些(~4 个),超出加省略号。
 * 完整 label 通过 <title> 暴露给鼠标 tooltip / 屏幕阅读器。
 */
function shortLabel(s: string, max = 4): string {
  if (!s) return '';
  let width = 0;
  let out = '';
  for (const ch of s) {
    const w = /[\u4e00-\u9fff\u3000-\u303f]/.test(ch) ? 1 : 0.55;
    if (width + w > max) {
      return out + '…';
    }
    width += w;
    out += ch;
  }
  return out;
}

/**
 * 节点 hover tooltip:role + 关键 attributes。
 * 真正的细节走 <title>(浏览器原生 tooltip)而不是自绘 div ——
 * SVG 内的 hover layer 牵涉 z-index / overflow,简单稳。
 */
function nodeTooltip(n: Node): string {
  if (n.isMe) return n.label;
  const lines: string[] = [];
  lines.push(n.label);
  if (n.role) lines.push(`关系:${n.role}`);
  if (n.attrs && Object.keys(n.attrs).length) {
    for (const [k, v] of Object.entries(n.attrs)) {
      if (v === null || v === undefined || v === '') continue;
      lines.push(`${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`);
    }
  }
  return lines.join('\n');
}

/**
 * 不同关系类别用不同的描边/底色,让宠物、邻居、家人一眼区分。
 * 命中规则按"包含关键字"做,容忍 LLM 输出的变体(妻子/老婆/媳妇 都算 family)。
 */
function nodeStyle(n: Node): { fill: string; stroke: string } {
  if (n.isMe) return { fill: '#7c3aed', stroke: '#1e1b4b' };
  const role = (n.role || '').toLowerCase();
  // family
  if (
    /妻|丈夫|老婆|老公|爱人|儿子|女儿|父亲|母亲|爸|妈|兄|弟|姐|妹|爷|奶|公|婆|外公|外婆/.test(
      role,
    )
  ) {
    return { fill: '#fef3c7', stroke: '#f59e0b' }; // 暖黄
  }
  // pet
  if (/宠物|猫|狗|鸟|兔/.test(role)) {
    return { fill: '#fce7f3', stroke: '#ec4899' }; // 粉
  }
  // school
  if (/同学|老师|校友|导师/.test(role)) {
    return { fill: '#dbeafe', stroke: '#3b82f6' }; // 蓝
  }
  // work
  if (/同事|领导|下属|老板|客户|主管/.test(role)) {
    return { fill: '#dcfce7', stroke: '#16a34a' }; // 绿
  }
  // neighbor
  if (/邻居|房东/.test(role)) {
    return { fill: '#ede9fe', stroke: '#8b5cf6' }; // 浅紫
  }
  // friend
  if (/朋友|闺蜜|发小|网友/.test(role)) {
    return { fill: '#fff7ed', stroke: '#f97316' }; // 橙
  }
  // default
  return { fill: '#fff', stroke: '#a78bfa' };
}
</script>

<template>
  <div class="mem-tabs" v-loading="loading">
    <div class="card" style="height:calc(100vh - 64px);display:flex;flex-direction:column">
      <header style="display:flex;align-items:center;justify-content:space-between">
        <h3 style="margin:0;color:var(--xb-purple-dark)">关系图谱</h3>
        <div class="legend">
          <span class="legend-item legend-family">家人</span>
          <span class="legend-item legend-pet">宠物</span>
          <span class="legend-item legend-school">同学/老师</span>
          <span class="legend-item legend-work">同事</span>
          <span class="legend-item legend-neighbor">邻居</span>
          <span class="legend-item legend-friend">朋友</span>
          <span class="legend-tip">悬停节点查看属性</span>
        </div>
      </header>
      <div v-if="!rels.length && !loading" class="empty-hint">还没有关系数据</div>
      <svg
        v-else
        :viewBox="`0 0 ${SIZE} ${SIZE}`"
        class="graph-canvas"
        preserveAspectRatio="xMidYMid meet"
      >
        <!-- 边 -->
        <g>
          <line
            v-for="e in layout.edges"
            :key="`${e.from}-${e.to}`"
            :x1="nodeById(e.from)?.x"
            :y1="nodeById(e.from)?.y"
            :x2="nodeById(e.to)?.x"
            :y2="nodeById(e.to)?.y"
            :stroke="e.tier === 2 ? '#cbd5e1' : '#a78bfa'"
            :stroke-dasharray="e.tier === 2 ? '4 3' : '0'"
            stroke-width="1.5"
            stroke-opacity="0.85"
          />
        </g>
        <!-- 边 label -->
        <g>
          <text
            v-for="e in layout.edges"
            :key="`l-${e.from}-${e.to}`"
            :x="((nodeById(e.from)?.x ?? 0) + (nodeById(e.to)?.x ?? 0)) / 2"
            :y="((nodeById(e.from)?.y ?? 0) + (nodeById(e.to)?.y ?? 0)) / 2 - 4"
            :fill="e.tier === 2 ? '#64748b' : '#7c3aed'"
            :font-size="e.tier === 2 ? 10 : 11"
            text-anchor="middle"
          >
            {{ e.label }}
          </text>
        </g>
        <!-- 节点 -->
        <g v-for="n in layout.nodes" :key="n.id">
          <circle
            :cx="n.x"
            :cy="n.y"
            :r="n.isMe ? 32 : n.tier === 1 ? 26 : 20"
            :fill="nodeStyle(n).fill"
            :stroke="nodeStyle(n).stroke"
            stroke-width="2"
          />
          <title>{{ nodeTooltip(n) }}</title>
          <text
            :x="n.x"
            :y="n.y + (n.role && !n.isMe ? -2 : 4)"
            text-anchor="middle"
            :fill="n.isMe ? '#fff' : '#312e81'"
            :font-size="n.isMe ? 14 : n.tier === 1 ? 13 : 11"
            font-weight="600"
          >
            {{ shortLabel(n.label, n.tier === 2 ? 3 : 4) }}
          </text>
          <!-- 一阶/二阶节点在主名下方加一行小字 role,让"称谓和身份"同时可见 -->
          <text
            v-if="n.role && !n.isMe"
            :x="n.x"
            :y="n.y + 12"
            text-anchor="middle"
            fill="#64748b"
            :font-size="n.tier === 1 ? 10 : 9"
          >
            {{ shortLabel(n.role, n.tier === 2 ? 3 : 4) }}
          </text>
        </g>
      </svg>
    </div>
  </div>
</template>

<style scoped>
.graph-canvas {
  flex: 1;
  width: 100%;
  min-height: 0;
}

.legend {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--xb-muted);
  flex-wrap: wrap;
}
.legend-item {
  padding: 2px 8px;
  border-radius: 999px;
  border: 1.5px solid;
  background: #fff;
}
.legend-family {
  background: #fef3c7;
  border-color: #f59e0b;
  color: #92400e;
}
.legend-pet {
  background: #fce7f3;
  border-color: #ec4899;
  color: #9d174d;
}
.legend-school {
  background: #dbeafe;
  border-color: #3b82f6;
  color: #1e3a8a;
}
.legend-work {
  background: #dcfce7;
  border-color: #16a34a;
  color: #14532d;
}
.legend-neighbor {
  background: #ede9fe;
  border-color: #8b5cf6;
  color: #5b21b6;
}
.legend-friend {
  background: #fff7ed;
  border-color: #f97316;
  color: #9a3412;
}
.legend-tip {
  margin-left: 8px;
  font-style: italic;
}
</style>
