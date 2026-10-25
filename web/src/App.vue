<script setup lang="ts">
/**
 * The console shell (docs/console-redesign.md §3/§5): index rail, a live signal
 * ledger, the language/theme/token controls, and a mobile drawer built from the
 * same rail. Panels still come from the backend registry — the shell never
 * decides which pages exist.
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import SideNav from './components/SideNav.vue'
import BrandMark from './components/BrandMark.vue'
import RecoveryBanner from './components/RecoveryBanner.vue'
import AppIcon from './components/AppIcon.vue'
import { api, token } from './api'
import { usePanels } from './composables/usePanels'
import { formatUptime, locale, panelTitle, t, type MsgKey } from './i18n'

const route = useRoute()
const router = useRouter()
const { panels } = usePanels()

const version = ref('')
const online = ref(false)
const audioOnline = ref(false)
const uptime = ref(0)
const devices = ref(0)
const wakeCount = ref<number | null>(null)
const booted = ref(false)

const drawerOpen = ref(false)
const needToken = ref(false)
const tokenOpen = ref(false)
const tokenDraft = ref(token.get())
const tokenInput = ref<HTMLInputElement | null>(null)
/** The recovery commands are documented with the deploy guide (docs/deploy.md §2.4). */
const tokenDocsUrl =
  'https://github.com/yes999zc/rui/blob/main/docs/deploy.md#24-%E6%8E%A7%E5%88%B6%E5%8F%B0-token'

let timer: number | undefined

/** Server-driven setup state (`GET /api/v1/setup/step`), cached briefly. */
let setupCache: { at: number; complete: boolean } | null = null
const setupComplete = ref(false)
const SETUP_CACHE_MS = 30_000
let nudgeBusy = false
let lastNudge = Date.now()

const signalWord = computed(() => {
  if (!online.value) return t('signal.offline')
  return audioOnline.value ? t('signal.listening') : t('signal.controlOnly')
})

const stateDot = computed(() => (!online.value ? 'fail' : audioOnline.value ? 'live' : 'ok'))

async function refreshSignal(): Promise<void> {
  if (document.hidden) return
  try {
    const health = await api.health()
    version.value = health.version
    uptime.value = health.uptime_s
    const all = health as unknown as { devices?: number; audio_plane?: boolean }
    devices.value = all.devices ?? 0
    audioOnline.value = Boolean(all.audio_plane)
    online.value = health.status === 'ok'
  } catch {
    online.value = false
  }
  try {
    const audio = await api.audio()
    const metrics = audio.metrics as unknown as { wake_count?: number } | null | undefined
    wakeCount.value = metrics?.wake_count ?? null
    audioOnline.value = audio.running
  } catch {
    /* the ledger shows what it has; the page shows the error */
  }
  booted.value = true
  // The rail hides the wizard once setup is complete, so keep the shared state fresh
  // (the 30 s cache means at most one tiny GET per half minute, not a poll loop).
  void refreshSetup(false)
  // While setup is incomplete, keep nudging at most once a minute (cached result);
  // once complete, stop asking.
  if (!setupComplete.value && Date.now() - lastNudge >= 60_000) void nudgeSetup(false)
}

async function setupIsComplete(force: boolean): Promise<boolean> {
  const now = Date.now()
  if (!force && setupCache && now - setupCache.at < SETUP_CACHE_MS) {
    return setupCache.complete
  }
  const state = await api.setupStepState()
  setupCache = { at: Date.now(), complete: state.complete }
  setupComplete.value = state.complete
  return state.complete
}

/** Fetch the setup state even when the current page is not eligible for a nudge. */
async function refreshSetup(force: boolean): Promise<void> {
  try {
    setupComplete.value = await setupIsComplete(force)
  } catch {
    // 401/offline: leave the last known state; the wizard itself reports the error.
  }
}

/**
 * A fresh install lands on the wizard, not on an overview of nothing. The legacy
 * `/api/v1/setup` said "complete" after four steps, so users who had just saved
 * wake words were dropped into an offline overview while connect/test were pending.
 * The eight-step endpoint keeps the nudge alive until connect/test/verify are done.
 *
 * Only landing routes redirect: a bookmarked panel is a deliberate act. Results are
 * cached, so the 10 s signal refresh never turns this into aggressive polling.
 */
