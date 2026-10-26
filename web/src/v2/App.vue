<script setup lang="ts">
/**
 * v2 shell: top navigation (Run / Devices / House / Config), language + theme
 * switches, and the token dialog. It also owns the `/api/v2/meta` startup gate:
 * on a contract mismatch or an unreachable backend no page requests are issued
 * and a readable state is shown instead of an empty shell.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import StateBlock from './components/StateBlock.vue'
import TokenDialog from './components/TokenDialog.vue'
import { token } from './api'
import { loadCapabilities } from './capabilities'
import { describeError } from './errors'
import {
  contractCompatible,
  loadMeta,
  meta,
  metaError,
  metaFeature,
  metaLoading,
  metaSettled,
} from './meta'
import { locale, setLocale, t, themeLabel, type MsgKey } from './i18n'
import { setThemePref, themePref } from '../theme'

const route = useRoute()

const navItems: Array<{ key: string; to: string; labelKey: MsgKey }> = [
  { key: 'run', to: '/run', labelKey: 'v2.nav.run' },
  { key: 'devices', to: '/devices', labelKey: 'v2.nav.devices' },
  { key: 'rooms', to: '/rooms', labelKey: 'v2.nav.rooms' },
  { key: 'config', to: '/voice-assistant', labelKey: 'v2.nav.config' },
]

/** Sub-navigation under the Run section (M7/M8): overview + three run pages. */
const runSubnav: Array<{ key: string; to: string; labelKey: MsgKey }> = [
  { key: 'run', to: '/run', labelKey: 'v2.nav.overview' },
  { key: 'conversation', to: '/conversation', labelKey: 'v2.nav.conversation' },
  { key: 'logs', to: '/logs', labelKey: 'v2.nav.logs' },
  { key: 'memory', to: '/memory', labelKey: 'v2.nav.memory' },
]

/** Sub-navigation under the Config section (M9): four config pages. */
const configSubnav: Array<{ key: string; to: string; labelKey: MsgKey }> = [
  { key: 'voice', to: '/voice-assistant', labelKey: 'v2.nav.voiceAssistant' },
  { key: 'music', to: '/music', labelKey: 'v2.nav.music' },
  { key: 'credentials', to: '/credentials', labelKey: 'v2.nav.credentials' },
  { key: 'health', to: '/health', labelKey: 'v2.nav.health' },
]

const section = computed(() => {
  if (route.path.startsWith('/devices') || route.path.startsWith('/setup')) return 'devices'
  if (route.path.startsWith('/rooms')) return 'rooms'
  if (
    route.path.startsWith('/run') ||
    route.path.startsWith('/conversation') ||
    route.path.startsWith('/logs') ||
    route.path.startsWith('/memory')
  ) {
    return 'run'
  }
  if (
    route.path.startsWith('/config') ||
    route.path.startsWith('/voice-assistant') ||
    route.path.startsWith('/music') ||
    route.path.startsWith('/credentials') ||
    route.path.startsWith('/health')
  ) {
    return 'config'
  }
  return ''
})

const runPage = computed(() => {
  if (route.path.startsWith('/conversation')) return 'conversation'
  if (route.path.startsWith('/logs')) return 'logs'
  if (route.path.startsWith('/memory')) return 'memory'
  return 'run'
})

const configPage = computed(() => {
  if (route.path.startsWith('/music')) return 'music'
  if (route.path.startsWith('/credentials')) return 'credentials'
  if (route.path.startsWith('/health')) return 'health'
  return 'voice'
})

const pageLabel = computed(() => {
  if (section.value === 'devices') return t('v2.nav.devices')
  if (section.value === 'rooms') return t('v2.nav.rooms')
  if (section.value === 'run') {
    if (runPage.value === 'conversation') return t('v2.nav.conversation')
    if (runPage.value === 'logs') return t('v2.nav.logs')
    if (runPage.value === 'memory') return t('v2.nav.memory')
    return t('v2.nav.run')
  }
  if (section.value === 'config') {
    if (configPage.value === 'music') return t('v2.nav.music')
    if (configPage.value === 'credentials') return t('v2.nav.credentials')
    if (configPage.value === 'health') return t('v2.nav.health')
    return t('v2.nav.voiceAssistant')
  }
  return t('v2.nav.devices')
})

