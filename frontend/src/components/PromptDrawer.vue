<script setup lang="ts">
import { computed } from 'vue';

const props = defineProps<{
  modelValue: boolean;
  meta: Record<string, unknown> | null;
}>();
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void;
}>();

const show = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
});

function pretty(v: unknown): string {
  if (v == null) return '—';
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

const sections = computed(() => {
  const m = props.meta ?? {};
  return [
    { key: 'route', title: '路由 (route)', value: m.route },
    { key: 'activated', title: '激活记忆 (activated)', value: m.activated },
    {
      key: 'snapshot_stats',
      title: '记忆池快照 (snapshot_stats)',
      value: m.snapshot_stats,
    },
    {
      key: 'section_keys',
      title: 'Prompt 段 (section_keys)',
      value: m.section_keys,
    },
    {
      key: 'system_excerpt',
      title: 'System Prompt 摘要',
      value: m.system_excerpt,
    },
    {
      key: 'contract_enforced',
      title: '人格契约后置 (contract_enforced)',
      value: m.contract_enforced,
    },
  ];
});
</script>

<template>
  <el-drawer v-model="show" title="本轮 prompt_meta" size="520">
    <div v-if="!meta" class="empty-hint">这条消息没有 prompt_meta（系统消息或客户端未持久化前的占位）。</div>
    <div v-else>
      <div v-for="s in sections" :key="s.key" class="meta-block">
        <h4>{{ s.title }}</h4>
        <pre>{{ pretty(s.value) }}</pre>
      </div>
      <div class="meta-block">
        <h4>原始 meta_json</h4>
        <pre>{{ pretty(meta) }}</pre>
      </div>
    </div>
  </el-drawer>
</template>