async function nudgeSetup(force: boolean): Promise<void> {
  if (nudgeBusy) return
  if (route.path !== '/' && route.path !== '/panel/overview') return
  nudgeBusy = true
  try {
    if (!(await setupIsComplete(force))) await router.replace('/setup')
  } catch {
    // 401/offline: the wizard would not load either; stay put.
  } finally {
    nudgeBusy = false
    lastNudge = Date.now()
  }
}

function saveToken(): void {
  token.set(tokenDraft.value.trim())
  needToken.value = false
  tokenOpen.value = false
  window.location.reload()
}

function onUnauthorized(): void {
  needToken.value = true
  tokenOpen.value = true
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  if (tokenOpen.value) tokenOpen.value = false
  else if (drawerOpen.value) drawerOpen.value = false
}

/** Document title, localised and per-panel (browser tabs are part of the UI). */
const STATIC_TITLES: Record<string, MsgKey> = {
  '/secrets': 'secrets.title',
  '/diagnostics': 'health.title',
  '/setup': 'setup.title',
  '/tools': 'tools.title',
  '/channels': 'channels.title',
}

function updateTitle(): void {
  const panelId = typeof route.params.id === 'string' ? route.params.id : ''
  const panel = panels.value.find((item) => item.id === panelId)
  const key = STATIC_TITLES[route.path]
  const brand = t('brand.name')
  const title = panel ? panelTitle(panel) : key ? t(key) : brand
  document.title = title === brand ? title : `${title} · ${brand}`
}

watch([() => route.fullPath, locale, panels], updateTitle, { immediate: true })

watch(drawerOpen, async (open) => {
  document.body.style.overflow = open ? 'hidden' : ''
  if (open) {
    await nextTick()
    const first = document.querySelector<HTMLElement>('.sidebar .nav-item')
    first?.focus()
  }
})

watch(tokenOpen, async (open) => {
  if (open) {
    await nextTick()
    tokenInput.value?.focus()
  }
})

// A later navigation back to the landing page is checked fresh (cached results are
// only for the periodic refresh): after finishing setup there must be no bounce.
// Other destinations refresh the rail's setup state so the wizard entry disappears
// as soon as the last step is done, wherever the operator lands.
watch(
  () => route.path,
  (path) => {
    if (path === '/' || path === '/panel/overview') void nudgeSetup(true)
    else void refreshSetup(true)
  },
)

onMounted(async () => {
  window.addEventListener('rui-401', onUnauthorized)
  window.addEventListener('keydown', onKeydown)
  document.addEventListener('visibilitychange', refreshSignal)

  await refreshSignal()
  timer = window.setInterval(refreshSignal, 10_000)
  await nudgeSetup(true)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
  window.removeEventListener('rui-401', onUnauthorized)
  window.removeEventListener('keydown', onKeydown)
  document.removeEventListener('visibilitychange', refreshSignal)
  document.body.style.overflow = ''
})
</script>

