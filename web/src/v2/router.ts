import { createRouter, createWebHistory } from 'vue-router'

/**
 * v2 routes live under `/v2/` (contract C-002 §2.1). The design IA is three
 * sections — Run / Devices / Config — with rooms/house as a Devices-area page;
 * the flat top nav exposes it between Devices and Config:
 *
 *   /v2/devices          device list grouped by room
 *   /v2/devices/add      create a device from an available plugin (M3 → M6 wizard)
 *   /v2/setup            first-run eight-step wizard (M6)
 *   /v2/devices/:id      device detail (`?tab=config|danger` are M3 writes)
 *   /v2/rooms            rooms, multi-device policy, global audio (M5)
 *   /v2/run              house overview with exceptions first (M7)
 *   /v2/conversation     recent turns, device/room badges and filters (M8)
 *   /v2/logs             service-log tail with level filter and copy (M8)
 *   /v2/memory           memory list + add/confirm/remove (M8)
 *   /v2/voice-assistant  catalog-driven assistant/session/audio config (M9a)
 *   /v2/tools            catalog-driven weather/news/search/MCP/HA config (M9c)
 *   /v2/music            sources, playback status and write-only credentials (M9b)
 *   /v2/credentials      secret presence, writes and provider probes (M9b)
 *   /v2/health           health, dependency probes and config recovery (M9b)
 *   /v2/config           compatibility redirect to /voice-assistant
 *
 * There is deliberately no import of the legacy panel registry, PanelHost, or
 * any legacy view: v2 owns its route table.
 */
const routes = [
  { path: '/', redirect: '/devices' },
  { path: '/devices', name: 'devices', component: () => import('./views/DevicesView.vue') },
  {
    // Reserved: `/devices/add` shadows a device with id `add`, so the create
    // form rejects that id (case-insensitive). Keep this path per the design.
    path: '/devices/add',
    name: 'device-add',
    component: () => import('./views/AddDeviceView.vue'),
  },
  {
    path: '/devices/:id',
    name: 'device-detail',
    component: () => import('./views/DeviceDetailView.vue'),
    props: true,
  },
  { path: '/rooms', name: 'rooms', component: () => import('./views/RoomsView.vue') },
  {
    // First-run wizard: the backend decides first-run vs add from the house state.
    path: '/setup',
    name: 'setup',
    component: () => import('./views/SetupView.vue'),
  },
  { path: '/run', name: 'run', component: () => import('./views/RunView.vue') },
  {
    path: '/conversation',
    name: 'conversation',
    component: () => import('./views/ConversationView.vue'),
  },
  { path: '/logs', name: 'logs', component: () => import('./views/LogsView.vue') },
  { path: '/memory', name: 'memory', component: () => import('./views/MemoryView.vue') },
  // `/config` was a stub through M8; keep the URL working as the section entry.
  { path: '/config', redirect: '/voice-assistant' },
  {
    path: '/voice-assistant',
    name: 'voice-assistant',
    component: () => import('./views/VoiceAssistantView.vue'),
  },
  {
    path: '/tools',
    name: 'tools',
    component: () => import('./views/ToolsView.vue'),
  },
  { path: '/music', name: 'music', component: () => import('./views/MusicView.vue') },
  {
    path: '/credentials',
    name: 'credentials',
    component: () => import('./views/CredentialsView.vue'),
  },
  { path: '/health', name: 'health', component: () => import('./views/HealthView.vue') },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('./views/NotFoundView.vue') },
]

export const router = createRouter({
  history: createWebHistory('/v2/'),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})
