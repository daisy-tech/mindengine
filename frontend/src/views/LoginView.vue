<script setup lang="ts">
import { reactive, ref } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { ElMessage } from 'element-plus';
import { formatApiError } from '@/api/errors';
import { useAuthStore } from '@/stores/auth';

const auth = useAuthStore();
const router = useRouter();
const route = useRoute();

const mode = ref<'login' | 'register'>('login');
const loading = ref(false);

const form = reactive({
  email: '',
  password: '',
  display_name: '',
  personality: 'balanced' as 'introvert' | 'balanced' | 'extrovert',
});

async function submit() {
  if (!form.email || !form.password) {
    ElMessage.warning('请输入邮箱和密码');
    return;
  }
  if (mode.value === 'register' && form.password.length < 8) {
    ElMessage.warning('密码至少 8 位');
    return;
  }
  loading.value = true;
  try {
    if (mode.value === 'register') {
      await auth.registerNew({
        email: form.email,
        password: form.password,
        display_name: form.display_name || undefined,
        personality: form.personality,
      });
    } else {
      await auth.loginWithCredentials(form.email, form.password);
    }
    const next = (route.query.next as string) || '/chat';
    await router.push(next);
  } catch (e: unknown) {
    ElMessage.error(formatApiError(e));
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="login-shell">
    <div class="login-card">
      <img src="/xiaobai-avatar.png" alt="小白" class="avatar-big" />
      <h1>小白</h1>
      <p class="subtitle">AI 记忆伴侣 · MindEngine</p>

      <el-form @submit.prevent="submit" label-position="top" size="large">
        <el-form-item label="邮箱">
          <el-input v-model="form.email" autocomplete="email" />
        </el-form-item>
        <el-form-item :label="mode === 'register' ? '密码（至少 8 位）' : '密码'">
          <el-input
            v-model="form.password"
            type="password"
            :autocomplete="mode === 'register' ? 'new-password' : 'current-password'"
            show-password
          />
        </el-form-item>
        <template v-if="mode === 'register'">
          <el-form-item label="昵称（可选）">
            <el-input v-model="form.display_name" />
          </el-form-item>
          <el-form-item label="人格">
            <el-radio-group v-model="form.personality">
              <el-radio value="introvert">内向</el-radio>
              <el-radio value="balanced">中性</el-radio>
              <el-radio value="extrovert">外向</el-radio>
            </el-radio-group>
          </el-form-item>
        </template>
        <el-button
          native-type="submit"
          type="primary"
          :loading="loading"
          style="width: 100%"
        >
          {{ mode === 'login' ? '登录' : '注册并登录' }}
        </el-button>
      </el-form>

      <div class="switch">
        <a href="#" @click.prevent="mode = mode === 'login' ? 'register' : 'login'">
          {{ mode === 'login' ? '没有账号？立即注册' : '已有账号？返回登录' }}
        </a>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-shell {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1e1b4b 0%, #312e81 50%, #6d28d9 100%);
}
.login-card {
  width: 360px;
  background: #fff;
  border-radius: 16px;
  padding: 32px 28px;
  box-shadow: 0 20px 60px rgba(15, 23, 42, 0.25);
  text-align: center;
}
.avatar-big {
  width: 88px;
  height: 88px;
  border-radius: 50%;
  box-shadow: 0 6px 14px rgba(124, 58, 237, 0.3);
}
.login-card h1 {
  margin: 8px 0 2px;
  color: #1f2937;
  letter-spacing: 2px;
}
.subtitle {
  margin: 0 0 18px;
  color: var(--xb-muted);
  font-size: 13px;
}
.switch {
  margin-top: 14px;
  font-size: 13px;
}
</style>
