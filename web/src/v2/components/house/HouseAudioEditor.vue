<script setup lang="ts">
/**
 * Global audio defaults editor (M5, contract C-002 §7.4–7.6).
 *
 * - shows the global block plus the server's `explicit` set; value equality is
 *   never used to infer explicitness;
 * - a save sends a field-level diff; `null` clears one explicit leaf so it falls
 *   back to the core default / plug manifest;
 * - saving never restarts the audio plane — the page states that and offers the
 *   explicit `POST /house/audio/restart` button afterwards;
 * - 422 paths (`audio.capture.channels`, `ack.echo_ignore_ms`, …) highlight the
 *   matching row; unmapped paths stay in the top error banner;
 * - a 429 disables both write actions for the advertised cooldown.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../../components/AppIcon.vue'
import {
  api,
  type HouseAudioInfo,
  type PatchHouseAudioRequest,
  type Restart,
} from '../../api'
import { describeError, apiErrorCode, apiRetryAfterSeconds, type ErrorCopy } from '../../errors'
import { displayValue } from '../../format'
import {
  buildHouseAudioRows,
  houseAudioGroupLabelKey,
  houseAudioGroupOrder,
  houseAudioSpec,
  setHouseAudioLeaf,
  sortHouseAudioRows,
  type HouseAudioRow,
} from '../../houseAudio'
import { t, type MsgKey } from '../../i18n'
import { useCooldown } from '../../useCooldown'
import ConfirmDialog from '../ConfirmDialog.vue'
import StateBlock from '../StateBlock.vue'
import ErrorNotice from '../device/ErrorNotice.vue'
import RestartNotice from '../device/RestartNotice.vue'
import HouseAudioField from './HouseAudioField.vue'

const props = withDefaults(
  defineProps<{
    data: HouseAudioInfo | null
    loading: boolean
    errorCopy: ErrorCopy | null
    writeEnabled: boolean
    overrideDevices?: Array<{ id: string; count: number }>
    overrideUnreadable?: number
    overrideUnavailable?: boolean
  }>(),
  {
    overrideDevices: () => [],
    overrideUnreadable: 0,
    overrideUnavailable: false,
  },
)

const emit = defineEmits<{
  retry: []
  updated: [data: HouseAudioInfo]
  restarted: []
  'dirty-change': [dirty: boolean]
}>()

interface ChangeView {
  path: string
  from: string
  to: string
}

interface GroupView {
  id: string
  labelKey: MsgKey | null
  rows: HouseAudioRow[]
  advanced: HouseAudioRow[]
}

const baseline = ref<Record<string, unknown>>({})
const drafts = ref<Record<string, string | null>>({})
const touched = ref<Record<string, boolean>>({})
const submitted = ref(false)
const serverFields = ref<Record<string, string>>({})

const saving = ref(false)
const restarting = ref(false)
const confirmOpen = ref(false)
const saved = ref(false)
const restartDone = ref(false)
const saveError = ref<ErrorCopy | null>(null)
const restartError = ref<ErrorCopy | null>(null)
const lastRestart = ref<Restart | null>(null)

const {
  seconds: cooldownSeconds,
  active: cooldownActive,
  start: startCooldown,
} = useCooldown()

const busy = computed(() => saving.value || restarting.value)
const canEdit = computed(() => props.writeEnabled && !busy.value && !cooldownActive.value)
const rows = computed(() => sortHouseAudioRows(buildHouseAudioRows(props.data)))

const groupViews = computed<GroupView[]>(() => {
  const map = new Map<string, HouseAudioRow[]>()
  for (const row of rows.value) {
    const list = map.get(row.group) ?? []
    list.push(row)
    map.set(row.group, list)
  }
  return [...map.entries()]
    .sort(([a], [b]) => {
      const byOrder = houseAudioGroupOrder(a) - houseAudioGroupOrder(b)
      return byOrder !== 0 ? byOrder : a.localeCompare(b)
    })
    .map(([id, list]) => ({
      id,
      labelKey: houseAudioGroupLabelKey(id),
      rows: list.filter((row) => !row.spec?.advanced),
      advanced: list.filter((row) => row.spec?.advanced),
    }))
})

function groupLabel(group: GroupView): string {
  return group.labelKey ? t(group.labelKey) : group.id
}

let synced = false

function syncFrom(next: HouseAudioInfo): void {
  const map: Record<string, unknown> = {}
  for (const row of buildHouseAudioRows(next)) map[row.path] = row.value
  baseline.value = map
  drafts.value = {}
  touched.value = {}
  submitted.value = false
  serverFields.value = {}
}

watch(
  () => props.data,
  (next) => {
    if (!next) return
    if (synced && (busy.value || dirty.value)) return
    syncFrom(next)
    synced = true
  },
  { immediate: true },
)

function hasDraft(path: string): boolean {
  return Object.prototype.hasOwnProperty.call(drafts.value, path)
}

function draftFor(row: HouseAudioRow): string | null | undefined {
  return hasDraft(row.path) ? drafts.value[row.path] : undefined
}

function baselineFor(row: HouseAudioRow): string {
  const value = baseline.value[row.path]
  return value === null || value === undefined ? '' : String(value)
}

function controlValue(row: HouseAudioRow): string {
  const draft = draftFor(row)
  if (draft === undefined) return baselineFor(row)
  return draft === null ? '' : draft
}

function isCleared(row: HouseAudioRow): boolean {
  return hasDraft(row.path) && drafts.value[row.path] === null
}

function isDirty(row: HouseAudioRow): boolean {
  if (!row.editable) return false
  if (!hasDraft(row.path)) return false
  const draft = drafts.value[row.path]
  if (draft === null) return row.explicit
  return draft !== baselineFor(row)
}

const dirtyRows = computed(() => rows.value.filter(isDirty))
const dirty = computed(() => dirtyRows.value.length > 0)

// The page owns route-leave/unload guards; the editor only reports its draft.
watch(dirty, (value) => emit('dirty-change', value))
onBeforeUnmount(() => emit('dirty-change', false))

function setDraft(row: HouseAudioRow, event: Event): void {
  if (!canEdit.value || !row.editable) return
  const raw = (event.target as HTMLInputElement | HTMLSelectElement).value
  const next = { ...drafts.value }
  if (raw === baselineFor(row)) delete next[row.path]
  else next[row.path] = raw
  drafts.value = next
  touched.value = { ...touched.value, [row.path]: true }
  if (serverFields.value[row.path]) {
    const fields = { ...serverFields.value }
    delete fields[row.path]
    serverFields.value = fields
  }
  saved.value = false
}

function clearRow(row: HouseAudioRow): void {
  if (!canEdit.value || !row.editable || !row.explicit) return
  drafts.value = { ...drafts.value, [row.path]: null }
  touched.value = { ...touched.value, [row.path]: true }
  saved.value = false
}

function undoClear(row: HouseAudioRow): void {
  if (!canEdit.value) return
  const next = { ...drafts.value }
  delete next[row.path]
  drafts.value = next
  saved.value = false
}

function canClear(row: HouseAudioRow): boolean {
  return canEdit.value && row.editable && row.explicit && !isCleared(row)
}

function candidateValue(row: HouseAudioRow): unknown {
  const draft = draftFor(row)
  if (draft === undefined) return undefined
  if (draft === null) return null
  const spec = row.spec
  if (!spec) return undefined
  if (spec.control === 'number') {
    const value = Number(draft.trim())
    return Number.isFinite(value) ? value : undefined
  }
  if (spec.control === 'boolean') return draft === 'true'
  if (spec.control === 'enum') {
    return spec.options?.find((option) => String(option.value) === draft)?.value
  }
  return draft
}

function validationFor(row: HouseAudioRow): string {
  if (!row.editable || !row.spec || !isDirty(row)) return ''
  const draft = draftFor(row)
  if (draft === undefined || draft === null) return ''
  const spec = row.spec
  const raw = draft.trim()
  if (spec.control === 'number') {
    if (!/^-?\d+$/.test(raw)) return t('v2.houseAudio.validation.number')
    const value = Number(raw)
    if (
      (spec.min !== undefined && value < spec.min) ||
      (spec.max !== undefined && value > spec.max)
    ) {
      return t('v2.houseAudio.validation.range', {
        min: spec.min ?? '—',
        max: spec.max ?? '—',
      })
    }
  } else if (spec.control === 'boolean') {
    if (raw !== 'true' && raw !== 'false') return t('v2.houseAudio.validation.enum')
  } else if (spec.control === 'enum') {
    if (!spec.options?.some((option) => String(option.value) === raw)) {
      return t('v2.houseAudio.validation.enum')
    }
  }
  return ''
}

function visibleError(row: HouseAudioRow): string {
  if (serverFields.value[row.path]) return serverFields.value[row.path]
  if (!submitted.value && !touched.value[row.path]) return ''
  return validationFor(row)
}

const validationErrors = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {}
  for (const row of rows.value) {
    const error = validationFor(row)
    if (error) map[row.path] = error
  }
  return map
})

/**
 * `displayValue('')` renders `v2.common.na`; a text leaf cleared by the
 * operator is a real empty string, so show the dedicated empty-value copy
 * instead of "n/a → n/a" in the confirmation.
 */
