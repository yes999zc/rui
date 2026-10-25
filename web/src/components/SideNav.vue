<script setup lang="ts">
/**
 * The index rail (docs/console-redesign.md §3/§5): registry-driven navigation.
 * Every panel brings its own nav manifest from the backend; the only frontend-side
 * destinations are the utility routes declared in web/src/nav.ts. On a phone the
 * same element becomes the drawer — one instance, so state and listeners never
 * double up.
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import AppIcon from './AppIcon.vue'
import BrandMark from './BrandMark.vue'
import { usePanels } from '../composables/usePanels'
import type { PanelSpec } from '../panels'
import { buildNav } from '../nav'
import { locale, localizeBackend, panelTitle, setLocale, t } from '../i18n'
import { setThemePref, themePref } from '../theme'

const props = defineProps<{
  version: string
  online: boolean
  tokenNeeded: boolean
  /** The wizard is a first-run destination, not a permanent one (IA audit §2.6). */
  setupComplete?: boolean
  open?: boolean
}>()
const emit = defineEmits<{ navigate: []; 'open-token': [] }>()

/** On a phone the rail is an off-canvas drawer: when it is closed it must leave
 * the accessibility tree and the tab order, not just sit at translateX(-100%). */
const isMobile = ref(false)
const navEl = ref<HTMLElement | null>(null)
const navScrollable = ref(false)
const navAtBottom = ref(false)

/** Tell the reader the drawer continues below the fold (three destinations used to hide). */
function updateNavScroll() {
  const el = navEl.value
  if (!el) {
    navScrollable.value = false
    return
  }
  navScrollable.value = el.scrollHeight - el.clientHeight > 8
  navAtBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 10
}

function onNavScroll() {
  updateNavScroll()
}
let query: MediaQueryList | null = null
function syncMobile() {
  isMobile.value = query?.matches ?? false
}
onMounted(() => {
  query = window.matchMedia('(max-width: 720px)')
  syncMobile()
  query.addEventListener('change', syncMobile)
})
onUnmounted(() => query?.removeEventListener('change', syncMobile))
watch(
  () => props.open,
  async () => {
    await nextTick()
    updateNavScroll()
  },
)
onMounted(async () => {
  await nextTick()
  updateNavScroll()
})

const route = useRoute()
const { panels } = usePanels()

/**
 * The rail is the backend nav manifest (panel.nav: section/order/hidden/icon) plus
 * the hand-written utility routes from nav.ts. There are no id→group maps here.
 */
const groups = computed(() => buildNav(panels.value, Boolean(props.setupComplete)))

function panelActive(panel: PanelSpec): boolean {
  const current = typeof route.params.id === 'string' ? route.params.id : ''
  return current === panel.id
}

function utilityActive(path: string): boolean {
  return route.path === path
}

const groupsLabel = computed(() => (props.online ? 'nav.brandSub' : 'signal.offline'))

const themes = [
  { id: 'auto', icon: 'monitor', labelKey: 'nav.theme.auto' },
  { id: 'light', icon: 'sun', labelKey: 'nav.theme.light' },
  { id: 'dark', icon: 'moon', labelKey: 'nav.theme.dark' },
] as const

// The drawer closes after any navigation, including a click on the current page.
watch(
  () => route.fullPath,
  () => emit('navigate'),
)
</script>

