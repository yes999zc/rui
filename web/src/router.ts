import { createRouter, createWebHistory } from 'vue-router'

/**
 * Console routes (docs/console-redesign.md §3, docs/console-ia-audit.md §5 Phase 4).
 *
 * Panels are registry-driven: `/panel/:id` renders whatever the backend declares,
 * and that PanelSpec's `nav` manifest is the only navigation source. Phase 4 removed
 * the legacy panel ids and their redirects (assistant-session, settings, phrases,
 * speaker, devices-admin, tools, mcp, foundation) together with the old top-level
 * aliases (/overview, /devices, /logs, /assistant, /settings, /voice, /models, /tools).
 *
 * What remains is the shell: the first-run wizard, the hand-written maintenance
 * pages the registry does not own, `/secrets` for bookmarks, `/` -> overview, and
 * the catch-all. An unknown panel id still renders PanelHost's not-found state.
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

  { path: '/:pathMatch(.*)*', redirect: '/panel/overview' },
]

export const router = createRouter({ history: createWebHistory(), routes })
