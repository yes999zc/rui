<script setup lang="ts">
/**
 * Device config tab (M3).
 *
 * Editable: room/host/enabled/wake_words/volume_target/listen_port — the six
 * promoted DeviceConfig fields. `PATCH /devices/{id}` is atomic, returns the new
 * detail plus the fleet `restart` envelope, and rejects unknown/immutable
 * fields. Every save therefore goes through a second confirmation and the
 * result block always shows scope/status/error (contract §4.5).
 *
 * PATCH is a **field-level diff against the loaded baseline**: only values the
 * operator actually changed are sent. Sending the whole form used to rewrite
 * `listen_port`/`wake_words` that merely had inherited defaults materialised;
 * see `buildPatch()` for the exact rules. `GET /devices/{id}` only exposes the
 * *effective* `listen_port` (contract §7.12), not the stored value, so the port
 * input starts empty ("inherit") and the effective value is shown as a hint —
 * never pre-filled and never submitted unless edited (contract §7.13).
 *
 * 422 `details.fields[]` paths are normalised back to the input that produced
 * them (`devices[0].room` → `room`, `wake_words[1]` → third wake row) and stay
 * highlighted until that field is edited.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import AppIcon from '../../../components/AppIcon.vue'
import {
  api,
  type DeviceDetail,
  type HouseInfo,
  type PatchDeviceRequest,
  type PatchDeviceResponse,
  type PluginInfo,
  type Restart,
} from '../../api'
import {
  apiErrorCode,
  apiErrorDetail,
  describeError,
  normalizeFieldPath,
  type ErrorCopy,
} from '../../errors'
import { t } from '../../i18n'
import ConfirmDialog from '../ConfirmDialog.vue'
import ErrorNotice from './ErrorNotice.vue'
import RestartNotice from './RestartNotice.vue'

const props = withDefaults(
  defineProps<{
    detail: DeviceDetail
    plugin: PluginInfo | null
    house: HouseInfo | null
    featureEnabled: boolean
    refreshing?: boolean
  }>(),
  { refreshing: false },
)

const emit = defineEmits<{
  saved: [PatchDeviceResponse]
  refresh: []
  /** Lets the detail page guard tab switches while a draft is unsaved. */
  'dirty-change': [boolean]
}>()

interface FormState {
  room: string
  host: string
  enabled: boolean
  /** `v-model` on `<input type="number">` casts to number, so accept both. */
  listenPort: string | number
  volumeTarget: string | number
  wakeWords: string[]
}

type ConfigField = 'room' | 'host' | 'enabled' | 'listen_port' | 'volume_target' | 'wake_words'

const form = ref<FormState>(emptyForm())
/** Values as last loaded/saved from the server; the diff base for PATCH. */
const baseline = ref<FormState>(emptyForm())
const formId = ref('')
/**
 * `true` once the operator produced an input event on the port field. The input
 * is never pre-filled from the effective port, so an emptied field is the only
 * way to ask for an explicit `listen_port: null` (contract §7.13).
 */
const portEdited = ref(false)
const saving = ref(false)
const submitted = ref(false)
const touched = ref<Record<string, boolean>>({})
const confirmOpen = ref(false)
const saveError = ref<ErrorCopy | null>(null)
const saveErrorRaw = ref<unknown>(null)
const serverFields = ref<Record<string, string>>({})
const lastRestart = ref<Restart | null>(null)

function emptyForm(): FormState {
  return {
    room: '',
    host: '',
    enabled: true,
    listenPort: '',
    volumeTarget: '0',
    wakeWords: [],
  }
}

function formFrom(detail: DeviceDetail): FormState {
  return {
    room: detail.room ?? '',
    host: detail.host ?? '',
    enabled: Boolean(detail.enabled),
    // Deliberately empty: `detail.listen_port` is the *effective* port (stored
    // value or manifest default, `v2_api.py` `effective_listen_port`). Writing
    // it back would pin the manifest default into config forever. An empty
    // field means "leave listen_port alone"; the current effective value is
    // only shown as a hint.
    listenPort: '',
    volumeTarget: String(detail.volume_target ?? 0),
    wakeWords: [...(detail.wake_words ?? [])],
  }
}

