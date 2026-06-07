<script setup lang="ts">
import { computed } from 'vue';
import { useRouter, RouterView } from 'vue-router';
import { useAuthStore } from '@/stores/auth';

const auth = useAuthStore();
const router = useRouter();

const showShell = computed(() => auth.isAuthed);

async function logout() {
  auth.clear();
  await router.push({ name: 'login' });
}
</script>

<template>
  <div v-if="showShell" class="app-shell">
    <aside class="sidebar">
      <div class="sidebar-brand">
        <img src="/xiaobai-avatar.png" alt="小白" />
        <h2>小白</h2>
        <p>AI 记忆伴侣</p>
      </div>
      <nav>
        <ul class="nav-list">
          <li><router-link to="/chat">对话</router-link></li>
          <li><router-link to="/memory">记忆</router-link></li>
          <li><router-link to="/social">关系图谱</router-link></li>
          <li><router-link to="/eval">评测</router-link></li>
        </ul>
      </nav>
      <div class="sidebar-footer">
        <div>{{ auth.displayName || auth.email || auth.userId }}</div>
        <div>人格 · {{ auth.personality }}</div>
        <div class="logout" @click="logout">退出登录</div>
      </div>
    </aside>
    <main class="main">
      <RouterView />
    </main>
  </div>
  <RouterView v-else />
</template>
