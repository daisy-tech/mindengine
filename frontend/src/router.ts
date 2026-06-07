import {
  createRouter,
  createWebHistory,
  type RouteRecordRaw,
} from 'vue-router';
import { useAuthStore } from './stores/auth';

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('./views/LoginView.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    redirect: '/chat',
  },
  {
    path: '/chat',
    name: 'chat',
    component: () => import('./views/ChatView.vue'),
  },
  {
    path: '/memory',
    name: 'memory',
    component: () => import('./views/MemoryView.vue'),
  },
  {
    path: '/social',
    name: 'social',
    component: () => import('./views/SocialGraphView.vue'),
  },
  {
    path: '/eval',
    name: 'eval',
    component: () => import('./views/EvalView.vue'),
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/chat',
  },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to) => {
  const auth = useAuthStore();
  if (!to.meta.public && !auth.isAuthed) {
    return { name: 'login', query: { next: to.fullPath } };
  }
  if (to.name === 'login' && auth.isAuthed) {
    return { name: 'chat' };
  }
  return true;
});