/** Numeric text without leading zeros / float noise; empty stays empty. */
function cleanNumber(value: string | number): string {
  const raw = String(value ?? '').trim()
  if (!raw) return ''
  const number = Number(raw)
  return Number.isFinite(number) ? String(number) : raw
}

/**
 * Canonical view of a form for diffing: trimmed strings, numeric text without
 * leading zeros, wake words stripped of blank rows. Editing " 客厅 " or adding
 * an empty wake row is therefore not a change (and never becomes a PATCH field).
 */
function snapshot(state: FormState): FormState {
  return {
    room: state.room.trim(),
    host: state.host.trim(),
    enabled: state.enabled,
    listenPort: cleanNumber(state.listenPort),
    volumeTarget: cleanNumber(state.volumeTarget),
    wakeWords: state.wakeWords.map((word) => word.trim()).filter(Boolean),
  }
}

function resetFrom(detail: DeviceDetail): void {
  const next = formFrom(detail)
  form.value = { ...next, wakeWords: [...next.wakeWords] }
  formId.value = detail.id
  baseline.value = snapshot(next)
  submitted.value = false
  touched.value = {}
  serverFields.value = {}
  portEdited.value = false
}

/**
 * Fields whose value differs from the baseline. `listen_port` is special: the
 * effective value is never loaded into the input, so "edited" (any input event)
 * is what makes it dirty — an emptied field then means `listen_port: null`.
 */
const changedFields = computed<Set<ConfigField>>(() => {
  const before = snapshot(baseline.value)
  const after = snapshot(form.value)
  const changed = new Set<ConfigField>()
  if (before.room !== after.room) changed.add('room')
  if (before.host !== after.host) changed.add('host')
  if (before.enabled !== after.enabled) changed.add('enabled')
  if (before.volumeTarget !== after.volumeTarget) changed.add('volume_target')
  if (before.wakeWords.join('\u0000') !== after.wakeWords.join('\u0000')) {
    changed.add('wake_words')
  }
  if (portEdited.value) changed.add('listen_port')
  return changed
})
const dirty = computed(() => changedFields.value.size > 0)
const multiDeviceEnabled = computed(() => props.house?.multi_device?.enabled !== false)
const planesRunning = computed(() => props.house?.multi_device?.planes_running ?? null)

watch(dirty, (value) => emit('dirty-change', value))
onBeforeUnmount(() => emit('dirty-change', false))

watch(
  () => props.detail,
  (next) => {
    if (!next) return
    // A deep link/id change always resets. A refresh with local edits must not
    // discard the operator's draft; their dirty marker stays visible.
    if (next.id !== formId.value) {
      resetFrom(next)
      return
    }
    if (!saving.value && !dirty.value) resetFrom(next)
  },
  { immediate: true },
)

/** Effective port reported by the API (manifest default when not stored). */
const effectivePort = computed<number | null>(() => props.detail.listen_port ?? null)
const portPlaceholder = computed(() =>
  effectivePort.value === null
    ? t('v2.deviceConfig.field.portPlaceholder')
    : t('v2.deviceConfig.field.portPlaceholderEffective', { port: effectivePort.value }),
)
const portInheritHint = computed(() =>
  effectivePort.value === null
    ? t('v2.deviceConfig.field.portInheritHintUnknown')
    : t('v2.deviceConfig.field.portInheritHint', { port: effectivePort.value }),
)

function parsePort(): number | null | 'invalid' {
  const raw = String(form.value.listenPort ?? '').trim()
  if (!raw) return null
  if (!/^\d+$/.test(raw)) return 'invalid'
  const value = Number(raw)
  if (!Number.isInteger(value) || value < 1 || value > 65535) return 'invalid'
  return value
}

