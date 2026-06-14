import { http } from './client';

export interface ConversationDTO {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface MessageDTO {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  created_at: string;
  meta: Record<string, unknown> | null;
}

export interface AuditMessageDTO extends MessageDTO {
  error: string | null;
}

export interface AuditExportDTO {
  conversation_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  archived: boolean;
  messages: AuditMessageDTO[];
}

export async function listConversations(): Promise<ConversationDTO[]> {
  const { data } = await http.get<ConversationDTO[]>('/api/conversations');
  return data;
}

export async function createConversation(payload?: {
  id?: string;
  title?: string;
}): Promise<ConversationDTO> {
  const { data } = await http.post<ConversationDTO>(
    '/api/conversations',
    payload ?? {},
  );
  return data;
}

export async function listMessages(
  conversationId: string,
  limit = 50,
): Promise<MessageDTO[]> {
  const { data } = await http.get<MessageDTO[]>(
    `/api/conversations/${encodeURIComponent(conversationId)}/messages`,
    { params: { limit } },
  );
  return data;
}

export async function deleteConversation(conversationId: string): Promise<void> {
  await http.delete(
    `/api/conversations/${encodeURIComponent(conversationId)}`,
  );
}

export async function exportAudit(
  conversationId: string,
  limit = 500,
): Promise<AuditExportDTO> {
  const { data } = await http.get<AuditExportDTO>(
    `/api/conversations/${encodeURIComponent(conversationId)}/audit`,
    { params: { limit } },
  );
  return data;
}

/**
 * 完整 prompt 归档(供 prompt 评估 UI 使用)。
 *
 * 与 ``MessageDTO.meta`` 的关系:
 * - ``meta`` 来自 ``messages.meta_json``,只有 ``system_excerpt`` (≤500 字)。
 * - 这里返回的是后端 ``prompt_archive_dir`` 旁路落盘的完整文本,
 *   字段命名跟后端 ``PromptArchiveDTO`` 一一对应。
 * - 后端没配 ``prompt_archive_dir`` 时返回 503;老消息(归档前发的)返回 404。
 *   调用方应该把这两种情况都当「不可用」展示,而不是错误中断。
 */
export interface PromptArchiveDTO {
  schema: string;
  user_id: string;
  conversation_id: string;
  user_message_id: string;
  assistant_message_id: string;
  composed_at: string;
  system: string;
  user_message: string;
  llm_messages: { role: string; content: string }[];
  assistant_reply: string;
  meta: Record<string, unknown>;
}

export async function getMessagePrompt(
  conversationId: string,
  messageId: string,
): Promise<PromptArchiveDTO> {
  const { data } = await http.get<PromptArchiveDTO>(
    `/api/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/prompt`,
  );
  return data;
}
