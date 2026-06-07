// Single axios instance for the JSON parts of the API. SSE chat lives
// in `sseChat.ts` because axios buffers responses and is unsuitable for
// streaming.

import axios, { type AxiosError } from 'axios';
import { useAuthStore } from '@/stores/auth';

const baseURL = import.meta.env.VITE_API_BASE ?? '';

export const http = axios.create({
  baseURL,
  timeout: 30_000,
});

http.interceptors.request.use((config) => {
  const auth = useAuthStore();
  if (auth.token) {
    config.headers.set('Authorization', `Bearer ${auth.token}`);
  }
  return config;
});

http.interceptors.response.use(
  (resp) => resp,
  (err: AxiosError) => {
    // Auto-logout on 401 so the router can redirect to /login.
    if (err.response?.status === 401) {
      const auth = useAuthStore();
      auth.clear();
    }
    return Promise.reject(err);
  },
);

export function apiBase(): string {
  return baseURL;
}
