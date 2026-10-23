<script setup lang="ts">
/**
 * Renders one PanelSpec (docs/console.md): polls its source(s) while visible,
 * hands each block its data. The panel's own component escape hatch lands here
 * too — a hand-written view replaces the blocks when a spec declares `component`
 * and receives `component_args` as props.
 *
 * The page chrome (kicker · serif title · status meta) is identical everywhere;
 * blocks are separated by rules, not stacked as identical cards
 * (docs/console-redesign.md §5).
 */
import { computed, defineAsyncComponent, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { usePanels } from '../composables/usePanels'
import { PANEL_COMPONENTS } from '../components/panelComponents'
import { groupTitle, localizeBackend, panelTitle, t } from '../i18n'
import AppIcon from '../components/AppIcon.vue'
import BlockStats from '../components/BlockStats.vue'
import BlockNow from '../components/BlockNow.vue'
import BlockActions from '../components/BlockActions.vue'
import BlockList from '../components/BlockList.vue'
import BlockForm from '../components/BlockForm.vue'
import BlockTable from '../components/BlockTable.vue'
import BlockLog from '../components/BlockLog.vue'
import BlockMemory from '../components/BlockMemory.vue'
import BlockSay from '../components/BlockSay.vue'
import BlockVolume from '../components/BlockVolume.vue'

const route = useRoute()
const router = useRouter()
const { panels, ready, error: registryError, reload } = usePanels()

const panel = computed(() => panels.value.find((p) => p.id === String(route.params.id)))
const main = ref<Record<string, unknown>>({})
const extras = ref<Record<string, unknown>>({})
const error = ref('')
const loaded = ref(false)
let timer: number | undefined

/** Renamed pages stay reachable against a control plane from either release. */
const ALIASES: Record<string, string> = {
  settings: 'assistant-session',
  'assistant-session': 'settings',
}

watch(
  [panel, ready],
  () => {
    if (panel.value || !ready.value || !panels.value.length) return
    const alias = ALIASES[String(route.params.id)]
    if (alias && panels.value.some((p) => p.id === alias)) {
      void router.replace(`/panel/${alias}`)
    }
  },
  { immediate: true },
)

/** Escape hatch: a spec that declares `component` renders a hand-written view. */
const custom = computed(() => {
  const id = panel.value?.component
  if (!id) return null
  const loader = PANEL_COMPONENTS[id]
  return loader ? defineAsyncComponent(loader) : null
})

const componentArgs = computed<Record<string, unknown>>(() => panel.value?.component_args ?? {})

function dataFor(block: { source?: string }): unknown {
  return block.source ? extras.value[block.source] : main.value
}

async function poll() {
  const p = panel.value
  if (!p || document.hidden) return
  try {
    const [primary, extra] = await Promise.all([
      api.json(p.source),
      p.extra_sources
        ? Promise.all(Object.entries(p.extra_sources).map(async ([k, href]) => [k, await api.json(href)] as const))
        : Promise.resolve([] as const),
    ])
    main.value = primary as Record<string, unknown>
    extras.value = Object.fromEntries(extra)
    error.value = ''
    loaded.value = true
  } catch (exc) {
    // Keep the last good data visible; the banner says what failed (the old
    // build's errbox rule: the backend's own words, never a blank page).
    error.value = exc instanceof Error ? exc.message : String(exc)
    loaded.value = true
  }
}

function restart() {
  if (timer) window.clearInterval(timer)
  main.value = {}
  extras.value = {}
  loaded.value = false
  void poll()
  const seconds = panel.value?.refresh_s ?? 5
  if (seconds > 0) timer = window.setInterval(poll, seconds * 1000)
}

// The registry is fetched asynchronously: on a cold deep link `panel` is still
// undefined when the view mounts, so the first poll used to wait for the 5 s
// timer and the overview rendered dashes. Start as soon as the panel appears,
// and restart only when its id actually changes (a focus re-fetch replaces the
// array, not the identity of the page).
watch(
  () => panel.value?.id,
  (id) => { if (id) restart() },
  { immediate: true },
)
onUnmounted(() => { if (timer) window.clearInterval(timer) })
</script>

<template>
  <div v-if="!ready" class="panel-loading" role="status">
    <div class="skeleton skeleton-line" style="width: 30%; height: 26px" />
    <div class="skeleton skeleton-line" style="width: 68%" />
    <div class="skeleton skeleton-line" style="width: 52%" />
    <p class="note">{{ t('page.loading') }}</p>
  </div>

  <div v-else-if="!panel" class="panel-missing">
    <header class="page-head">
      <div>
        <div class="kicker">{{ t('nav.run') }}</div>
        <h1>{{ t('page.notFound') }}</h1>
      </div>
    </header>
    <div class="banner banner-warn">
      <AppIcon name="alert" :size="18" class="banner-icon" />
      <div>
        <p>{{ t('page.notFoundHint', { id: String(route.params.id) }) }}</p>
        <p v-if="registryError" class="note mono">{{ registryError }}</p>
        <div class="actions" style="margin-top: 10px">
          <button class="btn btn-sm" @click="reload()">{{ t('common.retry') }}</button>
          <RouterLink to="/panel/overview" class="btn btn-sm btn-ghost">{{ t('backend.overview') }}</RouterLink>
        </div>
      </div>
    </div>
  </div>

  <template v-else>
    <header class="page-head">
      <div>
        <div class="kicker">{{ groupTitle(panel.group) }}</div>
        <h1>{{ panelTitle(panel) }}</h1>
      </div>
      <div v-if="!panel.enabled" class="page-head-side">
        <span class="badge badge-warn">{{ t('nav.disabledReason') }}</span>
      </div>
    </header>

    <div v-if="!panel.enabled" class="banner banner-warn">
      <AppIcon name="alert" :size="18" class="banner-icon" />
      <div>
        <strong>{{ t('page.disabled') }}</strong>
        <p class="note">{{ localizeBackend(panel.enabled_reason) }}</p>
      </div>
    </div>

    <template v-else>
      <div v-if="error" class="banner banner-fail panel-error">
        <AppIcon name="alert" :size="16" class="banner-icon" />
        <span>
          {{ error }}
          <span class="muted">{{ loaded && Object.keys(main).length ? t('page.staleData') : '' }}</span>
        </span>
      </div>

      <div v-if="panel.component && !custom" class="banner banner-fail">
        <AppIcon name="alert" :size="16" class="banner-icon" />
        <span>{{ t('page.componentMissing', { id: panel.id, component: panel.component }) }}</span>
      </div>

      <component :is="custom" v-else-if="custom" v-bind="componentArgs" :panel-id="panel.id" />

      <div v-else class="blocks" :class="{ 'blocks-overview': panel.id === 'overview' }">
        <div
          v-for="(block, index) in panel.blocks"
          :key="block.type + (block.title ?? '') + index"
          class="block-slot"
          :class="`b-${block.type}`"
        >
          <BlockStats v-if="block.type === 'stats'" :block="block" :data="dataFor(block)" />
          <BlockNow v-else-if="block.type === 'now'" :block="block" :data="dataFor(block)" />
          <BlockActions v-else-if="block.type === 'actions'" :block="block" :data="dataFor(block)" :refresh="poll" />
          <BlockList v-else-if="block.type === 'list'" :block="block" :data="dataFor(block)" />
          <BlockForm v-else-if="block.type === 'form'" :block="block" :data="dataFor(block)" :refresh="poll" />
          <BlockTable v-else-if="block.type === 'table'" :block="block" :data="dataFor(block)" />
          <BlockLog v-else-if="block.type === 'log'" :block="block" :data="dataFor(block)" />
          <BlockMemory v-else-if="block.type === 'memory'" :block="block" :data="dataFor(block)" :refresh="poll" />
          <BlockSay v-else-if="block.type === 'say'" :block="block" :data="dataFor(block)" />
          <BlockVolume v-else-if="block.type === 'volume'" :block="block" :data="dataFor(block)" />
          <div v-else class="banner banner-warn">
            <AppIcon name="alert" :size="16" class="banner-icon" />
            <span>{{ t('page.blockUnknown', { type: block.type }) }}</span>
          </div>
        </div>
      </div>

      <p v-if="panel.id === 'overview'" class="panel-footnote meta">{{ t('overview.footnote') }}</p>
    </template>
  </template>
</template>

<style scoped>
.panel-loading,
.panel-missing {
  padding-top: var(--s2);
}

.panel-error {
  margin-bottom: var(--s5);
}

.skeleton-line + .skeleton-line {
  margin-top: 10px;
}

.panel-footnote {
  margin-top: var(--s7);
  padding-top: var(--s3);
  border-top: 1px solid var(--line);
  max-width: 64ch;
  line-height: 1.6;
}

/*
 * Overview composition (docs/console-redesign.md §5.1): an asymmetric 7/5 grid —
 * the try-it composer and the one destructive action on the left, the telemetry
 * ledgers on the right. Other panels stay a single editorial column.
 */
.blocks-overview {
  display: grid;
  grid-template-columns: minmax(0, 7fr) minmax(0, 5fr);
  gap: var(--s6) var(--s7);
  align-items: start;
}

.blocks-overview .block-slot {
  margin-top: 0 !important;
  min-width: 0;
}

.blocks-overview .b-say {
  grid-column: 1;
  grid-row: 1;
}

.blocks-overview .b-actions {
  grid-column: 1;
  grid-row: 2;
}

.blocks-overview .b-stats:nth-child(2) {
  grid-column: 2;
  grid-row: 1;
}

.blocks-overview .b-stats:nth-child(3) {
  grid-column: 2;
  grid-row: 2;
}

.blocks-overview .block-slot:nth-child(n + 5) {
  grid-column: 1 / -1;
}

/*
 * In the telemetry rail the stats are a ledger, not a tile grid: one label/value
 * row per reading, ruled like an instrument's scale. The 2×2 bento silhouette
 * only survives on a phone, where rows would push content too far down.
 */
.blocks-overview :deep(.metric-strip) {
  display: block;
  border-top: 1px solid var(--line-strong);
  border-bottom: 1px solid var(--line);
}

.blocks-overview :deep(.metric) {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--s4);
  padding: 9px 0;
  border-top: 1px solid var(--line);
  border-left: 0;
}

.blocks-overview :deep(.metric:first-child) {
  border-top: 0;
}

.blocks-overview :deep(.metric-value) {
  margin-top: 0;
  font-size: 20px;
  text-align: right;
  white-space: nowrap;
}

@media (max-width: 720px) {
  .blocks-overview :deep(.metric-strip) {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .blocks-overview :deep(.metric) {
    display: block;
    padding: 12px 12px 11px;
    border-top: 1px solid var(--line);
    border-left: 1px solid var(--line);
  }

  .blocks-overview :deep(.metric:nth-child(-n + 2)) {
    border-top: 0;
  }

  .blocks-overview :deep(.metric:nth-child(odd)) {
    border-left: 0;
    padding-left: 0;
  }

  .blocks-overview :deep(.metric-value) {
    margin-top: 3px;
    font-size: 22px;
    text-align: left;
  }
}

@media (max-width: 1100px) {
  .blocks-overview {
    display: block;
  }

  .blocks-overview .block-slot {
    grid-column: auto;
    grid-row: auto;
  }

  .blocks-overview .block-slot + .block-slot {
    margin-top: var(--s7) !important;
  }
}
</style>