function changeValue(value: unknown): string {
  if (value === '') return t('v2.house.emptyValue')
  return displayValue(value)
}

const changes = computed<ChangeView[]>(() =>
  dirtyRows.value.map((row) => ({
    path: row.path,
    from: row.explicit
      ? changeValue(row.value)
      : `${t('v2.houseAudio.implicit')} · ${changeValue(row.value)}`,
    to:
      draftFor(row) === null
        ? t('v2.houseAudio.clearToDefault')
        : changeValue(candidateValue(row)),
  })),
)

function buildPatch(): PatchHouseAudioRequest | null {
  const body: PatchHouseAudioRequest = {}
  for (const row of dirtyRows.value) {
    if (!row.spec) continue
    const value = candidateValue(row)
    if (value === undefined) return null
    setHouseAudioLeaf(body, row.path, value)
  }
  if (!body.audio && !body.ack) return null
  return body
}

function resetErrors(): void {
  saveError.value = null
  restartError.value = null
  serverFields.value = {}
}

function requestSave(): void {
  if (!canEdit.value) return
  submitted.value = true
  resetErrors()
  if (!dirty.value) return
  if (Object.keys(validationErrors.value).length > 0) return
  if (!buildPatch()) return
  confirmOpen.value = true
}

function closeConfirm(): void {
  if (!saving.value) confirmOpen.value = false
}

