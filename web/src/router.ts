import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  // Registry-driven panels first (docs/console.md): the backend declares what
  // exists, PanelHost renders it. A few read-only reference pages are still
  // hand-written; the ones whose content is a panel now redirect to it.
  { path: '/', redirect: '/panel/overview' },
  { path: '/panel/:id', component: () => import('./views/PanelHost.vue') },
  { path: '/overview', redirect: '/panel/overview' },
  { path: '/devices', redirect: '/panel/devices' },
  { path: '/assistant', redirect: '/panel/settings' },
  { path: '/logs', redirect: '/panel/logs' },
  { path: '/voice', component: () => import('./views/VoiceView.vue'), meta: { title: '语音' } },
  { path: '/models', component: () => import('./views/ModelsView.vue'), meta: { title: '模型' } },
  { path: '/tools', component: () => import('./views/ToolsView.vue'), meta: { title: '工具' } },
  { path: '/secrets', component: () => import('./views/SecretsView.vue'), meta: { title: '密钥' } },
  { path: '/diagnostics', component: () => import('./views/HealthView.vue'), meta: { title: '体检' } },
]

export const router = createRouter({ history: createWebHistory(), routes })
