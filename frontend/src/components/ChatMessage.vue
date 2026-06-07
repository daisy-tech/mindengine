<script setup lang="ts">
import { computed } from 'vue';
import type { ChatTurn } from '@/stores/chat';
import { useAuthStore } from '@/stores/auth';

const props = defineProps<{
  turn: ChatTurn;
}>();
const emit = defineEmits<{
  (e: 'inspect', turn: ChatTurn): void;
}>();

const auth = useAuthStore();

const isUser = computed(() => props.turn.role === 'user');

const initials = computed(() => {
  const src = auth.displayName || auth.email || auth.userId || '我';
  return src.slice(0, 1).toUpperCase();
});

const intent = computed(() => {
  const meta = props.turn.meta ?? null;
  if (!meta) return '';
  const route = (meta as Record<string, unknown>).route as
    | Record<string, unknown>
    | undefined;
  return route ? String(route.intent ?? '') : '';
});
</script>

<template>
  <div :class="['bubble-row', isUser ? 'user' : 'assistant']">
    <template v-if="isUser">
      <div class="avatar user-avatar">{{ initials }}</div>
    </template>
    <template v-else>
      <img class="avatar" src="/xiaobai-avatar.png" alt="小白" />
    </template>
    <div :class="['bubble', isUser ? 'user' : 'assistant', turn.error ? 'error' : '']">
      <span v-if="turn.pending && !turn.content" class="pending">小白正在思考…</span>
      <span v-else>{{ turn.content || (turn.error ? `错误：${turn.error}` : '') }}</span>
      <div v-if="!isUser && turn.meta" class="meta-link" @click="emit('inspect', turn)">
        查看本轮 prompt_meta
        <span v-if="intent">· {{ intent }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pending {
  color: var(--xb-muted);
  font-style: italic;
}
</style>
