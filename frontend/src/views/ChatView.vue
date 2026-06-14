<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useChatStore, type ChatTurn } from '@/stores/chat';
import { useAuthStore } from '@/stores/auth';
import ChatMessage from '@/components/ChatMessage.vue';
import PromptDrawer from '@/components/PromptDrawer.vue';

const chat = useChatStore();
const auth = useAuthStore();

const draft = ref('');
const streamEl = ref<HTMLDivElement | null>(null);
const drawerOpen = ref(false);
const drawerMeta = ref<Record<string, unknown> | null>(null);
// 这两个跟着「点哪条 assistant 消息」一起设,PromptDrawer 用它们
// 拉「完整 prompt 归档」端点。turn.id 在流式期间是 local-a-xxx,
// reloadCurrent() 之后会被替换成真正的 message_id。
const drawerConvId = ref<string>('');
const drawerMsgId = ref<string>('');

onMounted(async () => {
  try {
    await chat.refreshList();
    if (!chat.activeId && chat.conversations.length === 0) {
      // 不传 title — 让后端在首句到达后用 set_title_if_empty 把 user
      // 第一句话写进 title。如果这里传 '新对话',后端会把它当成
      // 用户显式起的名字,首句永远不会覆盖,侧栏就一直叫「新对话」。
      await chat.newConversation();
    }
  } catch (e: unknown) {
    ElMessage.error('加载会话失败');
    console.error(e);
  }
});

watch(
  () => chat.turns.length,
  () => nextTick(scrollToBottom),
);

watch(
  () => chat.turns[chat.turns.length - 1]?.content,
  () => nextTick(scrollToBottom),
);

function scrollToBottom() {
  if (streamEl.value) {
    streamEl.value.scrollTop = streamEl.value.scrollHeight;
  }
}

async function send() {
  const text = draft.value.trim();
  if (!text || chat.sending) return;
  draft.value = '';
  await chat.send(text);
}

function inspect(turn: ChatTurn) {
  drawerMeta.value = turn.meta;
  drawerConvId.value = chat.activeId;
  // 流式期间 turn.id 是临时 local-a-xxx;归档接口要真正的 server message_id。
  // server message_id 在流的 meta 事件里下发,store 已把它写到 turn.id;
  // 但保险起见也尝试从 meta.message_id 读。
  const fromMeta =
    turn.meta && typeof turn.meta.message_id === 'string'
      ? (turn.meta.message_id as string)
      : '';
  drawerMsgId.value = fromMeta || turn.id || '';
  drawerOpen.value = true;
}

async function newConv() {
  try {
    // 同 onMounted:不传 title,首句到达后由后端自动回填。
    await chat.newConversation();
  } catch (e: unknown) {
    ElMessage.error('新建会话失败');
    console.error(e);
  }
}

async function dropConv(id: string) {
  await ElMessageBox.confirm('确定归档这个会话吗？消息记录会保留在审计中。', '确认', {
    confirmButtonText: '归档',
    cancelButtonText: '取消',
    type: 'warning',
  })
    .then(async () => {
      await chat.dropConversation(id);
      ElMessage.success('已归档');
    })
    .catch(() => undefined);
}

const personalityLabel = computed(() => {
  return { introvert: '内向', balanced: '中性', extrovert: '外向' }[
    auth.personality
  ];
});

function setPersonality(p: 'introvert' | 'balanced' | 'extrovert') {
  auth.setPersonality(p);
}

function onEnter(e: KeyboardEvent) {
  if (e.shiftKey) return;
  e.preventDefault();
  send();
}

function fmtTime(s: string): string {
  return new Date(s).toLocaleString();
}
</script>

<template>
  <div class="chat-shell">
    <aside class="chat-sidebar">
      <header>
        <strong>会话</strong>
        <el-button size="small" type="primary" @click="newConv">新建</el-button>
      </header>
      <div v-if="!chat.conversations.length" class="empty-hint" style="padding:16px 0">
        还没有会话，点击「新建」开始
      </div>
      <div
        v-for="c in chat.conversations"
        :key="c.id"
        :class="['conv-item', c.id === chat.activeId ? 'active' : '']"
        @click="chat.select(c.id)"
      >
        <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
          {{ c.title || '新对话' }}
        </div>
        <el-button
          link
          type="danger"
          size="small"
          @click.stop="dropConv(c.id)"
          >×</el-button
        >
      </div>
    </aside>

    <section class="chat-main">
      <div class="chat-toolbar">
        <div>
          <strong>
            {{
              chat.activeConversation
                ? chat.activeConversation.title || '新对话'
                : '请选择会话'
            }}
          </strong>
          <span v-if="chat.activeConversation" style="color:var(--xb-muted);margin-left:8px">
            {{ fmtTime(chat.activeConversation.updated_at) }}
          </span>
        </div>
        <div>
          <span style="margin-right:8px;color:var(--xb-muted)">人格 · {{ personalityLabel }}</span>
          <el-radio-group :model-value="auth.personality" @change="setPersonality" size="small">
            <el-radio-button value="introvert">内向</el-radio-button>
            <el-radio-button value="balanced">中性</el-radio-button>
            <el-radio-button value="extrovert">外向</el-radio-button>
          </el-radio-group>
        </div>
      </div>

      <div ref="streamEl" class="chat-stream">
        <div v-if="!chat.activeId" class="empty-avatar">
          <img src="/xiaobai-avatar.png" alt="小白" />
          <div>新建一个会话开始与小白对话吧</div>
        </div>
        <div v-else-if="!chat.turns.length" class="empty-avatar">
          <img src="/xiaobai-avatar.png" alt="小白" />
          <div>说点什么吧，小白会记得你</div>
        </div>
        <ChatMessage
          v-for="turn in chat.turns"
          :key="turn.id"
          :turn="turn"
          @inspect="inspect"
        />
      </div>

      <div class="chat-input">
        <textarea
          v-model="draft"
          rows="3"
          placeholder="按 Enter 发送，Shift + Enter 换行"
          :disabled="!chat.activeId || chat.sending"
          @keydown.enter="onEnter"
        />
        <el-button
          type="primary"
          :loading="chat.sending"
          :disabled="!draft.trim() || !chat.activeId"
          @click="send"
          style="height:auto"
        >
          发送
        </el-button>
      </div>
    </section>

    <PromptDrawer
      v-model="drawerOpen"
      :meta="drawerMeta"
      :conversation-id="drawerConvId"
      :message-id="drawerMsgId"
    />
  </div>
</template>