function cleanWakeWords(): string[] {
  return form.value.wakeWords.map((word) => word.trim()).filter(Boolean)
}

function clientErrors(): Record<string, string> {
  const errors: Record<string, string> = {}
  if (multiDeviceEnabled.value && !form.value.room.trim()) {
    errors.room = t('v2.deviceConfig.validation.roomRequired')
  }
  const host = form.value.host.trim()
  if (!host) {
    errors.host = t('v2.deviceConfig.validation.hostRequired')
  } else if (/\s/.test(host) || host.includes('://') || host.length > 64) {
    errors.host = t('v2.deviceConfig.validation.hostFormat')
  }
  if (parsePort() === 'invalid') {
    errors.listen_port = t('v2.deviceConfig.validation.portRange')
  }
  const volume = String(form.value.volumeTarget ?? '').trim()
  if (!/^\d+$/.test(volume) || Number(volume) < 0 || Number(volume) > 100) {
    errors.volume_target = t('v2.deviceConfig.validation.volumeRange')
  }
  const words = cleanWakeWords()
  if (words.length > 3) errors.wake_words = t('v2.deviceConfig.validation.wakeMax')
  form.value.wakeWords.forEach((word, index) => {
    const trimmed = word.trim()
    if (trimmed.length > 12) {
      errors[`wake_words:${index}`] = t('v2.deviceConfig.validation.wakeTooLong')
    }
  })
  return errors
}

const validation = computed(clientErrors)

function visibleError(key: string): string {
  if (serverFields.value[key]) return serverFields.value[key]
  if ((submitted.value || touched.value[key]) && validation.value[key]) {
    return validation.value[key]
  }
  return ''
}

function fieldInvalid(key: string): boolean {
  return Boolean(visibleError(key))
}

function touch(key: string): void {
  touched.value[key] = true
  if (serverFields.value[key]) {
    const next = { ...serverFields.value }
    delete next[key]
    serverFields.value = next
  }
  // A new edit invalidates the previous server rejection; keep field-level
  // errors for untouched inputs, but drop the global notice/failure banner.
  saveError.value = null
  saveErrorRaw.value = null
}

/**
 * The port input is not pre-filled from the effective port, so any input event
 * means the operator made a deliberate statement about `listen_port`: a number
 * writes it, an empty value writes `null` (back to inherit).
 */
function onPortInput(): void {
  portEdited.value = true
  touch('listen_port')
}

function setWake(index: number, value: string): void {
  form.value.wakeWords[index] = value
  touch(`wake_words:${index}`)
  touch('wake_words')
}

function addWake(): void {
  if (form.value.wakeWords.length >= 3) return
  form.value.wakeWords.push('')
}

function removeWake(index: number): void {
  form.value.wakeWords.splice(index, 1)
  touch('wake_words')
}

const changedLabels = computed(() => {
  const labels: Array<[ConfigField, string]> = [
    ['room', t('v2.deviceConfig.field.room')],
    ['host', t('v2.deviceConfig.field.host')],
    ['enabled', t('v2.deviceConfig.field.enabled')],
    ['listen_port', t('v2.deviceConfig.field.listenPort')],
    ['volume_target', t('v2.deviceConfig.field.volumeTarget')],
    ['wake_words', t('v2.deviceConfig.field.wakeWords')],
  ]
  return labels
    .filter(([field]) => changedFields.value.has(field))
    .map(([, label]) => label)
})

const conflictIds = computed<string[]>(() => {
  if (apiErrorCode(saveErrorRaw.value) !== 'port_conflict') return []
  const ids = apiErrorDetail(saveErrorRaw.value, 'device_ids')
  return Array.isArray(ids) ? ids.map(String) : []
})
const conflictPort = computed<number | null>(() => {
  if (apiErrorCode(saveErrorRaw.value) !== 'port_conflict') return null
  const port = apiErrorDetail(saveErrorRaw.value, 'port')
  return typeof port === 'number' ? port : null
})

