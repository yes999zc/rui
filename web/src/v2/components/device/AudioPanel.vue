<script setup lang="ts">
/**
 * Device audio tab (M4): effective value + provenance per leaf, with per-device
 * override editing.
 *
 * Rules this component enforces:
 *  - every leaf keeps its `source` badge, and the four raw layers stay one click
 *    away; inheritance is never inferred from equal values (contract §7.15);
 *  - only leaves that the C-001 `DeviceAudioOverride` schema can store are
 *    editable — the rest are shown read-only rather than silently rejected;
 *  - a save sends a field-level diff (only edited leaves) and always asks for a
 *    second confirmation that states the whole-house fleet restart;
 *  - restoring inheritance sends `null` for exactly one leaf, then **re-reads**
 *    `/audio` and verifies both the source and the `overrides` list. A failed
 *    verification is shown as an error, never as success;
 *  - the server's `restart` envelope is rendered even when the write returned
 *    200 (`restart.status="failed"` must not look saved).
 *
 * Single file on purpose for M4: the draft baseline, the field-level diff, the
 * restore state machine and the post-write banners all read the same
 * `baseline`/`drafts`/`restore*` refs, and the table, both confirm dialogs and
 * the three outcome banners bind them directly. Splitting now would duplicate
 * that state or force a store for one tab. Before M5 (global audio) adds a
 * second editor surface, extract `AudioLeafRow` and a `useAudioDraft`
 * composable — the gate is recorded in README.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import AppIcon from '../../../components/AppIcon.vue'
import {
  api,
  type DeviceAudio,
  type DeviceDetail,
  type PatchDeviceAudioRequest,
  type PatchDeviceAudioResponse,
  type Restart,
} from '../../api'
import {
  AUDIO_GROUPS,
  AUDIO_LEAVES,
  audioGroupLabelKey,
  audioSpec,
  buildAudioRows,
  isInheritedSource,
  isNegotiatedCaptureRate,
  overridePathFor,
  type AudioRow,
} from '../../audio'
import { describeError, type ErrorCopy } from '../../errors'
import { displayValue } from '../../format'
import { t, type MsgKey } from '../../i18n'
import ConfirmDialog from '../ConfirmDialog.vue'
import ProvenanceTag from '../ProvenanceTag.vue'
import StateBlock from '../StateBlock.vue'
import ErrorNotice from './ErrorNotice.vue'
import RestartNotice from './RestartNotice.vue'

const props = withDefaults(
  defineProps<{
    deviceId: string
    audio: DeviceAudio | null
    loading: boolean
    errorCopy: ErrorCopy | null
    provenanceEnabled: boolean
    writeEnabled: boolean
    refreshing?: boolean
    planesRunning?: number | null
  }>(),
  { refreshing: false, planesRunning: null },
)

const emit = defineEmits<{
  retry: []
  /** Fresh audio (and device, when the write returned it) for the detail page. */
  updated: [payload: { audio: DeviceAudio; device?: DeviceDetail }]
  /** Same guard as the config tab: unsaved edits block a tab switch. */
  'dirty-change': [boolean]
}>()

interface AudioGroupView {
  id: string
  labelKey: MsgKey | null
  rows: AudioRow[]
}

interface ChangeView {
  path: string
  from: string
  to: string
}

const data = computed<DeviceAudio | null>(() => props.audio)
const rows = computed<AudioRow[]>(() => (data.value ? buildAudioRows(data.value) : []))
const hasRows = computed(() => rows.value.length > 0)
const plugUnresolved = computed(() => data.value?.plug_resolved === false)

const groupViews = computed<AudioGroupView[]>(() => {
  const groups = new Map<string, AudioRow[]>()
  for (const row of rows.value) {
    const list = groups.get(row.group) ?? []
    list.push(row)
    groups.set(row.group, list)
  }
  const order = new Map<string, number>(AUDIO_GROUPS.map((entry, index) => [entry.id, index]))
  return [...groups.entries()]
    .sort(([a], [b]) => {
      const byOrder = (order.get(a) ?? AUDIO_GROUPS.length) - (order.get(b) ?? AUDIO_GROUPS.length)
      return byOrder !== 0 ? byOrder : a.localeCompare(b)
    })
    .map(([id, list]) => ({ id, labelKey: audioGroupLabelKey(id), rows: list }))
})

function groupLabel(group: AudioGroupView): string {
  return group.labelKey ? t(group.labelKey) : group.id
}

