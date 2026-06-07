import { http } from './client';

export interface TokenResponse {
  user_id: string;
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface MeResponse {
  user_id: string;
  email: string | null;
  display_name: string | null;
  personality: 'introvert' | 'balanced' | 'extrovert';
}

export async function register(payload: {
  email: string;
  password: string;
  display_name?: string;
  personality?: 'introvert' | 'balanced' | 'extrovert';
}): Promise<TokenResponse> {
  const { data } = await http.post<TokenResponse>('/auth/register', payload);
  return data;
}

export async function login(payload: {
  email: string;
  password: string;
}): Promise<TokenResponse> {
  const { data } = await http.post<TokenResponse>('/auth/login', payload);
  return data;
}

export async function fetchMe(): Promise<MeResponse> {
  const { data } = await http.get<MeResponse>('/auth/me');
  return data;
}