function fieldKnown(path: string): boolean {
  const base = path.split('.')[0]
  return ['room', 'host', 'listen_port', 'volume_target', 'enabled', 'wake_words'].includes(base)
}

function applyServerFields(err: unknown): void {
  const copy = err ? describeError(err) : null
  const map: Record<string, string> = {}
  for (const field of copy?.fields ?? []) {
    const path = normalizeFieldPath(field.path)
    const message = field.message || t('v2.deviceConfig.validation.invalid')
    const indexed = path.match(/^wake_words(?:\[(\d+)\]|\.(\d+))$/)
    if (indexed) {
      map[`wake_words:${indexed[1] ?? indexed[2]}`] = message
      continue
    }
    if (fieldKnown(path)) {
      map[path.split('.')[0]] = message
      continue
    }
  }
  if (copy && apiErrorCode(err) === 'port_conflict') {
    const message = copy.detail || t('v2.deviceConfig.validation.invalid')
    map.host = map.host || message
    map.listen_port = map.listen_port || message
  }
  serverFields.value = map
}

function requestSave(): void {
  if (!props.featureEnabled || saving.value || !dirty.value) return
  submitted.value = true
  saveError.value = null
  saveErrorRaw.value = null
  if (Object.keys(validation.value).length > 0) return
  confirmOpen.value = true
}

function closeConfirm(): void {
  if (saving.value) return
  confirmOpen.value = false
}

/**
 * Build the PATCH body from the diff only (contract §7.13: every field is
 * optional). Rules verified against real requests:
 *   - untouched room/host/enabled/volume_target/wake_words → omitted;
 *   - wake_words edited to zero rows → `[]` (empty = inherit per §7.11);
 *   - listen_port untouched → omitted; edited to empty → `null`; edited to a
 *     number → that number. The effective port is never implicitly submitted.
 */
function buildPatch(): PatchDeviceRequest {
  const changed = changedFields.value
  const state = snapshot(form.value)
  const payload: PatchDeviceRequest = {}
  if (changed.has('room')) payload.room = state.room
  if (changed.has('host')) payload.host = state.host
  if (changed.has('enabled')) payload.enabled = state.enabled
  if (changed.has('volume_target')) payload.volume_target = Number(state.volumeTarget)
  if (changed.has('wake_words')) payload.wake_words = cleanWakeWords()
  if (changed.has('listen_port')) {
    const port = parsePort()
    if (port === 'invalid') return {}
    payload.listen_port = port
  }
  return payload
}

async function save(): Promise<void> {
  if (saving.value) return
  if (parsePort() === 'invalid') return
  const payload = buildPatch()
  // Nothing actually changed (e.g. whitespace-only edit): close without a write.
  if (Object.keys(payload).length === 0) {
    confirmOpen.value = false
    return
  }
  saving.value = true
  saveError.value = null
  saveErrorRaw.value = null
  serverFields.value = {}
  try {
    const response = await api.patchDevice(props.detail.id, payload)
    lastRestart.value = response.restart
    const next = formFrom(response.device)
    form.value = { ...next, wakeWords: [...next.wakeWords] }
    baseline.value = snapshot(next)
    submitted.value = false
    touched.value = {}
    portEdited.value = false
    confirmOpen.value = false
    emit('saved', response)
  } catch (err) {
    saveErrorRaw.value = err
    saveError.value = describeError(err)
    applyServerFields(err)
    confirmOpen.value = false
  } finally {
    saving.value = false
  }
}

function retryRead(): void {
  emit('refresh')
}
</script>

