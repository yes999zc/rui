import { createRouter, createWebHistory } from 'vue-router'

/**
 * v2 routes live under `/v2/` (contract C-002 §2.1). The IA is three sections —
 * Run / Devices / Config — and M1 only implements the device domain:
 *
 *   /v2/devices          device list grouped by room
 *   /v2/devices/add      create a device from an available plugin (M3)
 *   /v2/devices/:id      device detail (`?tab=config|danger` are M3 writes)
 *   /v2/run, /v2/config  honest "not in this milestone" states
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
  { path: '/run', name: 'run', component: () => import('./views/RunView.vue') },
  { path: '/config', name: 'config', component: () => import('./views/ConfigView.vue') },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('./views/NotFoundView.vue') },
]

export const router = createRouter({
  history: createWebHistory('/v2/'),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})