/** Values as last loaded from the server; the diff base for PATCH. */
const baseline = ref<Record<string, unknown>>({})
/** Only rows the operator touched; a string keeps number inputs lossless. */
const drafts = ref<Record<string, string>>({})
const touched = ref<Record<string, boolean>>({})
const submitted = ref(false)
const serverFields = ref<Record<string, string>>({})

const saving = ref(false)
const restoringPath = ref<string | null>(null)
const confirmOpen = ref(false)
const confirmRestorePath = ref<string | null>(null)

const saveError = ref<ErrorCopy | null>(null)
const saveErrorRaw = ref<unknown>(null)
const restoreError = ref<ErrorCopy | null>(null)
const restoreReadError = ref<ErrorCopy | null>(null)
const restoreVerifyFailure = ref('')
/** Clear succeeded but the plug is unresolved: not a verification failure. */
const restoreUnresolved = ref('')
const restoreNotice = ref('')
const lastRestart = ref<Restart | null>(null)
/** Last leaf that was successfully cleared; the target of the read retry. */
const lastRestorePath = ref<string | null>(null)
const verifyingRestore = ref(false)

const busy = computed(
  () => saving.value || restoringPath.value !== null || verifyingRestore.value,
)
const canEdit = computed(
  () => props.writeEnabled && props.provenanceEnabled && !busy.value,
)

function syncFrom(next: DeviceAudio): void {
  const map: Record<string, unknown> = {}
  for (const spec of AUDIO_LEAVES) {
    const layer = next.layers?.[spec.path]
    map[spec.path] = layer?.device ?? null
  }
  baseline.value = map
  drafts.value = {}
  touched.value = {}
  submitted.value = false
  serverFields.value = {}
}

function baselineFor(row: AudioRow): string {
  const value = baseline.value[row.path]
  return value === null || value === undefined ? '' : String(value)
}

function controlValue(row: AudioRow): string {
  const draft = drafts.value[row.path]
  return draft !== undefined ? draft : baselineFor(row)
}

function isDirtyRow(row: AudioRow): boolean {
  if (!row.editable || !props.writeEnabled) return false
  const draft = drafts.value[row.path]
  if (draft === undefined) return false
  return draft !== baselineFor(row)
}

const dirtyRows = computed(() => rows.value.filter(isDirtyRow))
const dirty = computed(() => dirtyRows.value.length > 0)

watch(
  dirty,
  (value) => emit('dirty-change', value),
  { immediate: true },
)
onBeforeUnmount(() => emit('dirty-change', false))

watch(
  () => props.audio,
  (next) => {
    if (!next) return
    // Never discard a draft on a background refresh; a new write response is
    // applied explicitly by `save()`/`restoreOverride()` below.
    if (busy.value || dirty.value) return
    syncFrom(next)
  },
  { immediate: true },
)

watch(
  () => props.deviceId,
  () => {
    if (props.audio) syncFrom(props.audio)
    lastRestart.value = null
    restoreNotice.value = ''
    restoreUnresolved.value = ''
    restoreVerifyFailure.value = ''
    restoreError.value = null
    restoreReadError.value = null
    lastRestorePath.value = null
    // Keep `verifyingRestore` until the in-flight read settles: its guard
    // compares device ids, so clearing it early could let the old request's
    // `finally` unlock a verification started for the new device.
    saveError.value = null
  },
)

function controlKind(row: AudioRow): string | null {
  const declared = row.spec?.control
  if (!declared) return null
  // The server's effective value decides the widget where it is unambiguous;
  // the C-001 spec only fills the gaps (an unresolved leaf has no value) and
  // supplies ranges/enum options.
  if (typeof row.effective === 'boolean') return 'boolean'
  if (typeof row.effective === 'string' && (row.spec?.options?.length ?? 0) > 0) return 'enum'
  return declared
}

function optionsFor(row: AudioRow): Array<{ value: string | number; label: string }> {
  return row.spec?.options ?? []
}

function minFor(row: AudioRow): number | undefined {
  return row.spec?.min
}

function maxFor(row: AudioRow): number | undefined {
  return row.spec?.max
}

function inheritsCurrently(row: AudioRow): string {
  return t('v2.audio.edit.inheritCurrent', { value: displayValue(row.effective) })
}

