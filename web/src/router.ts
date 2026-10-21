import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/devices' },
  { path: '/devices', component: () => import('./views/DevicesView.vue'), meta: { title: '设备' } },
  { path: '/voice', component: () => import('./views/VoiceView.vue'), meta: { title: '语音' } },
  { path: '/models', component: () => import('./views/ModelsView.vue'), meta: { title: '模型' } },
  { path: '/tools', component: () => import('./views/ToolsView.vue'), meta: { title: '工具' } },
  { path: '/health', component: () => import('./views/HealthView.vue'), meta: { title: '体检' } },
  { path: '/logs', component: () => import('./views/LogsView.vue'), meta: { title: '日志' } },
]

export const router = createRouter({ history: createWebHistory(), routes })
