<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRouter, RouterView } from 'vue-router';
import zhCn from 'element-plus/es/locale/lang/zh-cn';
import { useAuthStore } from '@/stores/auth';
import ChangePasswordDialog from '@/components/ChangePasswordDialog.vue';

const auth = useAuthStore();
const router = useRouter();

const showShell = computed(() => auth.isAuthed);
const showChangePwd = ref(false);

async function logout() {
  auth.clear();
  await router.push({ name: 'login' });
}
</script>

<template>
  <el-config-provider :locale="zhCn">
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
        <div class="footer-link" @click="showChangePwd = true">修改密码</div>
        <div class="footer-link" @click="logout">退出登录</div>
      </div>
    </aside>
    <main class="main">
      <RouterView />
    </main>
    <ChangePasswordDialog v-model="showChangePwd" />
  </div>
  <RouterView v-else />
  </el-config-provider>
</template>