function setControl(row: AudioRow, event: Event): void {
  if (!canEdit.value || !row.spec) return
  const raw = (event.target as HTMLInputElement | HTMLSelectElement).value
  if (raw === baselineFor(row)) {
    const next = { ...drafts.value }
    delete next[row.path]
    drafts.value = next
  } else {
    drafts.value = { ...drafts.value, [row.path]: raw }
  }
  touched.value = { ...touched.value, [row.path]: true }
  if (serverFields.value[row.path]) {
    const next = { ...serverFields.value }
    delete next[row.path]
    serverFields.value = next
  }
  saveError.value = null
  saveErrorRaw.value = null
}

function validationFor(row: AudioRow): string {
  if (!row.editable) return ''
  const spec = row.spec
  if (!spec) return ''
  const draft = drafts.value[row.path]
  if (draft === undefined) return ''
  const raw = draft.trim()
  if (!raw) {
    // An overridden leaf can only go back to inheritance through the explicit
    // button: silently PATCHing `null` from an emptied input would hide a
    // fleet restart behind a keystroke.
    return row.overridden ? t('v2.audio.edit.clearWithRestore') : ''
  }
  if (spec.control === 'number') {
    if (!/^-?\d+$/.test(raw)) return t('v2.audio.edit.validation.number')
    const value = Number(raw)
    if (
      (spec.min !== undefined && value < spec.min) ||
      (spec.max !== undefined && value > spec.max)
    ) {
      return t('v2.audio.edit.validation.range', {
        min: spec.min ?? '—',
        max: spec.max ?? '—',
      })
    }
  } else if (spec.control === 'boolean') {
    if (raw !== 'true' && raw !== 'false') return t('v2.audio.edit.validation.boolean')
  } else if (spec.control === 'enum') {
    if (!optionsFor(row).some((option) => String(option.value) === raw)) {
      return t('v2.audio.edit.validation.enum')
    }
  }
  return ''
}