<template>
  <aside
    class="sidebar"
    :class="{ open: props.open }"
    :inert="isMobile && !props.open"
    :aria-hidden="isMobile && !props.open ? 'true' : undefined"
  >
    <RouterLink to="/" class="brand" :aria-label="`${t('brand.name')} · ${t('nav.brandSub')}`">
      <span class="brand-line">
        <BrandMark :size="30" class="brand-mark" />
        <span class="brand-word">{{ t('brand.name') }}</span>
        <span class="brand-latin">{{ t('brand.latin') }}</span>
      </span>
      <span class="brand-meta">
        <span class="dot" :class="online ? 'live' : 'fail'" />
        <span>{{ online ? t('signal.online') : t('signal.offline') }}</span>
        <span aria-hidden="true">·</span>
        <span>v{{ version || '—' }}</span>
      </span>
      <span class="brand-sub">{{ t(groupsLabel) }}</span>
    </RouterLink>

    <div class="nav-wrap" :class="{ scrollable: navScrollable, 'at-bottom': navAtBottom }">
      <nav ref="navEl" class="nav" :aria-label="t('nav.menu')" @scroll="onNavScroll">
      <template v-for="group in groups" :key="group.id">
        <div class="nav-label">{{ t(group.title) }}</div>
        <template v-for="item in group.items" :key="item.key">
          <RouterLink
            v-if="item.kind === 'panel'"
            :to="`/panel/${item.panel.id}`"
            class="nav-item"
            :class="{ active: panelActive(item.panel), disabled: !item.panel.enabled }"
            :title="item.panel.enabled ? undefined : localizeBackend(item.panel.enabled_reason) || t('nav.disabledReason')"
          >
            <AppIcon :name="item.panel.nav.icon" :size="16" />
            <span class="nav-text">{{ panelTitle(item.panel) }}</span>
            <span v-if="!item.panel.enabled" class="nav-off">{{ t('common.disabled') }}</span>
          </RouterLink>
          <RouterLink
            v-else
            :to="item.path"
            class="nav-item"
            :class="{ active: utilityActive(item.path) }"
          >
            <AppIcon :name="item.icon" :size="16" />
            <span class="nav-text">{{ t(item.labelKey) }}</span>
          </RouterLink>
        </template>
      </template>
      </nav>
    </div>

    <div class="rail-foot">
      <div class="control-block">
        <div class="control-label">{{ t('nav.language') }}</div>
        <div class="segmented" role="group" :aria-label="t('nav.language')">
          <button type="button" :class="{ on: locale === 'zh' }" :aria-pressed="locale === 'zh'" @click="setLocale('zh')">
            {{ t('nav.lang.zh') }}
          </button>
          <button type="button" :class="{ on: locale === 'en' }" :aria-pressed="locale === 'en'" @click="setLocale('en')">
            EN
          </button>
        </div>
      </div>

      <div class="control-block">
        <div class="control-label">{{ t('nav.theme') }}</div>
        <div class="segmented segmented-icons" role="group" :aria-label="t('nav.theme')">
          <button
            v-for="theme in themes"
            :key="theme.id"
            type="button"
            :class="{ on: themePref === theme.id }"
            :aria-pressed="themePref === theme.id"
            :title="t(theme.labelKey)"
            :aria-label="t(theme.labelKey)"
            @click="setThemePref(theme.id)"
          >
            <AppIcon :name="theme.icon" :size="15" />
          </button>
        </div>
      </div>

      <button type="button" class="token-button" :class="{ attention: tokenNeeded }" @click="emit('open-token')">
        <AppIcon name="key" :size="15" />
        <span>{{ t('nav.token.menu') }}</span>
        <span v-if="tokenNeeded" class="dot fail" />
      </button>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: var(--sidebar-w);
  flex: none;
  display: flex;
  flex-direction: column;
  height: 100vh;
  position: sticky;
  top: 0;
  padding: 22px 14px 14px;
  border-right: 1px solid var(--line);
  background: var(--bg);
  z-index: 40;
}

.brand {
  display: block;
  padding: 0 10px 20px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 6px;
}

.brand-line {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}

.brand-mark {
  flex: none;
}

.brand-word {
  display: inline-block;
  font-family: var(--font-display);
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.035em;
  line-height: 1.05;
  white-space: nowrap;
}

.brand-latin {
  align-self: flex-end;
  margin-bottom: 2px;
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--muted);
  white-space: nowrap;
}

