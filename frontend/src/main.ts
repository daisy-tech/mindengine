import { createApp } from 'vue';
import { createPinia } from 'pinia';
// Element Plus components/APIs are auto-imported on demand (see vite.config.ts).
// Only the reset stylesheet is needed globally; per-component CSS is injected
// by the resolver. zh-cn locale is applied via <el-config-provider> in App.vue.
import 'element-plus/theme-chalk/base.css';

import App from './App.vue';
import { router } from './router';
import { useAuthStore } from './stores/auth';
import './styles.css';

const app = createApp(App);
app.use(createPinia());
app.use(router);

// Best-effort token refresh before the first route guard fires.
const auth = useAuthStore();
auth.refreshMe().finally(() => {
  app.mount('#app');
});
