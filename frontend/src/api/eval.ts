import { http } from './client';

export interface CaseFileInfo {
  name: string;
  path: string;
  case_count: number;
}

export interface SyntheticReport {
  schema: string;
  run_id: string;
  run_type: string;
  started_at: string;
  finished_at: string;
  total: number;
  passed: number;
  pass_rate: number;
  intent_confusion_matrix: Record<string, Record<string, number>>;
  cases: Array<Record<string, unknown>>;
}

export interface StoredReviewSummary {
  conversation_id: string;
  evaluated_at: string;
  turns_total: number;
  evaluable_turns: number;
  final_ok_rate: number;
  counters: Record<string, number>;
}

export interface ChatAuditReview {
  schema: string;
  evaluated_at: string;
  review: {
    evaluable_turns: number;
    final_ok_rate: number;
    structure_pass_rate: number;
    counters: Record<string, number>;
    rule_stats: Record<string, Record<string, number>>;
    root_cause_top: Array<{ code: string; count: number }>;
  };
  turns: Array<Record<string, unknown>>;
}

export async function listSynthetic(): Promise<CaseFileInfo[]> {
  const { data } = await http.get<CaseFileInfo[]>('/api/eval/synthetic');
  return data;
}

export async function startSynthetic(name: string): Promise<SyntheticReport> {
  const { data } = await http.post<SyntheticReport>(
    `/api/eval/synthetic/${encodeURIComponent(name)}/start`,
  );
  return data;
}

export async function listChatAuditStored(): Promise<StoredReviewSummary[]> {
  const { data } = await http.get<{ items: StoredReviewSummary[] }>(
    '/api/eval/chat-audit-stored',
  );
  return data.items;
}

export async function getChatAudit(
  conversationId: string,
  force = false,
): Promise<ChatAuditReview> {
  const { data } = await http.get<ChatAuditReview>(
    `/api/eval/chat-audit/${encodeURIComponent(conversationId)}`,
    { params: { force } },
  );
  return data;
}

export async function deleteChatAudit(conversationId: string): Promise<void> {
  await http.delete(
    `/api/eval/chat-audit/${encodeURIComponent(conversationId)}`,
  );
}

export async function seedPersona(): Promise<Record<string, unknown>> {
  const { data } = await http.post<Record<string, unknown>>(
    '/api/eval/seed-persona',
  );
  return data;
}