<template>
  <a class="skip-link" href="#main">{{ t('shell.skip') }}</a>

  <div class="layout">
    <SideNav
      :open="drawerOpen"
      :version="version"
      :online="online"
      :token-needed="needToken"
      :setup-complete="setupComplete"
      @navigate="drawerOpen = false"
      @open-token="tokenOpen = true"
    />

    <div v-if="drawerOpen" class="drawer-backdrop" aria-hidden="true" @click="drawerOpen = false" />

    <div class="main">
      <header class="mobile-bar">
        <button
          type="button"
          class="btn-icon"
          :aria-label="drawerOpen ? t('nav.closeMenu') : t('nav.expand')"
          :aria-expanded="drawerOpen"
          @click="drawerOpen = !drawerOpen"
        >
          <AppIcon :name="drawerOpen ? 'close' : 'menu'" :size="20" />
        </button>
        <span class="mobile-brand">
          <BrandMark :size="24" class="mobile-mark" />
          <span class="mobile-word">{{ t('brand.name') }}</span>
          <span class="mobile-latin">{{ t('brand.latin') }}</span>
        </span>
        <span class="mobile-state">
          <span class="dot" :class="stateDot" />
          <span>{{ signalWord }}</span>
        </span>
      </header>

      <div class="signal-bar" :aria-label="`${signalWord} · v${version || '—'}`">
        <span class="signal-state">
          <span class="dot" :class="stateDot" />
          <span>{{ signalWord }}</span>
        </span>
        <span class="signal-item">
          <span class="signal-k">{{ t('signal.audio') }}</span>
          <span :class="audioOnline ? 'ok' : 'warn'">{{ audioOnline ? t('common.on') : t('common.off') }}</span>
        </span>
        <span class="signal-item">
          <span class="signal-k">{{ t('signal.uptime') }}</span>
          <span class="mono">{{ booted ? formatUptime(uptime) : '—' }}</span>
        </span>
        <span class="signal-item signal-devices">
          <span class="signal-k">{{ t('signal.devices') }}</span>
          <span class="mono">{{ booted ? devices : '—' }}</span>
        </span>
        <span class="signal-item signal-wake">
          <span class="signal-k">{{ t('signal.wake') }}</span>
          <span class="mono">{{ wakeCount ?? '—' }}</span>
        </span>
        <span class="signal-spacer" />
        <RouterLink v-if="online" to="/diagnostics" class="signal-version mono" :title="t('nav.diagnostics')">
          v{{ version || '—' }}
        </RouterLink>
        <span v-else class="signal-version mono" :title="t('signal.offlineHint')">v{{ version || '—' }}</span>
      </div>

      <main id="main" class="content" tabindex="-1">
        <RecoveryBanner />
        <RouterView v-slot="{ Component }">
          <Transition name="fade" mode="out-in">
            <component :is="Component" :key="route.path" />
          </Transition>
        </RouterView>
      </main>
    </div>
  </div>

  <div v-if="tokenOpen" class="modal-backdrop" @click.self="tokenOpen = false">
    <section class="token-modal" role="dialog" aria-modal="true" aria-labelledby="token-title">
      <div class="token-head">
        <div>
          <div class="kicker">{{ t('nav.token.menu') }}</div>
          <h2 id="token-title">{{ needToken ? t('nav.token.needed') : t('nav.token.label') }}</h2>
        </div>
        <button type="button" class="btn-icon" :aria-label="t('common.close')" @click="tokenOpen = false">
          <AppIcon name="close" :size="18" />
        </button>
      </div>
      <p v-if="needToken" class="banner banner-fail">{{ t('nav.token.neededHint') }}</p>
      <div v-if="needToken" class="token-recover">
        <p class="token-recover-title">{{ t('nav.token.recover') }}</p>
        <p class="field-hint">{{ t('nav.token.recoverHint') }}</p>
        <pre class="token-cmd"><code>cd ~/rui &amp;&amp; docker compose exec -T rui rui token</code></pre>
        <pre class="token-cmd"><code>cat ~/rui/console-token.txt</code></pre>
        <p class="field-hint">{{ t('nav.token.recoverSaved') }}</p>
        <a class="token-docs" :href="tokenDocsUrl" target="_blank" rel="noopener noreferrer">
          {{ t('nav.token.recoverDocs') }} ↗
        </a>
      </div>
      <p class="field-hint">{{ t('nav.token.hint') }}</p>
      <label class="sr-only" for="token-input">{{ t('nav.token.label') }}</label>
      <input
        id="token-input"
        ref="tokenInput"
        v-model="tokenDraft"
        type="password"
        autocomplete="off"
        placeholder="X-Auth-Token"
        @keyup.enter="saveToken"
      />
      <div class="actions">
        <button type="button" class="btn btn-primary" :disabled="!tokenDraft.trim()" @click="saveToken">
          {{ t('nav.token.save') }}
        </button>
        <button type="button" class="btn btn-ghost" @click="tokenOpen = false">{{ t('common.cancel') }}</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  min-height: 100vh;
  background: var(--bg);
}

