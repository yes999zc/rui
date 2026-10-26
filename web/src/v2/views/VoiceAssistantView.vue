<script setup lang="ts">
/**
 * `/v2/voice-assistant` (M9a) — catalog-driven configuration for the assistant,
 * sessions and the audio path.
 *
 * Data sources (C-002 §1.2 transitional v1 endpoints):
 *  - `GET /api/v1/config/catalog` — groups, field order, kinds/ranges/options,
 *    current values and each field's `effect`;
 *  - `GET /api/v1/config` — redacted effective config, used only as a value
 *    fallback when a catalog entry has no value.
 *
 * Saving sends a field-level diff to `PUT /api/v1/config/values`: exactly the
 * dotted paths the operator changed. `effect=restart` fields open a confirmation
 * first and the response's `restart_fields` stays visible afterwards. A 422 is
 * mapped back to `details.fields[].path` (FastAPI `detail[].loc` is normalized in
 * `api.ts`) and marks that row red.
 *
 * Device fields (wake words, volume, ...) are read-only here and link to the
 * device page; editing them belongs to the device editor and restarts the plane.
 */
import { computed, nextTick, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import ErrorNotice from '../components/device/ErrorNotice.vue'
import StateBlock from '../components/StateBlock.vue'
import ConfigGroupCard from '../components/config/ConfigGroupCard.vue'
import DeviceConfigCard from '../components/config/DeviceConfigCard.vue'
import {
  api,
  type ConfigCatalog,
  type ConfigDocument,
  type ConfigFieldSpec,
  type ConfigValuesResponse,
} from '../api'
import {
  buildConfigDiff,
  cloneValue,
  initialDraftValue,
  isBlankNumber,
  orderedGroups,
  valueFor,
  valuesEqual,
} from '../config'
import { describeError, normalizeFieldPath, type ErrorCopy } from '../errors'
import { pickLocalized, t } from '../i18n'
import { useResource } from '../useResource'

const catalogResource = useResource(() => api.configCatalog())
const configResource = useResource(() => api.config())
const { data: catalog, error: catalogError, loading: catalogLoading } = catalogResource
const { data: configDoc, error: configError } = configResource

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
let pendingDiff: ReturnType<typeof buildConfigDiff> | null = null

const catalogErrorCopy = computed(() =>
  catalogError.value ? describeError(catalogError.value) : null,
)
const configErrorCopy = computed(() =>
  configError.value ? describeError(configError.value) : null,
)

/** Groups shown on this page; other config domains have their own pages. */
const SHOWN_GROUPS = new Set([
  'assistant',
  'session',
  'audio.capture',
  'audio.voice',
  'audio.playback',
  'audio.aec',
  'ack',
])

function showGroup(id: string): boolean {
  // Fixed domains plus any future `audio.*` group: the catalog, not this page,
  // decides how the voice path is split.
  return SHOWN_GROUPS.has(id) || id === 'devices' || id.startsWith('audio.')
}

const groups = computed(() =>
  catalog.value ? orderedGroups(catalog.value, (group) => showGroup(group.id)) : [],
)

const editableFields = computed<ConfigFieldSpec[]>(() => {
  if (!catalog.value) return []
  return catalog.value.fields.filter((field) => showGroup(field.group) && field.group !== 'devices')
})

const deviceRows = computed<Array<Record<string, unknown>>>(() => {
  const fromCatalog = catalog.value?.values?.devices
  if (Array.isArray(fromCatalog)) return fromCatalog as Array<Record<string, unknown>>
  const fallback = configDoc.value?.devices
  return Array.isArray(fallback) ? (fallback as Array<Record<string, unknown>>) : []
})

const changedPaths = computed<Set<string>>(() => {
  const set = new Set<string>()
  for (const field of editableFields.value) {
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
  for (const field of editableFields.value) {
    if (field.effect === 'restart' && changedPaths.value.has(field.path)) count += 1
  }
  return count
})

const restartLabels = computed(() => {
  const labels: string[] = []
  if (!catalog.value) return labels
  for (const path of restartPaths.value) {
    const field = catalog.value.fields.find((item) => item.path === path)
    labels.push(field ? pickLocalized(field.label) : path)
  }
  return labels
})

function hydrate(): void {
  if (!catalog.value) return
  const next: Record<string, unknown> = {}
  for (const field of catalog.value.fields) {
    next[field.path] = initialDraftValue(
      field,
      valueFor(catalog.value, configDoc.value, field.path),
    )
  }
  original.value = cloneValue(next)
  draft.value = cloneValue(next)
  fieldErrors.value = {}
}

async function boot(): Promise<void> {
  await Promise.all([catalogResource.load(), configResource.load()])
  hydrate()
}

function setField(path: string, value: unknown): void {
  draft.value = { ...draft.value, [path]: value }
  const next = { ...fieldErrors.value }
  delete next[path]
  const field = editableFields.value.find((item) => item.path === path)
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
  for (const field of editableFields.value) {
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
  const diff = buildConfigDiff(editableFields.value, original.value, draft.value)
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
  // Refreshing re-reads the server and hydrates the draft; confirm before
  // discarding edits instead of silently losing them.
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
    await catalogResource.load()
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
        <h1>{{ t('v2.voice.title') }}</h1>
        <p class="lede">{{ t('v2.voice.lede') }}</p>
      </div>
      <div class="v2-page-actions">
        <button
          type="button"
          class="btn btn-sm"
          :disabled="catalogLoading"
          data-testid="voice-refresh"
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
      :title="t('v2.voice.loadFailed')"
      :detail="catalogErrorCopy.detail"
      :hint="catalogErrorCopy.hint"
      :status="catalogErrorCopy.status"
      :request-id="catalogErrorCopy.requestId"
      show-retry
      @retry="boot()"
    />

    <template v-else-if="catalog">
      <div
        v-if="catalogErrorCopy"
        class="banner banner-warn v2-banner"
        role="status"
      >
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.voice.loadFailed') }}</strong>
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
        data-testid="voice-config-fallback"
      >
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.voice.configFallback') }}</strong>
          <p class="note">{{ configErrorCopy.hint }}</p>
        </div>
      </div>

      <p class="v2-catalog-note" data-testid="voice-catalog-note">
        {{ t('v2.voice.catalogNote') }}
        <span class="mono">· {{ t('v2.voice.restartCommand', { command: catalog.restart_command }) }}</span>
      </p>

      <div
        v-if="saved"
        class="banner v2-banner"
        :class="saved.restart_required ? 'banner-warn' : 'banner-ok'"
        role="status"
        data-testid="voice-save-result"
      >
        <AppIcon :name="saved.restart_required ? 'refresh' : 'check'" :size="17" />
        <div class="v2-banner-body">
          <strong>
            {{
              saved.restart_required
                ? t('v2.voice.savedRestart')
                : t('v2.voice.savedApplied', { n: saved.applied.length })
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

      <ErrorNotice
        :error="formError"
        data-testid="voice-form-error"
        @retry="requestSave"
      />

      <section
        v-if="configDoc"
        class="v2-config-meta"
        :aria-label="t('v2.voice.metaAria')"
      >
        <span class="meta">
          {{ t('v2.voice.configVersion', { n: configVersion ?? '—' }) }}
        </span>
        <span v-if="dirtyCount" class="meta" data-testid="voice-dirty-count">
          {{ t('v2.config.unsavedCount', { n: dirtyCount }) }}
        </span>
      </section>

      <template v-for="entry in groups" :key="entry.group.id">
        <DeviceConfigCard
          v-if="entry.group.id === 'devices'"
          :group="entry.group"
          :devices="deviceRows"
        />
        <ConfigGroupCard
          v-else
          :group="entry.group"
          :fields="entry.fields"
          :draft="draft"
          :errors="fieldErrors"
          :changed="changedPaths"
          @update="setField"
        />
      </template>

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
        <RouterLink class="btn btn-sm" to="/music">{{ t('v2.nav.music') }}</RouterLink>
        <RouterLink class="btn btn-sm" to="/credentials">{{ t('v2.nav.credentials') }}</RouterLink>
        <RouterLink class="btn btn-sm" to="/health">{{ t('v2.nav.health') }}</RouterLink>
        <RouterLink class="btn btn-sm" to="/rooms">{{ t('v2.nav.rooms') }}</RouterLink>
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
