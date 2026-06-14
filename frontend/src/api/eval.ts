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

/**
 * 跑一组合成评测。后端是同步阻塞实现:每条 case 串行调一次 LLM,
 * 20 条 smoke ≈ 1~2 分钟,55 条 full ≈ 3~6 分钟。所以这里 timeout
 * 单独提到 10 分钟(默认 axios 30s 会在跑完前先 abort)。后续若改成
 * 「返回 run_id + 异步轮询」,这里可以恢复短超时。
 *
 * 后端跑完会把 report 落盘到 ``eval/exports/synthetic/{user}/{run_id}.json``,
 * 之后可通过 ``listStoredSynthetic`` / ``getStoredSynthetic`` 查看,
 * 不需要再跑一次。
 */
export async function startSynthetic(name: string): Promise<SyntheticReport> {
  const { data } = await http.post<SyntheticReport>(
    `/api/eval/synthetic/${encodeURIComponent(name)}/start`,
    null,
    { timeout: 10 * 60 * 1000 },
  );
  return data;
}

export interface StoredSyntheticSummary {
  run_id: string;
  run_type: string;
  finished_at: string;
  total: number;
  passed: number;
  pass_rate: number;
}

export async function listStoredSynthetic(): Promise<StoredSyntheticSummary[]> {
  const { data } = await http.get<{ items: StoredSyntheticSummary[] }>(
    '/api/eval/synthetic/runs',
  );
  return data.items;
}

export async function getStoredSynthetic(
  runId: string,
): Promise<SyntheticReport> {
  const { data } = await http.get<SyntheticReport>(
    `/api/eval/synthetic/runs/${encodeURIComponent(runId)}`,
  );
  return data;
}

export async function deleteStoredSynthetic(runId: string): Promise<void> {
  await http.delete(`/api/eval/synthetic/runs/${encodeURIComponent(runId)}`);
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