.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.mobile-bar {
  display: none;
}

.signal-bar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 18px;
  height: 38px;
  padding: 0 48px;
  border-bottom: 1px solid var(--line);
  background: var(--bg);
  font-family: var(--font-mono);
  font-size: 10.5px;
  letter-spacing: 0.05em;
  color: var(--muted);
  overflow: hidden;
}

.signal-state {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--ink);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  white-space: nowrap;
}

.signal-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.signal-k {
  text-transform: uppercase;
  letter-spacing: 0.09em;
  color: var(--muted);
}

.signal-spacer {
  flex: 1;
}

.signal-version {
  color: var(--muted);
  white-space: nowrap;
}

.signal-version:hover {
  color: var(--accent);
}

.content {
  width: 100%;
  max-width: var(--content-max);
  padding: 30px 48px 96px;
  flex: 1;
}

.content:focus {
  outline: none;
}

.drawer-backdrop {
  display: none;
}

.modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(10, 9, 7, 0.5);
}

.token-modal {
  width: min(460px, 100%);
  background: var(--surface);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-md);
  box-shadow: var(--shadow-2);
  padding: var(--s5);
}

.token-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s3);
  margin-bottom: var(--s3);
}

.token-head h2 {
  margin-top: 4px;
  font-size: 20px;
}

.token-backdrop,
.token-modal .banner {
  margin-bottom: var(--s3);
}

.token-modal .field-hint {
  margin-bottom: var(--s3);
}

.token-modal .actions {
  margin-top: var(--s4);
}

.token-recover {
  margin-bottom: var(--s3);
  padding: var(--s3);
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
}

.token-recover-title {
  margin-bottom: var(--s1);
  font-size: 12.5px;
  font-weight: 600;
}

.token-recover .field-hint {
  margin-bottom: var(--s2);
}

.token-cmd {
  margin: 0 0 var(--s2);
  padding: var(--s2) var(--s3);
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  font-size: 11.5px;
  line-height: 1.5;
  overflow-x: auto;
}

.token-cmd code {
  font-size: inherit;
  white-space: pre;
}

.token-docs {
  font-size: 12.5px;
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.token-docs:hover {
  color: var(--accent-hover);
}

@media (max-width: 1200px) {
  .signal-bar {
    padding: 0 32px;
  }

  .content {
    padding: 26px 32px 88px;
  }
}

@media (max-width: 720px) {
  .mobile-bar {
    display: flex;
    align-items: center;
    gap: 10px;
    position: sticky;
    top: 0;
    z-index: 30;
    height: calc(var(--header-h) + env(safe-area-inset-top));
    padding: env(safe-area-inset-top) 12px 0;
    background: var(--bg);
    border-bottom: 1px solid var(--line);
  }

  .mobile-brand {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    min-width: 0;
  }

  .mobile-mark {
    flex: none;
  }

  .mobile-word {
    font-family: var(--font-display);
    font-size: 19px;
    font-weight: 700;
    letter-spacing: -0.035em;
    white-space: nowrap;
  }

  .mobile-latin {
    align-self: flex-end;
    margin-bottom: 2px;
    font-family: var(--font-mono);
    font-size: 8.5px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--muted);
  }

  .mobile-state {
    margin-left: auto;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--muted);
  }

  .signal-bar {
    position: static;
    height: 34px;
    padding: 0 16px;
    gap: 14px;
    font-size: 9.5px;
    overflow-x: auto;
    scrollbar-width: none;
  }

  .signal-bar::-webkit-scrollbar {
    display: none;
  }

  /* The mobile bar already states the live word; the ledger keeps only telemetry. */
  .signal-state,
  .signal-devices,
  .signal-wake {
    display: none;
  }

  .content {
    padding: 18px 16px calc(96px + env(safe-area-inset-bottom));
    max-width: none;
  }

  .drawer-backdrop {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 35;
    background: rgba(10, 9, 7, 0.45);
    animation: fade-in var(--dur-mid) var(--ease);
  }
}

@keyframes fade-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}
</style>