const metaErrorCopy = computed(() => (metaError.value ? describeError(metaError.value) : null))
const compatible = computed(() => contractCompatible(meta.value))

const tokenOpen = ref(false)
const tokenNeeded = ref(false)
const hasToken = ref(Boolean(token.get()))

const themeIcon = computed(() =>
  themePref.value === 'auto' ? 'monitor' : themePref.value === 'light' ? 'sun' : 'moon',
)
const themeTitle = computed(() => `${t('v2.nav.theme')} · ${themeLabel(themePref.value)}`)

function cycleTheme(): void {
  const order = ['auto', 'light', 'dark'] as const
  const index = order.indexOf(themePref.value)
  setThemePref(order[(index + 1) % order.length])
}

function openToken(): void {
  tokenOpen.value = true
}

function closeToken(): void {
  tokenOpen.value = false
  hasToken.value = Boolean(token.get())
}

function onUnauthorized(): void {
  tokenNeeded.value = true
  tokenOpen.value = true
}

/**
 * Exactly one boot per app instance. `onMounted` is the only caller today, but
 * the guard keeps the startup gate idempotent (retry, future watchers) so the
 * meta probe and the capability catalog are never requested twice concurrently.
 */
let booting = false
async function boot(): Promise<void> {
  if (booting) return
  booting = true
  try {
    await loadMeta()
    if (meta.value && compatible.value && metaFeature('devices')) void loadCapabilities()
  } finally {
    booting = false
  }
}

watch(
  [locale, pageLabel],
  () => {
    document.title = `${t('v2.app.title')} · ${pageLabel.value}`
  },
  { immediate: true },
)

onMounted(() => {
  window.addEventListener('v2:unauthorized', onUnauthorized)
  void boot()
})

onUnmounted(() => {
  window.removeEventListener('v2:unauthorized', onUnauthorized)
})
</script>