function mapServerPath(path: string): string {
  let next = path.trim()
  next = next.replace(/^devices\[\d+\]\.audio\./, 'audio.')
  next = next.replace(/^devices\.\d+\.audio\./, 'audio.')
  if (houseAudioSpec(next)) return next
  return ''
}

function applyServerFields(err: unknown): void {
  const copy = saveError.value ?? describeError(err)
  const map: Record<string, string> = {}
  for (const field of copy.fields) {
    const path = mapServerPath(field.path)
    if (!path) continue
    map[path] = field.message || t('v2.house.invalidField')
  }
  serverFields.value = map
  // Mapped leaves are highlighted in their row; only unmapped paths remain in
  // the top banner with the exact backend path.
  saveError.value = {
    ...copy,
    fields: copy.fields.filter((field) => !mapServerPath(field.path)),
  }
}

async function save(): Promise<void> {
  if (busy.value) return
  const patch = buildPatch()
  if (!patch) {
    confirmOpen.value = false
    return
  }
  saving.value = true
  resetErrors()
  try {
    const response = await api.patchHouseAudio(patch)
    lastRestart.value = response.restart
    saved.value = true
    restartDone.value = false
    confirmOpen.value = false
    syncFrom(response)
    emit('updated', response)
  } catch (err) {
    saveError.value = describeError(err)
    applyServerFields(err)
    confirmOpen.value = false
    if (apiErrorCode(err) === 'rate_limited' || saveError.value.status === 429) {
      startCooldown(apiRetryAfterSeconds(err) ?? 10)
    }
  } finally {
    saving.value = false
  }
}

async function restartAudio(): Promise<void> {
  if (busy.value || !canEdit.value) return
  restarting.value = true
  restartError.value = null
  try {
    const response = await api.restartHouseAudio()
    lastRestart.value = response.restart
    restartDone.value = true
    emit('restarted')
  } catch (err) {
    restartError.value = describeError(err)
    if (apiErrorCode(err) === 'rate_limited' || restartError.value.status === 429) {
      startCooldown(apiRetryAfterSeconds(err) ?? 10)
    }
  } finally {
    restarting.value = false
  }
}

function discard(): void {
  if (busy.value || !props.data) return
  syncFrom(props.data)
  saved.value = false
  restartDone.value = false
  lastRestart.value = null
  resetErrors()
  submitted.value = false
}

const overrides = computed(() => props.overrideDevices ?? [])
// Feature-off is "unknown", not "every device failed": the page sent no request.
const overrideCountLabel = computed(() => {
  if (props.overrideUnavailable) return t('v2.houseAudio.overridesUnavailable')
  const failed = props.overrideUnreadable ?? 0
  if (failed > 0) {
    return t('v2.houseAudio.overridesPartial', {
      failed,
      known: overrides.value.length,
    })
  }
  return overrides.value.length
    ? t('v2.houseAudio.overridesCount', { n: overrides.value.length })
    : t('v2.houseAudio.overridesNone')
})
const restartSucceeded = computed(
  () => restartDone.value && lastRestart.value?.status === 'ok',
)
</script>