<template>
  <div class="v2-detail-stack">
    <div v-if="!featureEnabled" class="banner banner-warn v2-banner" role="status">
      <AppIcon name="lock" :size="18" />
      <div class="v2-banner-body">
        <strong>{{ t('v2.deviceConfig.featureOff') }}</strong>
        <p class="note">{{ t('v2.deviceConfig.featureOffHint') }}</p>
      </div>
    </div>

    <section class="v2-card" data-testid="config-form">
      <div class="v2-card-head">
        <div>
          <h2 class="v2-card-title">{{ t('v2.deviceConfig.title') }}</h2>
          <p class="v2-card-lede">{{ t('v2.deviceConfig.lede') }}</p>
        </div>
        <span class="badge" :class="dirty ? 'badge-warn' : 'badge-muted'">
          {{ dirty ? t('v2.deviceConfig.dirty') : t('v2.deviceConfig.clean') }}
        </span>
      </div>

      <div class="field-row" :class="{ invalid: fieldInvalid('room') }">
        <div class="field-meta">
          <label class="field-label" for="config-room">
            {{ t('v2.deviceConfig.field.room') }}
          </label>
          <p class="field-hint">{{ t('v2.deviceConfig.field.roomHint') }}</p>
        </div>
        <div class="field-control">
          <input
            id="config-room"
            v-model="form.room"
            type="text"
            class="input"
            :class="{ invalid: fieldInvalid('room') }"
            :aria-invalid="fieldInvalid('room')"
            :placeholder="t('v2.deviceConfig.field.roomPlaceholder')"
            :disabled="saving || !featureEnabled"
            @input="touch('room')"
          />
          <p v-if="visibleError('room')" class="field-error" data-testid="error-room">
            {{ visibleError('room') }}
          </p>
        </div>
      </div>

      <div class="field-row" :class="{ invalid: fieldInvalid('host') }">
        <div class="field-meta">
          <label class="field-label" for="config-host">
            {{ t('v2.deviceConfig.field.host') }}
          </label>
          <p class="field-hint">{{ t('v2.deviceConfig.field.hostHint') }}</p>
        </div>
        <div class="field-control">
          <input
            id="config-host"
            v-model="form.host"
            type="text"
            class="input"
            :class="{ invalid: fieldInvalid('host') }"
            :aria-invalid="fieldInvalid('host')"
            :placeholder="t('v2.deviceConfig.field.hostPlaceholder')"
            :disabled="saving || !featureEnabled"
            @input="touch('host')"
          />
          <p v-if="visibleError('host')" class="field-error" data-testid="error-host">
            {{ visibleError('host') }}
          </p>
        </div>
      </div>

      <div class="field-row" :class="{ invalid: fieldInvalid('listen_port') }">
        <div class="field-meta">
          <label class="field-label" for="config-listen-port">
            {{ t('v2.deviceConfig.field.listenPort') }}
          </label>
          <p class="field-hint">{{ t('v2.deviceConfig.field.listenPortHint') }}</p>
        </div>
        <div class="field-control">
          <input
            id="config-listen-port"
            v-model="form.listenPort"
            type="number"
            inputmode="numeric"
            min="1"
            max="65535"
            class="input"
            :class="{ invalid: fieldInvalid('listen_port') }"
            :aria-invalid="fieldInvalid('listen_port')"
            :placeholder="portPlaceholder"
            :disabled="saving || !featureEnabled"
            data-testid="config-listen-port"
            @input="onPortInput"
          />
          <p
            v-if="!String(form.listenPort ?? '').trim()"
            class="field-hint"
            data-testid="port-inherit-hint"
          >
            {{ portInheritHint }}
          </p>
          <p
            v-if="visibleError('listen_port')"
            class="field-error"
            data-testid="error-listen-port"
          >
            {{ visibleError('listen_port') }}
          </p>
          <p
            v-if="conflictIds.length || conflictPort"
            class="field-error"
            data-testid="error-port-conflict"
          >
            {{
              t('v2.add.portConflict', {
                port: conflictPort ?? '—',
                ids: conflictIds.join(', ') || '—',
              })
            }}
          </p>
        </div>
      </div>

      <div class="field-row" :class="{ invalid: fieldInvalid('volume_target') }">
        <div class="field-meta">
          <label class="field-label" for="config-volume-target">
            {{ t('v2.deviceConfig.field.volumeTarget') }}
          </label>
          <p class="field-hint">{{ t('v2.deviceConfig.field.volumeTargetHint') }}</p>
        </div>
        <div class="field-control">
          <input
            id="config-volume-target"
            v-model="form.volumeTarget"
            type="number"
            inputmode="numeric"
            min="0"
            max="100"
            class="input"
            :class="{ invalid: fieldInvalid('volume_target') }"
            :aria-invalid="fieldInvalid('volume_target')"
            :disabled="saving || !featureEnabled"
            @input="touch('volume_target')"
          />
          <p
            v-if="visibleError('volume_target')"
            class="field-error"
            data-testid="error-volume-target"
          >
            {{ visibleError('volume_target') }}
          </p>
        </div>
      </div>

      <div class="field-row" :class="{ invalid: fieldInvalid('wake_words') }">
        <div class="field-meta">
          <label class="field-label">{{ t('v2.deviceConfig.field.wakeWords') }}</label>
          <p class="field-hint">{{ t('v2.deviceConfig.field.wakeWordsHint') }}</p>
        </div>
        <div class="field-control">
          <p v-if="!form.wakeWords.length" class="empty v2-wake-empty">
            {{ t('v2.deviceConfig.wakeEmpty') }}
          </p>
          <div
            v-for="(word, index) in form.wakeWords"
            :key="`wake-${index}`"
            class="v2-wake-row"
          >
            <input
              :id="`config-wake-${index}`"
              :value="word"
              type="text"
              maxlength="12"
              class="input"
              :class="{ invalid: fieldInvalid(`wake_words:${index}`) }"
              :aria-invalid="fieldInvalid(`wake_words:${index}`)"
              :placeholder="t('v2.deviceConfig.field.wakePlaceholder')"
              :disabled="saving || !featureEnabled"
              @input="setWake(index, ($event.target as HTMLInputElement).value)"
            />
            <button
              type="button"
              class="btn btn-sm"
              :aria-label="t('v2.deviceConfig.wakeRemove', { n: index + 1 })"
              :disabled="saving || !featureEnabled"
              @click="removeWake(index)"
            >
              <AppIcon name="trash" :size="14" />
            </button>
            <p
              v-if="visibleError(`wake_words:${index}`)"
              class="field-error"
              :data-testid="`error-wake-${index}`"
            >
              {{ visibleError(`wake_words:${index}`) }}
            </p>
          </div>
          <p v-if="visibleError('wake_words')" class="field-error" data-testid="error-wake-words">
            {{ visibleError('wake_words') }}
          </p>
          <button
            type="button"
            class="btn btn-sm v2-wake-add"
            :disabled="saving || !featureEnabled || form.wakeWords.length >= 3"
            @click="addWake"
          >
            <AppIcon name="plus" :size="14" />
            {{ t('v2.deviceConfig.wakeAdd') }}
          </button>
        </div>
      </div>

      <div class="field-row">
        <div class="field-meta">
          <label class="field-label" for="config-enabled">
            {{ t('v2.deviceConfig.field.enabled') }}
          </label>
          <p class="field-hint">{{ t('v2.deviceConfig.field.enabledHint') }}</p>
        </div>
        <div class="field-control">
          <label class="v2-checkbox">
            <input
              id="config-enabled"
              v-model="form.enabled"
              type="checkbox"
              :disabled="saving || !featureEnabled"
              @change="touch('enabled')"
            />
            <span>{{ form.enabled ? t('v2.common.enabled') : t('v2.common.disabled') }}</span>
          </label>
          <p v-if="visibleError('enabled')" class="field-error">{{ visibleError('enabled') }}</p>
        </div>
      </div>

      <div class="v2-save-bar">
        <p class="note v2-save-state">
          {{ dirty ? t('v2.deviceConfig.dirty') : t('v2.deviceConfig.noChanges') }}
        </p>
        <button
          type="button"
          class="btn"
          :disabled="saving || !dirty"
          :title="t('v2.deviceConfig.resetHint')"
          @click="resetFrom(detail)"
        >
          {{ t('v2.deviceConfig.reset') }}
        </button>
        <button
          type="button"
          class="btn btn-primary"
          data-testid="config-save"
          :disabled="saving || !dirty || !featureEnabled"
          @click="requestSave"
        >
          <AppIcon name="check" :size="15" />
          {{ saving ? t('v2.deviceConfig.saving') : t('v2.deviceConfig.save') }}
        </button>
      </div>
    </section>

    <section class="v2-card">
      <h2 class="v2-card-title">{{ t('v2.deviceConfig.immutableTitle') }}</h2>
      <p class="v2-card-lede">{{ t('v2.deviceConfig.immutableHint') }}</p>
      <dl class="v2-kv">
        <div>
          <dt>{{ t('v2.deviceConfig.immutable.id') }}</dt>
          <dd class="mono">{{ detail.id }}</dd>
        </div>
        <div>
          <dt>{{ t('v2.deviceConfig.immutable.plugin') }}</dt>
          <dd>
            <span class="mono">{{ detail.plugin_id }}</span>
            <span v-if="plugin" class="meta v2-block">{{ plugin.name }}</span>
          </dd>
        </div>
        <div>
          <dt>{{ t('v2.deviceConfig.immutable.model') }}</dt>
          <dd>
            <span>{{ detail.model || t('v2.common.na') }}</span>
            <span class="meta mono v2-block">{{ detail.model_id }}</span>
          </dd>
        </div>
        <div>
          <dt>{{ t('v2.deviceConfig.immutable.adapter') }}</dt>
          <dd>
            <span class="mono">{{ detail.model_id }}</span>
            <span class="tag">{{ t('v2.common.readOnly') }}</span>
          </dd>
        </div>
      </dl>
    </section>

    <ErrorNotice :error="saveError" />

    <RestartNotice
      v-if="lastRestart"
      :restart="lastRestart"
      :retrying="refreshing"
      @retry="retryRead"
    />

    <ConfirmDialog
      v-if="confirmOpen"
      :title="t('v2.deviceConfig.confirmTitle')"
      :confirm-label="t('v2.deviceConfig.confirmAction')"
      :cancel-label="t('v2.deviceConfig.confirmCancel')"
      :busy="saving"
      :busy-label="t('v2.deviceConfig.saving')"
      @confirm="save"
      @cancel="closeConfirm"
    >
      <p class="note v2-confirm-lead">{{ t('v2.deviceConfig.confirmBody') }}</p>
      <dl class="v2-kv v2-confirm-kv">
        <div>
          <dt>{{ t('v2.deviceConfig.confirmScope') }}</dt>
          <dd class="mono">{{ t('v2.restart.scope.fleet') }}</dd>
        </div>
        <div>
          <dt>{{ t('v2.deviceConfig.confirmAffected') }}</dt>
          <dd class="mono">{{ detail.id }}</dd>
        </div>
      </dl>
      <p v-if="planesRunning !== null" class="note">
        {{ t('v2.deviceConfig.confirmPlanes', { n: planesRunning }) }}
      </p>
      <p v-if="!changedLabels.length" class="note">{{ t('v2.deviceConfig.noChanges') }}</p>
      <ul v-else class="v2-confirm-changes">
        <li v-for="label in changedLabels" :key="label">{{ label }}</li>
      </ul>
    </ConfirmDialog>
  </div>
</template>
