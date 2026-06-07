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