<template>
  <section class="v2-card v2-house-audio" data-testid="house-audio-editor">
    <div class="v2-card-head">
      <div>
        <h2 class="v2-card-title">{{ t('v2.houseAudio.title') }}</h2>
        <p class="v2-card-lede">{{ t('v2.houseAudio.lede') }}</p>
      </div>
      <span
        class="badge"
        :class="dirty ? 'badge-warn' : 'badge-muted'"
        data-testid="house-audio-dirty"
      >
        {{ dirty ? t('v2.houseAudio.dirty', { n: dirtyRows.length }) : t('v2.houseAudio.clean') }}
      </span>
    </div>

    <div class="v2-audio-topnote" role="note">
      <AppIcon name="layers" :size="18" />
      <p class="note">{{ t('v2.houseAudio.manifestWarning') }}</p>
    </div>
    <p class="note v2-house-audio-legend">{{ t('v2.houseAudio.explicitLegend') }}</p>

    <StateBlock
      v-if="loading && !data"
      kind="loading"
      :title="t('v2.common.loading')"
    />
    <StateBlock
      v-else-if="errorCopy && !data"
      kind="error"
      :title="t('v2.houseAudio.loadFailed')"
      :detail="errorCopy.detail"
      :hint="errorCopy.hint || t('v2.houseAudio.unavailableHint')"
      :status="errorCopy.status"
      :request-id="errorCopy.requestId"
      :fields="errorCopy.fields"
      show-retry
      @retry="emit('retry')"
    />
    <StateBlock
      v-else-if="!data"
      kind="unavailable"
      :title="t(writeEnabled ? 'v2.houseAudio.unavailable' : 'v2.houseAudio.featureOff')"
      :hint="t(writeEnabled ? 'v2.houseAudio.unavailableHint' : 'v2.houseAudio.featureOffHint')"
    />

    <template v-else>
      <ErrorNotice v-if="errorCopy" :error="errorCopy" show-retry @retry="emit('retry')" />

      <section class="v2-house-audio-overrides" data-testid="audio-overrides">
        <h3>{{ t('v2.houseAudio.overridesTitle') }}</h3>
        <p
          class="v2-house-audio-override-count"
          data-testid="override-count"
        >
          {{ overrideCountLabel }}
        </p>
        <div v-if="overrides.length" class="chips">
          <RouterLink
            v-for="entry in overrides"
            :key="entry.id"
            class="chip mono"
            :to="`/devices/${entry.id}`"
            data-testid="override-device"
          >
            {{ t('v2.house.overrideChip', { id: entry.id, n: entry.count }) }}
          </RouterLink>
        </div>
        <p class="note">{{ t('v2.houseAudio.overridesNote') }}</p>
      </section>

      <div class="v2-audio-toolbar">
        <p class="note v2-audio-toolbar-note">{{ t('v2.houseAudio.diffHint') }}</p>
        <button type="button" class="btn" :disabled="busy || !dirty" @click="discard">
          {{ t('v2.houseAudio.discard') }}
        </button>
        <button
          type="button"
          class="btn btn-primary"
          data-testid="house-audio-save"
          :disabled="!canEdit || !dirty"
          @click="requestSave"
        >
          <AppIcon name="check" :size="15" />
          {{ saving ? t('v2.houseAudio.saving') : t('v2.houseAudio.save') }}
        </button>
      </div>

      <p v-if="cooldownActive" class="note v2-cooldown" role="status" data-testid="house-audio-cooldown">
        {{ t('v2.house.cooldown', { n: cooldownSeconds }) }}
      </p>

      <p v-if="!rows.length" class="empty">{{ t('v2.houseAudio.noRows') }}</p>

      <section v-for="group in groupViews" :key="group.id" class="v2-house-audio-group">
        <div class="v2-house-audio-group-head">
          <h3>{{ groupLabel(group) }}</h3>
          <span class="meta">{{ t('v2.common.items', { n: group.rows.length + group.advanced.length }) }}</span>
        </div>
        <div class="v2-house-audio-rows">
          <HouseAudioField
            v-for="row in group.rows"
            :key="row.path"
            :row="row"
            :control-value="controlValue(row)"
            :error="visibleError(row)"
            :cleared="isCleared(row)"
            :dirty="isDirty(row)"
            :can-clear="canClear(row)"
            :can-edit="canEdit && row.editable"
            @input="setDraft(row, $event)"
            @clear="clearRow(row)"
            @undo="undoClear(row)"
          />
        </div>
        <details v-if="group.advanced.length" class="v2-house-audio-advanced">
          <summary>{{ t('v2.houseAudio.advanced', { n: group.advanced.length }) }}</summary>
          <div class="v2-house-audio-rows">
            <HouseAudioField
              v-for="row in group.advanced"
              :key="row.path"
              :row="row"
              :control-value="controlValue(row)"
              :error="visibleError(row)"
              :cleared="isCleared(row)"
              :dirty="isDirty(row)"
              :can-clear="canClear(row)"
              :can-edit="canEdit && row.editable"
              @input="setDraft(row, $event)"
              @clear="clearRow(row)"
              @undo="undoClear(row)"
            />
          </div>
        </details>
      </section>

      <div v-if="saved" class="v2-house-audio-saved" data-testid="house-audio-saved">
        <div class="banner banner-ok v2-banner" role="status">
          <AppIcon name="check" :size="18" />
          <div class="v2-banner-body">
            <strong>{{ t('v2.houseAudio.saved') }}</strong>
            <p class="note">{{ restartSucceeded ? t('v2.houseAudio.restartApplied') : t('v2.houseAudio.savedRestartPending') }}</p>
            <p v-if="overrides.length" class="note v2-warn-text" data-testid="audio-saved-overrides">
              {{ t('v2.houseAudio.savedWithOverrides', { n: overrides.length }) }}
            </p>
          </div>
        </div>

        <template v-if="lastRestart">
          <p class="meta">{{ restartDone ? t('v2.houseAudio.restartResult') : t('v2.houseAudio.savedRestartPending') }}</p>
          <RestartNotice :restart="lastRestart" :show-retry="false" />
        </template>

        <button
          type="button"
          class="btn"
          data-testid="house-audio-restart"
          :disabled="busy || cooldownActive"
          @click="restartAudio"
        >
          <AppIcon name="refresh" :size="14" />
          {{ restarting ? t('v2.houseAudio.restarting') : t('v2.houseAudio.restartNow') }}
        </button>
        <ErrorNotice v-if="restartError" :error="restartError" />
      </div>
    </template>

    <ConfirmDialog
      v-if="confirmOpen"
      :title="t('v2.houseAudio.confirmTitle')"
      :confirm-label="t('v2.houseAudio.confirmAction')"
      :cancel-label="t('v2.houseAudio.confirmCancel')"
      :busy="saving"
      :busy-label="t('v2.houseAudio.saving')"
      @confirm="save"
      @cancel="closeConfirm"
    >
      <p class="note v2-confirm-lead">{{ t('v2.houseAudio.confirmBody') }}</p>
      <dl class="v2-kv v2-confirm-kv">
        <div>
          <dt>{{ t('v2.houseAudio.confirmFields') }}</dt>
          <dd class="mono">{{ changes.map((change) => change.path).join(', ') }}</dd>
        </div>
      </dl>
      <ul class="v2-confirm-changes" data-testid="house-audio-changes">
        <li v-for="change in changes" :key="change.path">
          <span class="mono">{{ change.path }}</span>
          <span class="v2-audio-change">
            {{ change.from }} → {{ change.to }}
          </span>
        </li>
      </ul>
    </ConfirmDialog>
  </section>