function visibleError(row: AudioRow): string {
  if (!row.editable) return ''
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

const blockedByServer = computed(() =>
  dirtyRows.value.some((row) => Boolean(serverFields.value[row.path])),
)

function candidateValue(row: AudioRow): unknown | undefined {
  const spec = row.spec
  if (!spec) return undefined
  const draft = drafts.value[row.path]
  if (draft === undefined) return undefined
  const raw = draft.trim()
  if (!raw) return undefined
  if (spec.control === 'number') {
    const value = Number(raw)
    return Number.isFinite(value) ? value : undefined
  }
  if (spec.control === 'boolean') return raw === 'true'
  if (spec.control === 'enum') {
    return optionsFor(row).find((option) => String(option.value) === raw)?.value
  }
  return undefined
}

function assign(target: Record<string, unknown>, path: string, value: unknown): void {
  const parts = path.split('.')
  let current = target
  for (const part of parts.slice(0, -1)) {
    const next = current[part]
    if (!next || typeof next !== 'object' || Array.isArray(next)) current[part] = {}
    current = current[part] as Record<string, unknown>
  }
  current[parts[parts.length - 1]] = value
}

/**
 * Field-level diff: only rows whose draft differs from the loaded baseline.
 * Untouched leaves are omitted, so a PATCH never rewrites an inherited value.
 */
function buildPatch(): PatchDeviceAudioRequest | null {
  const audio: Record<string, unknown> = {}
  for (const row of dirtyRows.value) {
    if (!row.spec) continue
    const value = candidateValue(row)
    if (value === undefined) return null
    assign(audio, row.spec.overridePath, value)
  }
  if (Object.keys(audio).length === 0) return null
  return { audio }
}

function restoreBody(path: string): PatchDeviceAudioRequest {
  const audio: Record<string, unknown> = {}
  assign(audio, overridePathFor(path), null)
  return { audio }
}

const changeViews = computed<ChangeView[]>(() =>
  dirtyRows.value.map((row) => ({
    path: row.path,
    from: row.overridden
      ? displayValue(baseline.value[row.path])
      : t('v2.audio.edit.inherited'),
    to: displayValue(candidateValue(row)),
  })),
)

function resetDrafts(): void {
  if (busy.value) return
  drafts.value = {}
  touched.value = {}
  submitted.value = false
  serverFields.value = {}
  saveError.value = null
  saveErrorRaw.value = null
}

function requestSave(): void {
  if (!props.writeEnabled || !props.provenanceEnabled || busy.value) return
  submitted.value = true
  saveError.value = null
  saveErrorRaw.value = null
  if (!dirty.value) return
  if (Object.keys(validationErrors.value).length > 0 || blockedByServer.value) return
  if (!buildPatch()) return
  confirmOpen.value = true
}

function closeSaveConfirm(): void {
  if (!saving.value) confirmOpen.value = false
}

function mapServerPath(path: string): string {
  let next = path.trim()
  next = next.replace(/^devices\[\d+\]\.audio\./, '')
  next = next.replace(/^devices\.\d+\.audio\./, '')
  next = next.replace(/^audio\./, '')
  return next === 'voice.ack_echo_ignore_ms' ? 'ack.echo_ignore_ms' : next
}

function applyServerFields(err: unknown): void {
  const copy = err ? describeError(err) : null
  const map: Record<string, string> = {}
  for (const field of copy?.fields ?? []) {
    const path = mapServerPath(field.path)
    if (!audioSpec(path)) continue
    map[path] = field.message || t('v2.deviceConfig.validation.invalid')
  }
  serverFields.value = map
}

async function save(): Promise<void> {
  if (busy.value) return
  const patch = buildPatch()
  // Nothing actually changed (e.g. a value typed back to its baseline).
  if (!patch) {
    confirmOpen.value = false
    return
  }
  saving.value = true
  saveError.value = null
  saveErrorRaw.value = null
  serverFields.value = {}
  lastRestart.value = null
  restoreNotice.value = ''
  restoreVerifyFailure.value = ''
  restoreError.value = null
  restoreReadError.value = null
  try {
    const response = await api.patchDeviceAudio(props.deviceId, patch)
    // A 200 is not "done": the restart envelope can still report a failed fleet
    // rebuild. Keep it for `RestartNotice` and never show a green saved state.
    lastRestart.value = response.restart
    confirmOpen.value = false
    emit('updated', { audio: response.audio, device: response.device })
    syncFrom(response.audio)
  } catch (err) {
    saveErrorRaw.value = err
    saveError.value = describeError(err)
    applyServerFields(err)
    confirmOpen.value = false
  } finally {
    saving.value = false
  }
}

function requestRestore(path: string): void {
  if (!props.writeEnabled || !props.provenanceEnabled || busy.value || dirty.value) return
  confirmRestorePath.value = path
}

function cancelRestore(): void {
  if (!restoringPath.value) confirmRestorePath.value = null
}

const restoreTarget = computed<AudioRow | null>(() => {
  const path = confirmRestorePath.value
  if (!path) return null
  return rows.value.find((row) => row.path === path) ?? null
})

/**
 * Classify one confirming read of a cleared leaf. Only the inherited-layer
 * outcome is success; both failures set their own red copy, and the unresolved
 * case is neutral (the clear did happen, the inherited layer is unknowable).
 */
function applyRestoreOutcome(path: string, source: string, stillOverridden: boolean): void {
  restoreReadError.value = null
  restoreVerifyFailure.value = ''
  restoreUnresolved.value = ''
  restoreNotice.value = ''
  if (stillOverridden) {
    restoreVerifyFailure.value = t('v2.audio.restore.verifyFailed', {
      path,
      source: source || '—',
    })
  } else if (source === 'unresolved') {
    restoreUnresolved.value = t('v2.audio.restore.verifyUnresolved', { path })
  } else if (!isInheritedSource(source)) {
    restoreVerifyFailure.value = t('v2.audio.restore.verifyMissing', {
      path,
      source: source || '—',
    })
  } else {
    restoreNotice.value = t('v2.audio.restore.verified', { path, source })
  }
}

/**
 * Re-read `/audio` and verify the clear:
 *  - the source must no longer be `device-override`;
 *  - the leaf must have left `overrides`;
 *  - the source must be one of the three inherited layers, or `unresolved`
 *    (neutral: the plug cannot confirm the inherited layer).
 *
 * This is the *only* place that verifies a restore, so the retry button below
 * cannot silently skip the source/overrides checks the way a bare resource
 * reload of the parent page would.
 */
async function verifyRestored(path: string): Promise<void> {
  const spec = audioSpec(path)
  if (!spec || verifyingRestore.value) return
  const deviceId = props.deviceId
  verifyingRestore.value = true
  try {
    const fresh = await api.deviceAudio(deviceId)
    // A late response for the previous device must not rewrite this one.
    if (deviceId !== props.deviceId) return
    const source = String(fresh.sources?.[path] ?? fresh.layers?.[path]?.source ?? '')
    const stillOverridden =
      (fresh.overrides ?? []).includes(spec.overridePath) || source === 'device-override'
    // `onAudioUpdated` also clears the resource error, so a successful re-read
    // cannot leave the stale "read failed" banner behind.
    emit('updated', { audio: fresh })
    syncFrom(fresh)
    applyRestoreOutcome(path, source, stillOverridden)
  } catch (err) {
    if (deviceId !== props.deviceId) return
    // The PATCH itself succeeded; only the confirming read failed. Keep that
    // explicit — never leave the previous green notice in place.
    restoreNotice.value = ''
    restoreUnresolved.value = ''
    restoreVerifyFailure.value = ''
    restoreReadError.value = describeError(err)
  } finally {
    verifyingRestore.value = false
  }
}

/**
 * Clear one override, then re-read `/audio` through `verifyRestored`.
 */
async function restoreOverride(): Promise<void> {
  const path = confirmRestorePath.value
  if (!path || busy.value) return
  const spec = audioSpec(path)
  if (!spec) return
  restoringPath.value = path
  restoreError.value = null
  restoreReadError.value = null
  restoreVerifyFailure.value = ''
  restoreUnresolved.value = ''
  restoreNotice.value = ''
  lastRestart.value = null

  let patched: PatchDeviceAudioResponse
  try {
    patched = await api.patchDeviceAudio(props.deviceId, restoreBody(path))
    lastRestart.value = patched.restart
    lastRestorePath.value = path
    // Keep the local baseline in step with the write response even if the
    // confirming read below fails; otherwise the row would still render the
    // stale override value from before the clear.
    syncFrom(patched.audio)
    emit('updated', { audio: patched.audio, device: patched.device })
  } catch (err) {
    restoreError.value = describeError(err)
    restoringPath.value = null
    confirmRestorePath.value = null
    return
  }

  try {
    await verifyRestored(path)
  } finally {
    restoringPath.value = null
    confirmRestorePath.value = null
  }
}

function retryRestoreRead(): void {
  const path = lastRestorePath.value
  if (!path || busy.value) return
  // Re-run the GET *and* the source/overrides checks. Emitting the page-level
  // retry only reloaded the resource: a failed GET kept the old data, and the
  // panel then had no error copy to show.
  void verifyRestored(path)
}

function retryState(): void {
  // Read-only retry after a failed fleet rebuild: never re-submit the write.
  emit('retry')
}
</script>

<template>
  <div class="v2-detail-stack" data-testid="audio-panel">
    <StateBlock
      v-if="!provenanceEnabled && !data"
      kind="unavailable"
      :title="t('v2.audio.featureOff')"
      :hint="t('v2.audio.featureOffHint')"
    />

    <StateBlock
      v-else-if="loading && !data"
      kind="loading"
      :title="t('v2.common.loading')"
    />

    <StateBlock
      v-else-if="errorCopy && !data"
      kind="error"
      :title="t('v2.error.audioUnavailable')"
      :detail="errorCopy.detail"
      :hint="t('v2.audio.unavailableHint', { id: deviceId })"
      :status="errorCopy.status"
      :request-id="errorCopy.requestId"
      show-retry
      @retry="emit('retry')"
    />

    <StateBlock
      v-else-if="!data"
      kind="empty"
      :title="t('v2.audio.unavailable')"
      :hint="t('v2.audio.unavailableHint', { id: deviceId })"
    />

    <template v-else>
      <section class="v2-card">
        <div class="v2-card-head">
          <div>
            <h2 class="v2-card-title">{{ t('v2.audio.tab.title') }}</h2>
            <p class="v2-card-lede">{{ t('v2.audio.tab.lede') }}</p>
          </div>
          <span class="badge" :class="dirty ? 'badge-warn' : 'badge-muted'" data-testid="audio-dirty">
            {{ dirty ? t('v2.audio.edit.dirty', { n: dirtyRows.length }) : t('v2.audio.edit.clean') }}
          </span>
        </div>

        <div class="v2-audio-topnote" role="note">
          <AppIcon name="layers" :size="18" />
          <p class="note">{{ t('v2.audio.globalTopNote') }}</p>
        </div>

        <div v-if="!provenanceEnabled" class="note v2-catalog-note">
          {{ t('v2.audio.provenanceOff') }}
        </div>

        <div v-if="!writeEnabled" class="banner banner-warn v2-banner" role="status">
          <AppIcon name="lock" :size="18" />
          <div class="v2-banner-body">
            <strong>{{ t('v2.audio.writeOff') }}</strong>
            <p class="note">{{ t('v2.audio.writeOffHint') }}</p>
          </div>
        </div>

        <div
          v-if="plugUnresolved"
          class="banner banner-warn v2-banner"
          role="status"
          data-testid="audio-unresolved-banner"
        >
          <AppIcon name="alert" :size="18" />
          <div class="v2-banner-body">
            <strong>{{ t('v2.audio.unresolvedTitle') }}</strong>
            <p class="note">{{ t('v2.audio.unresolvedHint') }}</p>
          </div>
        </div>

        <!-- A failed reload keeps the last good `data`; show the error anyway
             so stale values can never look like a successful refresh. -->
        <ErrorNotice v-if="errorCopy" :error="errorCopy" show-retry @retry="emit('retry')" />

        <div v-if="hasRows" class="v2-audio-toolbar">
          <p class="note v2-audio-toolbar-note">{{ t('v2.audio.edit.diffHint') }}</p>
          <button
            type="button"
            class="btn"
            :disabled="busy || !dirty"
            @click="resetDrafts"
          >
            {{ t('v2.audio.edit.discard') }}
          </button>
          <button
            type="button"
            class="btn btn-primary"
            data-testid="audio-save"
            :disabled="!canEdit || !dirty"
            @click="requestSave"
          >
            <AppIcon name="check" :size="15" />
            {{ saving ? t('v2.audio.edit.saving') : t('v2.audio.edit.save') }}
          </button>
        </div>

        <StateBlock
          v-if="!hasRows"
          kind="empty"
          :title="t('v2.audio.empty')"
          :hint="t('v2.audio.emptyHint')"
        />

        <section
          v-for="group in groupViews"
          :key="group.id"
          class="v2-audio-group"
          :data-testid="`audio-group-${group.id}`"
        >
          <header class="v2-audio-group-head">
            <h3>{{ groupLabel(group) }}</h3>
            <span class="meta">{{ t('v2.common.items', { n: group.rows.length }) }}</span>
          </header>

          <div class="v2-audio-rows">
            <div
              v-for="row in group.rows"
              :key="row.path"
              class="v2-audio-row"
              :class="{
                'is-overridden': row.overridden,
                'is-dirty': isDirtyRow(row),
                'is-unresolved': row.source === 'unresolved',
              }"
              :data-testid="`audio-row-${row.path}`"
            >
              <div class="v2-audio-leaf">
                <div class="v2-audio-path">
                  <code class="mono">{{ row.path }}</code>
                  <span v-if="row.overridden" class="tag is-warn">
                    {{ t('v2.audio.overriddenBadge') }}
                  </span>
                  <span v-if="isDirtyRow(row)" class="tag is-warn" :data-testid="`audio-dirty-${row.path}`">
                    {{ t('v2.audio.edit.dirtyRow') }}
                  </span>
                </div>
                <div class="v2-audio-source">
                  <ProvenanceTag :source="row.source" />
                </div>
                <!-- Only capture.rate is negotiated. Every other manifest leaf is a
                     hardware fact that wins over global config; those disagreements
                     belong to the server's `conflicts` list, not a per-row warning. -->
                <p
                  v-if="isNegotiatedCaptureRate(row.path, row.layer)"
                  class="v2-audio-shadow"
                  :data-testid="`audio-shadow-${row.path}`"
                >
                  <AppIcon name="alert" :size="13" />
                  <span>
                    {{ t('v2.audio.globalShadow', {
                      global: displayValue(row.layer?.global),
                      manifest: displayValue(row.layer?.manifest),
                    }) }}
                  </span>
                </p>
                <p
                  v-if="row.source === 'unresolved'"
                  class="v2-audio-unresolved"
                  :data-testid="`audio-unresolved-${row.path}`"
                >
                  {{ t('v2.audio.unresolvedRow') }}
                </p>
              </div>

              <div class="v2-audio-effective">
                <span class="v2-audio-effective-label">{{ t('v2.audio.value') }}</span>
                <span class="mono v2-audio-effective-value">{{ displayValue(row.effective) }}</span>
              </div>

              <div class="v2-audio-control">
                <template v-if="row.editable">
                  <template v-if="controlKind(row) === 'number'">
                    <input
                      type="number"
                      inputmode="numeric"
                      class="input v2-audio-input"
                      :value="controlValue(row)"
                      :placeholder="inheritsCurrently(row)"
                      :min="minFor(row)"
                      :max="maxFor(row)"
                      :step="1"
                      :disabled="!canEdit"
                      :aria-invalid="Boolean(visibleError(row))"
                      :data-testid="`audio-input-${row.path}`"
                      @input="setControl(row, $event)"
                    />
                  </template>
                  <template v-else>
                    <select
                      class="input v2-audio-input"
                      :value="controlValue(row)"
                      :disabled="!canEdit"
                      :aria-invalid="Boolean(visibleError(row))"
                      :data-testid="`audio-input-${row.path}`"
                      @change="setControl(row, $event)"
                    >
                      <option v-if="!row.overridden" value="">{{ inheritsCurrently(row) }}</option>
                      <template v-if="controlKind(row) === 'boolean'">
                        <option value="true">{{ t('v2.common.yes') }}</option>
                        <option value="false">{{ t('v2.common.no') }}</option>
                      </template>
                      <template v-else>
                        <option
                          v-for="option in optionsFor(row)"
                          :key="String(option.value)"
                          :value="String(option.value)"
                        >
                          {{ option.label }}
                        </option>
                      </template>
                    </select>
                  </template>
                  <p
                    v-if="visibleError(row)"
                    class="field-error"
                    :data-testid="`audio-error-${row.path}`"
                  >
                    {{ visibleError(row) }}
                  </p>
                  <p v-if="!row.overridden && !drafts[row.path]" class="v2-audio-inherit-note">
                    {{ t('v2.audio.edit.inherited') }}
                  </p>
                  <button
                    v-if="row.overridden"
                    type="button"
                    class="btn btn-sm v2-audio-restore"
                    :disabled="!canEdit || dirty"
                    :title="dirty ? t('v2.audio.edit.noDirtyForRestore') : undefined"
                    :data-testid="`audio-restore-${row.path}`"
                    @click="requestRestore(row.path)"
                  >
                    <AppIcon name="refresh" :size="13" />
                    {{ t('v2.audio.restore.action') }}
                  </button>
                </template>
                <span v-else class="meta v2-audio-readonly">{{ t('v2.audio.edit.readOnlyLeaf') }}</span>
              </div>

              <details v-if="row.layer" class="v2-audio-layers" :data-testid="`audio-layers-${row.path}`">
                <summary>{{ t('v2.audio.layers') }}</summary>
                <dl class="v2-layer-grid">
                  <div>
                    <dt>{{ t('v2.audio.layer.core') }}</dt>
                    <dd class="mono">{{ displayValue(row.layer.core) }}</dd>
                  </div>
                  <div>
                    <dt>{{ t('v2.audio.layer.manifest') }}</dt>
                    <dd class="mono">{{ displayValue(row.layer.manifest) }}</dd>
                  </div>
                  <div>
                    <dt>{{ t('v2.audio.layer.global') }}</dt>
                    <dd class="mono">
                      {{ displayValue(row.layer.global) }}
                      <span v-if="row.layer.global_explicit !== undefined" class="tag">
                        {{
                          row.layer.global_explicit
                            ? t('v2.audio.globalExplicit')
                            : t('v2.audio.globalImplicit')
                        }}
                      </span>
                    </dd>
                  </div>
                  <div>
                    <dt>{{ t('v2.audio.layer.device') }}</dt>
                    <dd class="mono">{{ displayValue(row.layer.device) }}</dd>
                  </div>
                </dl>
              </details>
              <span v-else class="meta v2-audio-readonly">{{ t('v2.common.na') }}</span>
            </div>
          </div>
        </section>

        <div v-if="hasRows && writeEnabled" class="v2-save-bar">
          <p class="note v2-save-state">
            {{ dirty ? t('v2.audio.edit.dirty', { n: dirtyRows.length }) : t('v2.audio.edit.noChanges') }}
          </p>
          <button type="button" class="btn" :disabled="busy || !dirty" @click="resetDrafts">
            {{ t('v2.audio.edit.discard') }}
          </button>
          <button
            type="button"
            class="btn btn-primary"
            data-testid="audio-save-bottom"
            :disabled="!canEdit || !dirty"
            @click="requestSave"
          >
            {{ saving ? t('v2.audio.edit.saving') : t('v2.audio.edit.save') }}
          </button>
        </div>
      </section>

      <ErrorNotice :error="saveError" />
      <ErrorNotice :error="restoreError" />

      <div
        v-if="restoreVerifyFailure"
        class="banner banner-fail v2-banner"
        role="alert"
        data-testid="audio-restore-verify-failed"
      >
        <AppIcon name="alert" :size="18" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.audio.restore.verifyTitle') }}</strong>
          <p class="note v2-break">{{ restoreVerifyFailure }}</p>
          <button
            type="button"
            class="btn btn-sm"
            :disabled="verifyingRestore"
            data-testid="audio-restore-retry"
            @click="retryRestoreRead"
          >
            {{
              verifyingRestore
                ? t('v2.audio.restore.reading')
                : t('v2.audio.restore.retryRead')
            }}
          </button>
        </div>
      </div>

      <div
        v-if="restoreReadError"
        class="banner banner-fail v2-banner"
        role="alert"
        data-testid="audio-restore-read-failed"
      >
        <AppIcon name="alert" :size="18" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.audio.restore.readFailed') }}</strong>
          <p class="note mono v2-break">{{ restoreReadError.detail }}</p>
          <button
            type="button"
            class="btn btn-sm"
            :disabled="verifyingRestore"
            data-testid="audio-restore-retry"
            @click="retryRestoreRead"
          >
            {{
              verifyingRestore
                ? t('v2.audio.restore.reading')
                : t('v2.audio.restore.retryRead')
            }}
          </button>
        </div>
      </div>

      <div
        v-if="restoreUnresolved"
        class="banner banner-warn v2-banner"
        role="status"
        data-testid="audio-restore-unresolved"
      >
        <AppIcon name="alert" :size="18" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.audio.unresolvedTitle') }}</strong>
          <p class="note v2-break">{{ restoreUnresolved }}</p>
          <button
            type="button"
            class="btn btn-sm"
            :disabled="verifyingRestore"
            data-testid="audio-restore-retry"
            @click="retryRestoreRead"
          >
            {{
              verifyingRestore
                ? t('v2.audio.restore.reading')
                : t('v2.audio.restore.retryRead')
            }}
          </button>
        </div>
      </div>

      <div
        v-if="restoreNotice"
        class="banner banner-ok v2-banner"
        role="status"
        data-testid="audio-restore-ok"
      >
        <AppIcon name="check" :size="18" />
        <div class="v2-banner-body">
          <strong>{{ restoreNotice }}</strong>
        </div>
      </div>

      <RestartNotice
        v-if="lastRestart"
        :restart="lastRestart"
        :retrying="refreshing"
        @retry="retryState"
      />
    </template>

    <ConfirmDialog
      v-if="confirmOpen"
      :title="t('v2.audio.edit.confirmTitle')"
      :confirm-label="t('v2.audio.edit.confirmAction')"
      :cancel-label="t('v2.audio.edit.confirmCancel')"
      :busy="saving"
      :busy-label="t('v2.audio.edit.saving')"
      @confirm="save"
      @cancel="closeSaveConfirm"
    >
      <p class="note v2-confirm-lead">{{ t('v2.audio.edit.confirmBody') }}</p>
      <dl class="v2-kv v2-confirm-kv">
        <div>
          <dt>{{ t('v2.audio.edit.confirmScope') }}</dt>
          <dd class="mono">{{ t('v2.restart.scope.fleet') }}</dd>
        </div>
        <div>
          <dt>{{ t('v2.audio.edit.confirmFields') }}</dt>
          <dd class="mono">{{ changeViews.length }}</dd>
        </div>
      </dl>
      <p v-if="planesRunning !== null" class="note">
        {{ t('v2.deviceConfig.confirmPlanes', { n: planesRunning }) }}
      </p>
      <ul class="v2-confirm-changes">
        <li v-for="change in changeViews" :key="change.path">
          <span class="mono">{{ change.path }}</span>
          <span class="v2-audio-change"> {{ change.from }} → {{ change.to }}</span>
        </li>
      </ul>
    </ConfirmDialog>

    <ConfirmDialog
      v-if="restoreTarget"
      :title="t('v2.audio.restore.title')"
      :confirm-label="t('v2.audio.restore.confirm')"
      :cancel-label="t('v2.audio.restore.cancel')"
      :busy="Boolean(restoringPath)"
      :busy-label="t('v2.audio.restore.busy')"
      @confirm="restoreOverride"
      @cancel="cancelRestore"
    >
      <p class="note v2-confirm-lead">{{ t('v2.audio.restore.body') }}</p>
      <dl class="v2-kv v2-confirm-kv">
        <div>
          <dt>{{ t('v2.audio.field') }}</dt>
          <dd class="mono">{{ restoreTarget.path }}</dd>
        </div>
        <div>
          <dt>{{ t('v2.audio.restore.current') }}</dt>
          <dd class="mono">{{ displayValue(restoreTarget.layer?.device) }}</dd>
        </div>
      </dl>
    </ConfirmDialog>
  </div>
</template>
