import { defineStore } from 'pinia';
import {
  createConversation,
  deleteConversation,
  listConversations,
  listMessages,
  type ConversationDTO,
  type MessageDTO,
} from '@/api/conversations';
import { streamChat } from '@/api/sseChat';
import { useAuthStore } from './auth';

export interface ChatTurn extends MessageDTO {
  // optimistic-streaming flags
  pending?: boolean;
  error?: string | null;
}

export const useChatStore = defineStore('chat', {
  state: () => ({
    conversations: [] as ConversationDTO[],
    activeId: '' as string,
    turns: [] as ChatTurn[],
    sending: false,
    error: '' as string,
  }),
  getters: {
    activeConversation(state): ConversationDTO | undefined {
      return state.conversations.find((c) => c.id === state.activeId);
    },
  },
  actions: {
    async refreshList() {
      this.conversations = await listConversations();
      if (!this.activeId && this.conversations.length) {
        await this.select(this.conversations[0].id);
      }
    },
    async newConversation(title?: string) {
      const conv = await createConversation({ title });
      this.conversations.unshift(conv);
      await this.select(conv.id);
      return conv;
    },
    async select(id: string) {
      this.activeId = id;
      this.error = '';
      this.turns = (await listMessages(id, 200)) as ChatTurn[];
    },
    async dropConversation(id: string) {
      await deleteConversation(id);
      this.conversations = this.conversations.filter((c) => c.id !== id);
      if (this.activeId === id) {
        this.activeId = this.conversations[0]?.id ?? '';
        this.turns = [];
        if (this.activeId) await this.select(this.activeId);
      }
    },
    async send(text: string) {
      if (!this.activeId || this.sending) return;
      const auth = useAuthStore();
      const userTurn: ChatTurn = {
        id: `local-u-${Date.now()}`,
        role: 'user',
        content: text,
        created_at: new Date().toISOString(),
        meta: null,
      };
      const assistantTurn: ChatTurn = {
        id: `local-a-${Date.now()}`,
        role: 'assistant',
        content: '',
        created_at: new Date().toISOString(),
        meta: null,
        pending: true,
      };
      this.turns.push(userTurn, assistantTurn);
      this.sending = true;
      this.error = '';
      try {
        let metaPayload: Record<string, unknown> | null = null;
        for await (const evt of streamChat({
          conversation_id: this.activeId,
          message: text,
          personality: auth.personality,
        })) {
          if (evt.kind === 'meta') {
            try {
              metaPayload = JSON.parse(evt.data);
              if (metaPayload && typeof metaPayload.message_id === 'string') {
                assistantTurn.id = metaPayload.message_id;
              }
              assistantTurn.meta = metaPayload;
            } catch {
              // ignore meta parse failure; we'll re-fetch from server
            }
          } else if (evt.kind === 'delta') {
            assistantTurn.content += evt.data;
          } else if (evt.kind === 'final') {
            assistantTurn.content = evt.data;
          } else if (evt.kind === 'error') {
            assistantTurn.error = evt.data;
            this.error = evt.data;
          }
        }
        assistantTurn.pending = false;
        // Backend has the authoritative meta_json post-persist; refresh
        // so PromptDrawer renders the full PromptMeta.
        await this.reloadCurrent();
      } catch (e) {
        assistantTurn.pending = false;
        const msg = e instanceof Error ? e.message : String(e);
        assistantTurn.error = msg;
        this.error = msg;
      } finally {
        this.sending = false;
      }
    },
    async reloadCurrent() {
      if (!this.activeId) return;
      this.turns = (await listMessages(this.activeId, 200)) as ChatTurn[];
    },
  },
});