</template>

<style scoped>
.v2-house-audio-legend {
  margin: var(--s2) 0 var(--s3);
}

.v2-house-audio-overrides {
  margin: var(--s3) 0 var(--s4);
  padding: var(--s3);
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-sm);
  background: var(--surface-2);
}

.v2-house-audio-overrides h3 {
  margin: 0 0 4px;
  font-size: 12px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--ink-2);
}

.v2-house-audio-override-count {
  margin: 0 0 var(--s2);
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink);
}

.v2-house-audio-overrides .note {
  margin: 4px 0 0;
}

.v2-house-audio-group {
  margin-top: var(--s4);
  padding-top: var(--s3);
  border-top: 1px solid var(--line);
}

.v2-house-audio-group-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--s3);
  margin-bottom: var(--s1);
}

.v2-house-audio-group-head h3 {
  margin: 0;
  color: var(--ink-2);
  font-family: var(--font-ui);
  font-size: 11px;
  font-weight: 650;
  letter-spacing: 0.07em;
  text-transform: uppercase;
}

.v2-house-audio-rows {
  display: flex;
  flex-direction: column;
}

.v2-house-audio-advanced {
  margin-top: var(--s2);
}

.v2-house-audio-advanced summary {
  cursor: pointer;
  color: var(--accent);
  font-size: 12px;
  padding: 4px 0;
}

.v2-house-audio-saved {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--s2);
  margin-top: var(--s4);
  padding-top: var(--s3);
  border-top: 1px solid var(--line);
}

.v2-house-audio-saved .note {
  margin: 0;
}
</style>
