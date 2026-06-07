import { createApp } from 'vue';
import { createPinia } from 'pinia';
import ElementPlus from 'element-plus';
import 'element-plus/dist/index.css';
import zhCn from 'element-plus/es/locale/lang/zh-cn';

import App from './App.vue';
import { router } from './router';
import { useAuthStore } from './stores/auth';
import './styles.css';

const app = createApp(App);
app.use(createPinia());
app.use(router);
app.use(ElementPlus, { locale: zhCn });

// Best-effort token refresh before the first route guard fires.
const auth = useAuthStore();
auth.refreshMe().finally(() => {
  app.mount('#app');
});
