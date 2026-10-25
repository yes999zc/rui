import { createRouter, createWebHistory } from 'vue-router'

/**
 * Console routes (docs/console-redesign.md §3, docs/console-ia-audit.md §3/§5).
 *
 * Panels are registry-driven: `/panel/:id` renders whatever the backend declares.
 * Phase 2 merged four destinations while keeping every old panel id declared for
 * one release. Legacy panel URLs redirect to the combined page *and* the tab that
 * used to be their job, so a bookmark lands on the same task, not just the page:
 *
 *   /panel/assistant-session, /panel/phrases, /panel/settings -> voice-assistant
 *   /panel/speaker, /panel/devices-admin                      -> devices (audio/manage)
 *   /panel/tools, /panel/mcp                                  -> integrations
 *   /panel/providers                                          -> unchanged (Phase 3)
 *   /panel/foundation                                         -> system (Phase 3)
 *
 * Phase 3 also adds `/panel/system` and hidden `/panel/credentials`; `/secrets`
 * remains a standalone route for bookmarks.
 *
 * Static aliases are declared before `/panel/:id`; Vue Router ranks them higher
 * than the dynamic segment, but keeping the order here documents the contract.
 */
const routes = [
  { path: '/', redirect: '/panel/overview' },

  // Old panel ids → combined pages (one release of compatibility).
  {
    path: '/panel/assistant-session',
    redirect: { path: '/panel/voice-assistant', query: { tab: 'reply' } },
  },
  {
    path: '/panel/settings',
    redirect: { path: '/panel/voice-assistant', query: { tab: 'reply' } },
  },
  {
    path: '/panel/phrases',
    redirect: { path: '/panel/voice-assistant', query: { tab: 'phrases' } },
  },
  {
    path: '/panel/speaker',
    redirect: { path: '/panel/devices', query: { tab: 'audio' } },
  },
  {
    path: '/panel/devices-admin',
    redirect: { path: '/panel/devices', query: { tab: 'manage' } },
  },
  {
    path: '/panel/tools',
    redirect: { path: '/panel/integrations', query: { tab: 'builtin' } },
  },
  {
    path: '/panel/mcp',
    redirect: { path: '/panel/integrations', query: { tab: 'mcp' } },
  },
  {
    path: '/panel/foundation',
    redirect: { path: '/panel/system' },
  },

  { path: '/panel/:id', component: () => import('./views/PanelHost.vue') },

  // First-install wizard (a sequence, not a data panel).
  { path: '/setup', component: () => import('./views/SetupView.vue') },

  // Hand-written maintenance pages that the registry does not own.
  { path: '/channels', component: () => import('./views/ChannelsView.vue') },
  { path: '/secrets', component: () => import('./views/SecretsView.vue') },
  { path: '/diagnostics', component: () => import('./views/HealthView.vue') },
  { path: '/tools', component: () => import('./views/ToolsView.vue') },

  // Old non-panel paths → their new homes (favorites and docs keep working).
  { path: '/overview', redirect: '/panel/overview' },
  { path: '/devices', redirect: '/panel/devices' },
  { path: '/logs', redirect: '/panel/logs' },
  { path: '/assistant', redirect: '/panel/voice-assistant' },
  { path: '/settings', redirect: '/panel/voice-assistant' },
  { path: '/voice', redirect: '/panel/system' },
  { path: '/models', redirect: '/panel/providers' },

  { path: '/:pathMatch(.*)*', redirect: '/panel/overview' },
]

export const router = createRouter({ history: createWebHistory(), routes })
