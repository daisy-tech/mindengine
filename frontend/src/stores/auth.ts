import { defineStore } from 'pinia';
import { fetchMe, login, register, type MeResponse } from '@/api/auth';

const STORAGE_KEY = 'mindengine.auth';

interface Persisted {
  user_id: string;
  token: string;
  email?: string;
  display_name?: string;
  personality?: 'introvert' | 'balanced' | 'extrovert';
}

export const useAuthStore = defineStore('auth', {
  state: () => {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed: Partial<Persisted> = raw ? safeParse(raw) : {};
    return {
      token: parsed.token ?? '',
      userId: parsed.user_id ?? '',
      email: parsed.email ?? '',
      displayName: parsed.display_name ?? '',
      personality: (parsed.personality ?? 'balanced') as
        | 'introvert'
        | 'balanced'
        | 'extrovert',
    };
  },
  getters: {
    isAuthed(state): boolean {
      return Boolean(state.token);
    },
  },
  actions: {
    persist() {
      const payload: Persisted = {
        user_id: this.userId,
        token: this.token,
        email: this.email,
        display_name: this.displayName,
        personality: this.personality,
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    },
    clear() {
      this.token = '';
      this.userId = '';
      this.email = '';
      this.displayName = '';
      localStorage.removeItem(STORAGE_KEY);
    },
    apply(me: MeResponse, token?: string) {
      if (token) this.token = token;
      this.userId = me.user_id;
      this.email = me.email ?? '';
      this.displayName = me.display_name ?? '';
      this.personality = me.personality;
      this.persist();
    },
    async loginWithCredentials(email: string, password: string) {
      const tok = await login({ email, password });
      this.token = tok.access_token;
      this.userId = tok.user_id;
      const me = await fetchMe();
      this.apply(me);
    },
    async registerNew(payload: {
      email: string;
      password: string;
      display_name?: string;
      personality?: 'introvert' | 'balanced' | 'extrovert';
    }) {
      const tok = await register(payload);
      this.token = tok.access_token;
      this.userId = tok.user_id;
      const me = await fetchMe();
      this.apply(me);
    },
    async refreshMe() {
      if (!this.token) return;
      try {
        const me = await fetchMe();
        this.apply(me);
      } catch {
        this.clear();
      }
    },
    setPersonality(p: 'introvert' | 'balanced' | 'extrovert') {
      this.personality = p;
      this.persist();
    },
  },
});

function safeParse(raw: string): Partial<Persisted> {
  try {
    return JSON.parse(raw) as Partial<Persisted>;
  } catch {
    return {};
  }
}
