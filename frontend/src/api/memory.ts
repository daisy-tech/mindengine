import { http } from './client';

export interface Profile {
  user_id?: string;
  basic?: Record<string, unknown>;
  occupation?: Record<string, unknown>;
  interests?: string[];
  family_structure?: Record<string, unknown>;
  extra?: Record<string, unknown>;
  user_corrections?: Array<{
    field_path: string;
    old_value: unknown;
    new_value: unknown;
    happened_at?: string;
  }>;
  schema_version?: number;
  updated_at?: string;
}

export interface Event {
  id: string;
  type: string;
  title: string;
  content: string;
  occurred_at?: string | null;
  status?: string;
}

export interface EpisodicMemory {
  id: string;
  // Backend field is `text` (see app/domain/memory.py::EpisodicMemory).
  // We previously declared `title` / `content` here, which silently
  // returned undefined and rendered as blank rows in the UI.
  text: string;
  source_message_id?: string | null;
  source?: string | null;
  status?: string;
  created_at?: string;
}

export interface Relationship {
  id: string;
  name: string;
  role: string;
  attributes?: Record<string, unknown>;
  via?: string | null;
}

export interface BannedEntityDTO {
  entity: string;
  reason: string;
  created_at: string;
}

export interface DeprecationDTO {
  id: number | null;
  source: string;
  ref_id: string;
  reason: string;
  action: string;
  deprecated_at: string | null;
  restored_at: string | null;
}

export async function getProfile(): Promise<Profile | null> {
  const { data } = await http.get<Profile | null>('/api/memory/profile');
  return data;
}

export async function patchProfile(partial: Partial<Profile>): Promise<Profile> {
  const { data } = await http.patch<Profile>('/api/memory/profile', partial);
  return data;
}

export async function listEvents(params?: {
  type?: string;
  limit?: number;
}): Promise<Event[]> {
  const { data } = await http.get<Event[]>('/api/memory/events', { params });
  return data;
}

export async function listEpisodic(limit = 50): Promise<EpisodicMemory[]> {
  const { data } = await http.get<EpisodicMemory[]>('/api/memory/episodic', {
    params: { limit },
  });
  return data;
}

export async function deleteEpisodic(memId: string, reason = 'user_deleted'): Promise<void> {
  await http.delete(`/api/memory/episodic/${encodeURIComponent(memId)}`, {
    params: { reason },
  });
}

export async function listRelationships(): Promise<Relationship[]> {
  const { data } = await http.get<Relationship[]>('/api/memory/relationships');
  return data;
}

export async function listBanned(): Promise<BannedEntityDTO[]> {
  const { data } = await http.get<BannedEntityDTO[]>('/api/memory/banned-entities');
  return data;
}

export async function addBanned(payload: {
  entities: string[];
  reason?: string;
}): Promise<{ inserted: number }> {
  const { data } = await http.post<{ inserted: number }>(
    '/api/memory/banned-entities',
    payload,
  );
  return data;
}

export async function listDeprecations(limit = 50): Promise<DeprecationDTO[]> {
  const { data } = await http.get<DeprecationDTO[]>('/api/memory/deprecations', {
    params: { limit },
  });
  return data;
}