<template>
  <a class="skip-link" href="#v2-main">{{ t('v2.app.skip') }}</a>
  <div class="v2-shell">
    <header class="v2-header">
      <div class="v2-header-inner">
        <RouterLink class="v2-brand" to="/devices" :aria-label="t('v2.app.brand')">
          <span class="v2-brand-mark" aria-hidden="true"><span />rui</span>
          <span class="v2-brand-copy">
            <strong>{{ t('v2.app.brand') }}</strong>
            <span class="kicker">{{ t('v2.app.kicker') }}</span>
          </span>
        </RouterLink>

        <nav class="v2-nav" :aria-label="t('v2.nav.label')">
          <RouterLink
            v-for="item in navItems"
            :key="item.key"
            :to="item.to"
            class="v2-nav-link"
            :class="{ 'is-active': section === item.key }"
            :aria-current="section === item.key ? 'page' : undefined"
          >
            {{ t(item.labelKey) }}
          </RouterLink>
        </nav>

        <div class="v2-tools">
          <div class="v2-lang" role="group" :aria-label="t('v2.nav.language')">
            <button
              type="button"
              class="v2-lang-btn"
              :class="{ 'is-active': locale === 'zh' }"
              :aria-pressed="locale === 'zh'"
              @click="setLocale('zh')"
            >
              {{ t('v2.lang.zh') }}
            </button>
            <button
              type="button"
              class="v2-lang-btn"
              :class="{ 'is-active': locale === 'en' }"
              :aria-pressed="locale === 'en'"
              @click="setLocale('en')"
            >
              {{ t('v2.lang.en') }}
            </button>
          </div>

          <button
            type="button"
            class="btn btn-icon v2-icon-btn"
            :title="themeTitle"
            :aria-label="themeTitle"
            @click="cycleTheme"
          >
            <AppIcon :name="themeIcon" :size="17" />
          </button>

          <button
            type="button"
            class="btn btn-icon v2-icon-btn v2-token-btn"
            :class="{ 'has-token': hasToken }"
            :title="`${t('v2.nav.token')} · ${hasToken ? t('v2.token.configured') : t('v2.token.notConfigured')}`"
            :aria-label="t('v2.nav.token')"
            @click="openToken"
          >
            <AppIcon name="key" :size="17" />
            <span v-if="hasToken" class="v2-token-dot" aria-hidden="true" />
          </button>

          <a class="v2-legacy" href="/" :title="t('v2.nav.legacyHint')">
            <AppIcon name="external" :size="14" />
            <span>{{ t('v2.nav.legacy') }}</span>
          </a>
        </div>
      </div>

      <!-- Run sub-navigation: overview, conversation, logs, memory (M7/M8). -->
      <nav
        v-if="section === 'run'"
        class="v2-subnav"
        :aria-label="t('v2.nav.runSub')"
        data-testid="run-subnav"
      >
        <RouterLink
          v-for="item in runSubnav"
          :key="item.key"
          :to="item.to"
          class="v2-subnav-link"
          :class="{ 'is-active': runPage === item.key }"
          :aria-current="runPage === item.key ? 'page' : undefined"
        >
          {{ t(item.labelKey) }}
        </RouterLink>
      </nav>
      <!-- Config sub-navigation: voice assistant, music, credentials, health (M9). -->
      <nav
        v-if="section === 'config'"
        class="v2-subnav"
        :aria-label="t('v2.nav.configSub')"
        data-testid="config-subnav"
      >
        <RouterLink
          v-for="item in configSubnav"
          :key="item.key"
          :to="item.to"
          class="v2-subnav-link"
          :class="{ 'is-active': configPage === item.key }"
          :aria-current="configPage === item.key ? 'page' : undefined"
        >
          {{ t(item.labelKey) }}
        </RouterLink>
      </nav>
    </header>

    <main id="v2-main" class="v2-main" tabindex="-1">
      <div
        v-if="tokenNeeded && metaErrorCopy?.code !== 'unauthorized'"
        class="banner banner-warn v2-banner"
        role="status"
      >
        <AppIcon name="alert" :size="18" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.token.needed') }}</strong>
          <p class="note">{{ t('v2.token.neededHint') }}</p>
        </div>
        <button type="button" class="btn btn-sm" @click="openToken">
          {{ t('v2.nav.token') }}
        </button>
      </div>

      <StateBlock v-if="metaLoading" kind="loading" :title="t('v2.meta.loading')" />
      <StateBlock
        v-else-if="metaErrorCopy && metaErrorCopy.code !== 'unauthorized'"
        kind="error"
        :title="t('v2.meta.failed')"
        :detail="metaErrorCopy.detail"
        :hint="metaErrorCopy.hint || t('v2.meta.failedHint')"
        :status="metaErrorCopy.status"
        :request-id="metaErrorCopy.requestId"
        :fields="metaErrorCopy.fields"
        show-retry
        @retry="boot"
      />
      <StateBlock
        v-else-if="metaErrorCopy"
        kind="error"
        :title="t('v2.token.needed')"
        :detail="t('v2.token.neededHint')"
        :hint="t('v2.token.recover')"
        :status="metaErrorCopy.status"
        show-retry
        @retry="boot"
      >
        <button type="button" class="btn btn-primary" @click="openToken">
          {{ t('v2.nav.token') }}
        </button>
      </StateBlock>
      <StateBlock
        v-else-if="!compatible"
        kind="error"
        :title="t('v2.meta.incompatible')"
        :detail="t('v2.meta.incompatibleHint', { contract: meta?.contract ?? '—' })"
        :hint="t('v2.meta.failedHint')"
      />
      <!--
        Pages mount only after `/meta` has settled **and** approved this shell:
        a missing/failed contract gate or a 401 shows the states above, so no
        page component is created and no page request is issued.
      -->
      <RouterView v-else-if="metaSettled && meta && compatible" />
    </main>

    <footer v-if="meta && compatible" class="v2-footer">
      <span class="mono v2-footer-meta">
        {{ t('v2.footer.version', { contract: meta.contract, version: meta.rui_version }) }}
      </span>
      <span class="v2-footer-tag">{{ t('v2.footer.readOnly') }}</span>
    </footer>
  </div>

  <TokenDialog :open="tokenOpen" @close="closeToken" />
</template>
