<script setup lang="ts">
/**
 * `/v2/tools` — catalog-driven weather / news / search / MCP / Home Assistant
 * configuration, plus read-only MCP/HA connection state.
 *
 * Data sources:
 *  - `GET /api/v1/config/catalog` — tools groups, field order, kinds/ranges,
 *    current values and each field's `effect`;
 *  - `GET /api/v1/config` — redacted effective config, used only as a value
 *    fallback when a catalog entry has no value;
 *  - `GET /api/v1/mcp` — live MCP/HA status (presence only, never a token).
 *
 * Saving sends a field-level diff to `PUT /api/v1/config/values`: exactly the
 * dotted paths the operator changed. `effect=restart` fields open a confirmation
 * first. A 422 is mapped back to `details.fields[].path` and marks that row red.
 *
 * Section membership comes from `tools.ts`: weather / news / MCP (incl. HA) /
 * other, derived from catalog group ids (or, for a flat catalog, path prefixes).
 */
import { computed, nextTick, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import ErrorNotice from '../components/device/ErrorNotice.vue'
import StateBlock from '../components/StateBlock.vue'
import ConfigGroupCard from '../components/config/ConfigGroupCard.vue'
import {
  api,
  type ConfigFieldSpec,
  type ConfigValuesResponse,
  type McpStatus,
} from '../api'
import {
  buildConfigDiff,
  cloneValue,
  initialDraftValue,
  isBlankNumber,
  valuesEqual,
  valueFor,
} from '../config'
import { describeError, normalizeFieldPath, type ErrorCopy } from '../errors'
import { formatDateTime, formatIso } from '../format'
import { pickLocalized, t, type MsgKey } from '../i18n'
import { toolsFields, toolsSections, type ToolsSectionKey } from '../tools'
import { useResource } from '../useResource'

const catalogResource = useResource(() => api.configCatalog())
const configResource = useResource(() => api.config())
const statusResource = useResource(() => api.mcp())
const { data: catalog, error: catalogError, loading: catalogLoading } = catalogResource
const { data: configDoc, error: configError } = configResource
const { data: mcpStatus, error: mcpError, loading: mcpLoading } = statusResource

const original = ref<Record<string, unknown>>({})
const draft = ref<Record<string, unknown>>({})
const fieldErrors = ref<Record<string, string>>({})
const formError = ref<ErrorCopy | null>(null)
const saved = ref<ConfigValuesResponse | null>(null)
const busy = ref(false)
const noChanges = ref(false)
const confirmRestart = ref(false)
const confirmRefresh = ref(false)
const restartPaths = ref<string[]>([])
/** Client-side read time; the server reports no handshake timestamp for HA. */
const statusCheckedAt = ref('')
let pendingDiff: ReturnType<typeof buildConfigDiff> | null = null

const catalogErrorCopy = computed(() =>
  catalogError.value ? describeError(catalogError.value) : null,
)
const configErrorCopy = computed(() =>
  configError.value ? describeError(configError.value) : null,
)
const mcpErrorCopy = computed(() => (mcpError.value ? describeError(mcpError.value) : null))

/** Only catalog fields under `tools` are edited here; other pages own the rest. */
const fields = computed<ConfigFieldSpec[]>(() =>
  catalog.value ? toolsFields(catalog.value) : [],
)
const sections = computed(() => (catalog.value ? toolsSections(catalog.value) : []))

const SECTION_LABELS: Record<ToolsSectionKey, MsgKey> = {
  weather: 'v2.tools.section.weather',
  news: 'v2.tools.section.news',
  mcp: 'v2.tools.section.mcp',
  other: 'v2.tools.section.other',
}

function sectionCount(key: ToolsSectionKey): number {
  const section = sections.value.find((entry) => entry.key === key)
  if (!section) return 0
  return section.groups.reduce((total, group) => total + group.fields.length, 0)
}

const changedPaths = computed<Set<string>>(() => {
  const set = new Set<string>()
  for (const field of fields.value) {
    if (!field.writable) continue
    const before = original.value[field.path]
    const after = draft.value[field.path]
    if (!valuesEqual(before, after)) set.add(field.path)
  }
  return set
})

const dirtyCount = computed(() => changedPaths.value.size)
const configVersion = computed(() => {
  const value = configDoc.value?.version
  return typeof value === 'number' ? value : null
})
const restartCount = computed(() => {
  let count = 0
  for (const field of fields.value) {
    if (field.effect === 'restart' && changedPaths.value.has(field.path)) count += 1
  }
  return count
})

const restartLabels = computed(() => {
  const labels: string[] = []
  if (!catalog.value) return labels
  for (const path of restartPaths.value) {
    const field = fields.value.find((item) => item.path === path)
    labels.push(field ? pickLocalized(field.label) : path)
  }
  return labels
})

type ConnState = 'available' | 'unavailable' | 'unconfigured'

interface StatusRow {
  key: string
  name: string
  kind: string
  state: ConnState
  details: string[]
  error: string
  lastChecked: string
}

const STATUS_LABELS: Record<ConnState, MsgKey> = {
  available: 'v2.tools.status.available',
  unavailable: 'v2.tools.status.unavailable',
  unconfigured: 'v2.tools.status.unconfigured',
}

const STATUS_BADGES: Record<ConnState, string> = {
  available: 'badge-ok',
  unavailable: 'badge-fail',
  unconfigured: 'badge-warn',
}

/**
 * One row per first-party instance (HA) and per configured generic server. A
 * disabled server is still a configured row; its state reads unavailable and
 * the detail says it is turned off, so the page never implies it is missing.
 */
const statusRows = computed<StatusRow[]>(() => {
  const status: McpStatus | null = mcpStatus.value
  if (!status) return []
  const rows: StatusRow[] = []

  for (const instance of status.first_party ?? []) {
    const wired = Boolean(instance.wired)
    const state: ConnState = !wired
      ? 'unconfigured'
      : instance.connected
        ? 'available'
        : 'unavailable'
    const haError = instance.id === 'ha' ? status.ha?.error ?? '' : ''
    rows.push({
      key: `first-party:${instance.id}`,
      name: instance.name || instance.id,
      kind: t('v2.tools.status.kindFirstParty'),
      state,
      details: [
        instance.tools ? t('v2.tools.status.tools', { n: instance.tools }) : '',
        instance.entities ? t('v2.tools.status.entities', { n: instance.entities }) : '',
        instance.latency_ms != null
          ? t('v2.tools.status.latency', { ms: Math.round(instance.latency_ms) })
          : '',
      ].filter(Boolean),
      error: state === 'unavailable' ? haError : '',
      // `HaBridge.status` has no timestamp; the section header carries the read time.
      lastChecked: '',
    })
  }

  for (const server of status.servers ?? []) {
    const state: ConnState = server.enabled
      ? server.connected
        ? 'available'
        : 'unavailable'
      : 'unavailable'
    rows.push({
      key: `server:${server.name}`,
      name: server.name,
      kind: server.transport
        ? `${t('v2.tools.status.kindGeneric')} · ${server.transport}`
        : t('v2.tools.status.kindGeneric'),
      state,
      details: [
        server.enabled ? '' : t('v2.tools.status.disabled'),
        server.connected ? t('v2.tools.status.tools', { n: server.tools }) : '',
        server.latency_ms != null
          ? t('v2.tools.status.latency', { ms: Math.round(server.latency_ms) })
          : '',
      ].filter(Boolean),
      error: server.error || '',
      lastChecked: server.last_seen != null ? formatDateTime(server.last_seen) : '',
    })
  }

  return rows
})

const statusCheckedLabel = computed(() =>
  statusCheckedAt.value ? formatIso(statusCheckedAt.value) : '',
)
const genericSummary = computed(() => {
  const generic = mcpStatus.value?.generic
  if (!generic) return ''
  return t('v2.tools.status.genericSummary', {
    connected: generic.connected,
    enabled: generic.enabled,
  })
})

function hydrate(): void {
  if (!catalog.value) return
  const next: Record<string, unknown> = {}
  for (const field of fields.value) {
    next[field.path] = initialDraftValue(
      field,
      valueFor(catalog.value, configDoc.value, field.path),
    )
  }
  original.value = cloneValue(next)
  draft.value = cloneValue(next)
  fieldErrors.value = {}
}

async function loadStatus(): Promise<void> {
  await statusResource.load()
  if (!mcpError.value) statusCheckedAt.value = new Date().toISOString()
}

async function boot(): Promise<void> {
  await Promise.all([catalogResource.load(), configResource.load(), loadStatus()])
  hydrate()
}

function setField(path: string, value: unknown): void {
  draft.value = { ...draft.value, [path]: value }
  const next = { ...fieldErrors.value }
  delete next[path]
  const field = fields.value.find((item) => item.path === path)
  if (field && isBlankNumber(field, value)) {
    next[path] = t('v2.config.numberRequired')
  }
  fieldErrors.value = next
  saved.value = null
  noChanges.value = false
}

function resetDraft(): void {
  draft.value = cloneValue(original.value)
  fieldErrors.value = {}
  formError.value = null
  noChanges.value = false
}

/** Empty non-nullable numbers are an input error, not an omitted diff entry. */
function validateDraft(): boolean {
  const errors: Record<string, string> = {}
  for (const field of fields.value) {
    if (!field.writable || !changedPaths.value.has(field.path)) continue
    if (isBlankNumber(field, draft.value[field.path])) {
      errors[field.path] = t('v2.config.numberRequired')
    }
  }
  if (!Object.keys(errors).length) return true
  fieldErrors.value = { ...fieldErrors.value, ...errors }
  noChanges.value = false
  return false
}

function requestSave(): void {
  if (busy.value || !catalog.value) return
  formError.value = null
  if (!validateDraft()) return
  const diff = buildConfigDiff(fields.value, original.value, draft.value)
  if (!Object.keys(diff.values).length) {
    noChanges.value = true
    return
  }
  pendingDiff = diff
  restartPaths.value = diff.restartFields
  if (diff.restartFields.length) {
    confirmRestart.value = true
    return
  }
  void runSave(diff)
}

function requestRefresh(): void {
  if (dirtyCount.value > 0) {
    confirmRefresh.value = true
    return
  }
  void boot()
}

async function confirmReload(): Promise<void> {
  confirmRefresh.value = false
  saved.value = null
  formError.value = null
  noChanges.value = false
  await boot()
}

async function confirmSave(): Promise<void> {
  const diff = pendingDiff
  confirmRestart.value = false
  if (diff) await runSave(diff)
}

async function runSave(diff: ReturnType<typeof buildConfigDiff>): Promise<void> {
  if (!catalog.value || busy.value) return
  busy.value = true
  formError.value = null
  fieldErrors.value = {}
  try {
    const response = await api.configValues(diff.values)
    saved.value = response
    // The response already confirms the write; keep the page truthful even if
    // the follow-up catalog reload fails.
    original.value = cloneValue(draft.value)
    await Promise.all([catalogResource.load(), loadStatus()])
    if (!catalogError.value) hydrate()
  } catch (err) {
    handleError(err)
  } finally {
    busy.value = false
    pendingDiff = null
  }
}

function handleError(err: unknown): void {
  const copy = describeError(err)
  formError.value = copy
  const map: Record<string, string> = {}
  for (const field of copy.fields) {
    const path = normalizeFieldPath(field.path)
    map[path] = field.message || t('v2.config.fieldInvalid')
    if (path !== field.path) map[field.path] = map[path]
  }
  fieldErrors.value = map
  const first = copy.fields[0]?.path
  if (first) {
    void nextTick(() => {
      const selector = `[data-field-path="${first.replace(/["\\]/g, '\\$&')}"]`
      document.querySelector<HTMLElement>(selector)?.scrollIntoView({
        block: 'center',
        behavior: 'smooth',
      })
    })
  }
}

onMounted(() => {
  void boot()
})
</script>

<template>
  <div class="v2-page">
    <header class="v2-page-head">
      <div class="v2-page-title">
        <span class="kicker">{{ t('v2.nav.config') }}</span>
        <h1>{{ t('v2.tools.title') }}</h1>
        <p class="lede">{{ t('v2.tools.lede') }}</p>
      </div>
      <div class="v2-page-actions">
        <button
          type="button"
          class="btn btn-sm"
          :disabled="catalogLoading || mcpLoading"
          data-testid="tools-refresh"
          @click="requestRefresh"
        >
          <AppIcon name="refresh" :size="14" />
          {{ t('v2.common.refresh') }}
        </button>
      </div>
    </header>

    <StateBlock
      v-if="catalogLoading && !catalog"
      kind="loading"
      :title="t('v2.common.loading')"
    />

    <StateBlock
      v-else-if="catalogErrorCopy && !catalog"
      kind="error"
      :title="t('v2.tools.loadFailed')"
      :detail="catalogErrorCopy.detail"
      :hint="catalogErrorCopy.hint"
      :status="catalogErrorCopy.status"
      :request-id="catalogErrorCopy.requestId"
      show-retry
      @retry="boot()"
    />

    <template v-else-if="catalog">
      <div v-if="catalogErrorCopy" class="banner banner-warn v2-banner" role="status">
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.tools.loadFailed') }}</strong>
          <p class="note">{{ catalogErrorCopy.hint }}</p>
        </div>
        <button type="button" class="btn btn-sm" @click="boot()">
          {{ t('v2.common.retry') }}
        </button>
      </div>

      <div
        v-if="configErrorCopy"
        class="banner banner-warn v2-banner"
        role="status"
        data-testid="tools-config-fallback"
      >
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.tools.configFallback') }}</strong>
          <p class="note">{{ configErrorCopy.hint }}</p>
        </div>
      </div>

      <p v-if="fields.length" class="v2-catalog-note" data-testid="tools-catalog-note">
        {{ t('v2.tools.catalogNote') }}
        <span class="mono">· {{ t('v2.tools.restartCommand', { command: catalog.restart_command }) }}</span>
      </p>

      <div
        v-if="saved"
        class="banner v2-banner"
        :class="saved.restart_required ? 'banner-warn' : 'banner-ok'"
        role="status"
        data-testid="tools-save-result"
      >
        <AppIcon :name="saved.restart_required ? 'refresh' : 'check'" :size="17" />
        <div class="v2-banner-body">
          <strong>
            {{
              saved.restart_required
                ? t('v2.tools.savedRestart')
                : t('v2.tools.savedApplied', { n: saved.applied.length })
            }}
          </strong>
          <ul v-if="saved.restart_fields.length" class="v2-field-list">
            <li v-for="path in saved.restart_fields" :key="path">
              <span class="mono">{{ path }}</span>
            </li>
          </ul>
          <ul v-if="saved.warnings.length" class="v2-field-list">
            <li v-for="warning in saved.warnings" :key="warning">{{ warning }}</li>
          </ul>
        </div>
        <button type="button" class="btn btn-sm" @click="saved = null">
          {{ t('v2.common.close') }}
        </button>
      </div>

      <ErrorNotice :error="formError" data-testid="tools-form-error" @retry="requestSave" />

      <section
        v-if="configDoc"
        class="v2-config-meta"
        :aria-label="t('v2.tools.metaAria')"
      >
        <span class="meta">
          {{ t('v2.tools.configVersion', { n: configVersion ?? '—' }) }}
        </span>
        <span v-if="dirtyCount" class="meta" data-testid="tools-dirty-count">
          {{ t('v2.config.unsavedCount', { n: dirtyCount }) }}
        </span>
      </section>

      <p v-if="!fields.length" class="v2-card note" data-testid="tools-empty">
        {{ t('v2.tools.empty') }}
      </p>

      <section
        v-for="entry in sections"
        :key="entry.key"
        class="v2-tools-section"
        :data-section="entry.key"
        :data-testid="`tools-section-${entry.key}`"
        :aria-labelledby="`v2-tools-section-${entry.key}`"
      >
        <header class="v2-tools-section-head">
          <h2 :id="`v2-tools-section-${entry.key}`" class="v2-tools-section-title">
            {{ t(SECTION_LABELS[entry.key]) }}
          </h2>
          <span class="meta">{{ t('v2.common.items', { n: sectionCount(entry.key) }) }}</span>
        </header>
        <ConfigGroupCard
          v-for="group in entry.groups"
          :key="group.group.id"
          :group="group.group"
          :fields="group.fields"
          :draft="draft"
          :errors="fieldErrors"
          :changed="changedPaths"
          @update="setField"
        />
      </section>

      <section v-if="mcpStatus || mcpErrorCopy" class="v2-card" data-testid="tools-mcp-status">
        <header class="v2-card-head">
          <h2 class="v2-card-title">{{ t('v2.tools.status.title') }}</h2>
          <span v-if="statusCheckedLabel" class="meta" data-testid="tools-status-checked">
            {{ t('v2.tools.status.checkedAt', { time: statusCheckedLabel }) }}
          </span>
          <span v-if="genericSummary" class="meta">{{ genericSummary }}</span>
        </header>
        <p class="v2-card-lede">{{ t('v2.tools.status.lede') }}</p>

        <p v-if="mcpErrorCopy" class="note" data-testid="tools-status-error">
          {{ t('v2.tools.status.loadFailed') }}
          <span v-if="mcpErrorCopy.detail" class="mono"> · {{ mcpErrorCopy.detail }}</span>
        </p>
        <template v-else-if="mcpStatus">
          <p v-if="!statusRows.length" class="note">{{ t('v2.tools.status.empty') }}</p>
          <div v-else class="table-wrap">
            <table class="table v2-tools-status-table">
              <thead>
                <tr>
                  <th>{{ t('v2.tools.status.name') }}</th>
                  <th>{{ t('v2.tools.status.state') }}</th>
                  <th>{{ t('v2.tools.status.detail') }}</th>
                  <th>{{ t('v2.tools.status.lastChecked') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in statusRows"
                  :key="row.key"
                  data-testid="tools-status-row"
                  :data-state="row.state"
                >
                  <td>
                    <strong>{{ row.name }}</strong>
                    <span class="meta"> · {{ row.kind }}</span>
                  </td>
                  <td>
                    <span class="badge" :class="STATUS_BADGES[row.state]">
                      {{ t(STATUS_LABELS[row.state]) }}
                    </span>
                  </td>
                  <td>
                    <span v-if="row.error" class="mono v2-break">{{ row.error }}</span>
                    <span v-else>{{ row.details.join(' · ') || t('v2.common.na') }}</span>
                  </td>
                  <td class="mono">
                    {{ row.lastChecked || t('v2.common.na') }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
      </section>

      <div class="v2-save-bar" data-testid="config-save-bar">
        <p class="v2-save-state note" role="status">
          <template v-if="busy">{{ t('v2.common.saving') }}</template>
          <template v-else-if="noChanges">{{ t('v2.config.noChanges') }}</template>
          <template v-else-if="dirtyCount">
            {{ t('v2.config.unsaved', { n: dirtyCount }) }}
            <template v-if="restartCount">
              · {{ t('v2.config.restartCount', { n: restartCount }) }}
            </template>
          </template>
          <template v-else>{{ t('v2.config.clean') }}</template>
        </p>
        <button
          type="button"
          class="btn"
          :disabled="!dirtyCount || busy"
          data-testid="config-reset"
          @click="resetDraft"
        >
          {{ t('v2.config.reset') }}
        </button>
        <button
          type="button"
          class="btn btn-primary"
          :disabled="!dirtyCount || busy"
          data-testid="config-save"
          @click="requestSave"
        >
          <AppIcon :name="busy ? 'clock' : 'check'" :size="14" />
          {{ busy ? t('v2.common.saving') : t('v2.common.save') }}
        </button>
      </div>

      <nav class="v2-config-links" :aria-label="t('v2.config.otherPages')">
        <span class="meta">{{ t('v2.config.otherPages') }}</span>
        <RouterLink class="btn btn-sm" to="/voice-assistant">
          {{ t('v2.nav.voiceAssistant') }}
        </RouterLink>
        <RouterLink class="btn btn-sm" to="/music">{{ t('v2.nav.music') }}</RouterLink>
        <RouterLink class="btn btn-sm" to="/credentials">{{ t('v2.nav.credentials') }}</RouterLink>
        <RouterLink class="btn btn-sm" to="/health">{{ t('v2.nav.health') }}</RouterLink>
      </nav>
    </template>

    <ConfirmDialog
      v-if="confirmRestart"
      :title="t('v2.config.restartTitle')"
      :confirm-label="t('v2.config.restartConfirm')"
      :cancel-label="t('v2.common.cancel')"
      :busy="busy"
      :busy-label="t('v2.common.saving')"
      data-testid="config-restart-dialog"
      @confirm="confirmSave"
      @cancel="confirmRestart = false"
    >
      <p class="v2-confirm-lead">{{ t('v2.config.restartBody', { n: restartPaths.length }) }}</p>
      <ul class="v2-confirm-changes">
        <li v-for="(label, index) in restartLabels" :key="restartPaths[index]">
          {{ label }}
          <span class="mono meta">{{ restartPaths[index] }}</span>
        </li>
      </ul>
      <p class="note">{{ t('v2.config.restartHint') }}</p>
    </ConfirmDialog>

    <ConfirmDialog
      v-if="confirmRefresh"
      :title="t('v2.config.refreshDirtyTitle')"
      :confirm-label="t('v2.config.refreshDirtyConfirm')"
      :cancel-label="t('v2.common.cancel')"
      :busy="catalogLoading"
      :busy-label="t('v2.common.loading')"
      data-testid="config-refresh-dialog"
      @confirm="confirmReload"
      @cancel="confirmRefresh = false"
    >
      <p class="v2-confirm-lead">
        {{ t('v2.config.refreshDirtyBody', { n: dirtyCount }) }}
      </p>
      <p class="note">{{ t('v2.config.refreshDirtyHint') }}</p>
    </ConfirmDialog>
  </div>
</template>
