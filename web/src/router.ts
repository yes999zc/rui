import { createRouter, createWebHistory } from 'vue-router'

/**
 * Console routes (docs/console-redesign.md §3).
 *
 * Panels are registry-driven: `/panel/:id` renders whatever the backend declares.
 * The five config pages are ordinary PanelSpecs (component `config-center`), so
 * there is no per-setting route to maintain. Old bookmarks keep working through
 * redirects; the two renamed pages are aliased in PanelHost so a console can talk
 * to a control plane from either side of the rename.
 */
const routes = [
  { path: '/', redirect: '/panel/overview' },

  { path: '/panel/:id', component: () => import('./views/PanelHost.vue') },

  // First-install wizard (a sequence, not a data panel).
  { path: '/setup', component: () => import('./views/SetupView.vue') },

  // Hand-written maintenance pages that the registry does not own.
  { path: '/channels', component: () => import('./views/ChannelsView.vue') },
  { path: '/secrets', component: () => import('./views/SecretsView.vue') },
  { path: '/diagnostics', component: () => import('./views/HealthView.vue') },
  { path: '/tools', component: () => import('./views/ToolsView.vue') },

  // Old paths → their new homes (favorites and docs keep working).
  { path: '/overview', redirect: '/panel/overview' },
  { path: '/devices', redirect: '/panel/devices' },
  { path: '/logs', redirect: '/panel/logs' },
  { path: '/assistant', redirect: '/panel/assistant-session' },
  { path: '/settings', redirect: '/panel/assistant-session' },
  { path: '/voice', redirect: '/panel/foundation' },
  { path: '/models', redirect: '/panel/providers' },

  { path: '/:pathMatch(.*)*', redirect: '/panel/overview' },
]

export const router = createRouter({ history: createWebHistory(), routes })