.brand-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 7px;
  font-family: var(--font-mono);
  font-size: 10.5px;
  letter-spacing: 0.04em;
  color: var(--muted);
}

.brand-sub {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  color: var(--muted);
}

.nav-wrap {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
}

.nav-wrap::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 36px;
  background: linear-gradient(to bottom, transparent, var(--bg));
  pointer-events: none;
  opacity: 0;
  transition: opacity var(--dur-mid) var(--ease);
}

.nav-wrap.scrollable:not(.at-bottom)::after {
  opacity: 1;
}

.nav {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 6px 0 10px;
  scrollbar-width: thin;
}

.nav-label {
  padding: 16px 10px 5px;
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--muted);
}

.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 7px 10px;
  border-radius: var(--r-sm);
  color: var(--ink-2);
  font-size: 13px;
  line-height: 1.35;
  transition: background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
}

.nav-item:hover {
  background: var(--surface-2);
  color: var(--ink);
}

.nav-item.active {
  color: var(--ink);
  font-weight: 550;
  background: var(--surface);
  box-shadow: inset 0 0 0 1px var(--line);
}

.nav-item.active::before {
  content: "";
  position: absolute;
  left: -4px;
  top: 8px;
  bottom: 8px;
  width: 2px;
  border-radius: 2px;
  background: var(--accent);
}

.nav-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nav-item.disabled {
  opacity: 0.5;
}

.nav-off {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
}

.rail-foot {
  border-top: 1px solid var(--line);
  padding-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.control-block {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 4px;
}

.control-label {
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--muted);
}

.segmented {
  display: inline-flex;
  padding: 2px;
  border-radius: var(--r-sm);
  border: 1px solid var(--line);
  background: var(--surface);
  gap: 2px;
}

.segmented button {
  border: 0;
  background: transparent;
  color: var(--muted);
  padding: 3px 8px;
  border-radius: var(--r-xs);
  font-size: 11.5px;
  line-height: 1.4;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.segmented button:hover {
  color: var(--ink);
}

.segmented button.on {
  background: var(--ink);
  color: var(--bg);
}

.segmented-icons button {
  padding: 4px 6px;
}

.token-button {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  border-radius: var(--r-sm);
  border: 1px solid transparent;
  background: transparent;
  color: var(--muted);
  font-size: 12.5px;
  cursor: pointer;
  text-align: left;
}

.token-button:hover {
  background: var(--surface-2);
  color: var(--ink);
}

.token-button.attention {
  color: var(--fail);
  border-color: var(--fail);
  background: var(--fail-soft);
}

.token-button .dot {
  margin-left: auto;
}

@media (max-width: 720px) {
  .sidebar {
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    width: min(320px, 86vw);
    height: 100dvh;
    display: block;
    overflow-y: auto;
    overscroll-behavior: contain;
    transform: translateX(-102%);
    transition: transform var(--dur-slow) var(--ease);
    box-shadow: var(--shadow-2);
    padding-top: calc(16px + env(safe-area-inset-top));
    padding-bottom: calc(16px + env(safe-area-inset-bottom));
    border-right: 1px solid var(--line-strong);
  }

  /* The whole rail scrolls on a phone, so 密钥/体检/向导 are reachable before the
   * appearance controls instead of hiding under a pinned footer. */
  .nav-wrap,
  .nav {
    display: block;
    flex: none;
    overflow: visible;
    min-height: 0;
  }

  .nav-wrap::after {
    display: none;
  }

  .nav-item {
    min-height: 44px;
  }

  /* Language / theme / token stay reachable without scrolling the whole rail. */
  .rail-foot {
    position: sticky;
    bottom: 0;
    z-index: 2;
    margin: var(--s4) -14px 0;
    padding: 12px 14px calc(12px + env(safe-area-inset-bottom));
    background: var(--bg);
    border-top: 1px solid var(--line);
  }

  .sidebar::after {
    content: "";
  }

  .sidebar.open {
    transform: translateX(0);
  }
}
</style>
