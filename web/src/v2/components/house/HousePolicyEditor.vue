<script setup lang="ts">
/**
 * Multi-device policy editor (M5, contract C-002 §7.3).
 *
 * The save is a field-level diff: only the changed `multi_device` fields are
 * sent, so an untouched `default_output`/`room_priority` is never rewritten.
 * A second confirmation states the restart truth: `PATCH /house` does not
 * restart anything and its `restart.scope` is `service`; the audio-plane
 * restart button is an explicit extra action, not part of the save.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import AppIcon from '../../../components/AppIcon.vue'
import {
  api,
  type DeviceSummary,
  type HouseInfo,
  type PatchHouseRequest,
  type Restart,
} from '../../api'
import { describeError, apiErrorCode, apiRetryAfterSeconds, type ErrorCopy } from '../../errors'
import { t, type MsgKey } from '../../i18n'
import { useCooldown } from '../../useCooldown'
import ConfirmDialog from '../ConfirmDialog.vue'
import StateBlock from '../StateBlock.vue'
import ErrorNotice from '../device/ErrorNotice.vue'
import RestartNotice from '../device/RestartNotice.vue'

const props = defineProps<{
  house: HouseInfo | null
  devices: DeviceSummary[]
  writeEnabled: boolean
}>()

const emit = defineEmits<{
  updated: [house: HouseInfo]
  restarted: []
  'dirty-change': [dirty: boolean]
}>()

interface PolicyState {
  enabled: boolean
  max_planes: number
  default_output: string | null
  room_priority: string[]
}

interface ChangeView {
  path: string
  labelKey: MsgKey
  from: string
  to: string
}

const FIELD_KEYS: Record<string, MsgKey> = {
  enabled: 'v2.policy.enabled',
  max_planes: 'v2.policy.maxPlanes',
  default_output: 'v2.policy.defaultOutput',
  room_priority: 'v2.policy.roomPriority',
}

const baseline = ref<PolicyState>({
  enabled: true,
  max_planes: 1,
  default_output: null,
  room_priority: [],
})
const enabled = ref(true)
const maxPlanes = ref('1')
const defaultOutput = ref('')
const roomPriority = ref<string[]>([])

const saving = ref(false)
const restarting = ref(false)
const confirmOpen = ref(false)
const saved = ref(false)
const restartDone = ref(false)
const saveError = ref<ErrorCopy | null>(null)
const restartError = ref<ErrorCopy | null>(null)
const lastRestart = ref<Restart | null>(null)
const submitted = ref(false)
const touched = ref<Record<string, boolean>>({})
const serverFields = ref<Record<string, string>>({})

const {
  seconds: cooldownSeconds,
  active: cooldownActive,
  start: startCooldown,
} = useCooldown()
const busy = computed(() => saving.value || restarting.value)
const canEdit = computed(() => props.writeEnabled && !busy.value && !cooldownActive.value)

let synced = false

function syncFrom(next: HouseInfo): void {
  const multi = next.multi_device
  baseline.value = {
    enabled: multi.enabled,
    max_planes: multi.max_planes,
    default_output: multi.default_output || null,
    room_priority: [...(multi.room_priority ?? [])],
  }
  enabled.value = multi.enabled
  maxPlanes.value = String(multi.max_planes)
  defaultOutput.value = multi.default_output || ''
  roomPriority.value = [...(multi.room_priority ?? [])]
  submitted.value = false
  touched.value = {}
  serverFields.value = {}
}

watch(
  () => props.house,
  (next) => {
    if (!next) return
    // A background refresh must never discard an operator's draft. The first
    // payload always syncs, even though the initial defaults look dirty.
    if (synced && (busy.value || dirty.value)) return
    syncFrom(next)
    synced = true
  },
  { immediate: true },
)

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index])
}

function deviceLabel(id: string): string {
  const device = props.devices.find((entry) => entry.id === id)
  if (!device) return id
  return `${id} · ${device.room || t('v2.devices.unassigned')}`
}

function currentDef(): string | null {
  return defaultOutput.value || null
}

function priorityLabel(list: string[]): string {
  if (!list.length) return t('v2.house.emptyList')
  return list.map(deviceLabel).join(', ')
}

const changes = computed<ChangeView[]>(() => {
  const base = baseline.value
  const list: ChangeView[] = []
  if (enabled.value !== base.enabled) {
    list.push({
      path: 'enabled',
      labelKey: FIELD_KEYS.enabled,
      from: base.enabled ? t('v2.common.yes') : t('v2.common.no'),
      to: enabled.value ? t('v2.common.yes') : t('v2.common.no'),
    })
  }
  const planes = Number(maxPlanes.value)
  if (planes !== base.max_planes) {
    list.push({
      path: 'max_planes',
      labelKey: FIELD_KEYS.max_planes,
      from: String(base.max_planes),
      to: maxPlanes.value.trim() || t('v2.house.emptyValue'),
    })
  }
  if (currentDef() !== base.default_output) {
    list.push({
      path: 'default_output',
      labelKey: FIELD_KEYS.default_output,
      from: base.default_output
        ? deviceLabel(base.default_output)
        : t('v2.house.notSet'),
      to: currentDef() ? deviceLabel(currentDef() as string) : t('v2.house.notSet'),
    })
  }
  if (!sameList(roomPriority.value, base.room_priority)) {
    list.push({
      path: 'room_priority',
      labelKey: FIELD_KEYS.room_priority,
      from: priorityLabel(base.room_priority),
      to: priorityLabel(roomPriority.value),
    })
  }
  return list
})

const dirty = computed(() => changes.value.length > 0)

// The page owns route-leave/unload guards; the editor only reports its draft.
watch(dirty, (value) => emit('dirty-change', value))
onBeforeUnmount(() => emit('dirty-change', false))

const validation = computed<Record<string, string>>(() => {
  const out: Record<string, string> = {}
  const raw = maxPlanes.value.trim()
  const planes = Number(raw)
  if (!/^\d+$/.test(raw) || planes < 1 || planes > 8) {
    out.max_planes = t('v2.policy.validation.maxPlanes')
  }
  if (props.devices.length > 0) {
    const def = currentDef()
    if (def) {
      const device = props.devices.find((entry) => entry.id === def)
      if (!device || !device.enabled) {
        out.default_output = t('v2.policy.validation.defaultOutput')
      }
    }
    if (enabled.value) {
      const missing = props.devices
        .filter((device) => device.enabled && !device.room.trim())
        .map((device) => device.id)
      if (missing.length > 0) {
        out.enabled = t('v2.policy.validation.room', { ids: missing.join(', ') })
      }
    }
    const seen = new Set<string>()
    let bad = false
    for (const id of roomPriority.value) {
      if (seen.has(id) || !props.devices.some((device) => device.id === id)) bad = true
      seen.add(id)
    }
    if (bad) out.room_priority = t('v2.policy.validation.priority')
  }
  return out
})

const validationErrors = computed(() => Object.keys(validation.value).length)

function visibleError(key: string): string {
  if (serverFields.value[key]) return serverFields.value[key]
  if (!submitted.value && !touched.value[key]) return ''
  return validation.value[key] ?? ''
}

function markTouched(key: string): void {
  touched.value = { ...touched.value, [key]: true }
  saved.value = false
  if (serverFields.value[key]) {
    const next = { ...serverFields.value }
    delete next[key]
    serverFields.value = next
  }
}

function setEnabled(event: Event): void {
  if (!canEdit.value) return
  enabled.value = (event.target as HTMLInputElement).checked
  markTouched('enabled')
}

function setMaxPlanes(event: Event): void {
  if (!canEdit.value) return
  maxPlanes.value = (event.target as HTMLInputElement).value
  markTouched('max_planes')
}

function setDefaultOutput(event: Event): void {
  if (!canEdit.value) return
  defaultOutput.value = (event.target as HTMLSelectElement).value
  markTouched('default_output')
}

const enabledDevices = computed(() => props.devices.filter((device) => device.enabled))

const defaultOptions = computed(() => {
  const list = enabledDevices.value.map((device) => ({
    id: device.id,
    label: deviceLabel(device.id),
  }))
  const current = currentDef()
  if (current && !list.some((entry) => entry.id === current)) {
    const device = props.devices.find((entry) => entry.id === current)
    list.unshift({
      id: current,
      label: `${current} · ${device?.room || t('v2.devices.unassigned')}${
        device && !device.enabled ? ` ${t('v2.policy.defaultOutputDisabled')}` : ''
      }`,
    })
  }
  return list
})

const availablePriority = computed(() =>
  props.devices.filter((device) => !roomPriority.value.includes(device.id)),
)
const priorityPick = ref('')

function addPriority(): void {
  if (!canEdit.value) return
  const id = priorityPick.value
  if (!id || roomPriority.value.includes(id)) return
  roomPriority.value = [...roomPriority.value, id]
  priorityPick.value = ''
  markTouched('room_priority')
}

function movePriority(index: number, delta: number): void {
  if (!canEdit.value) return
  const target = index + delta
  if (target < 0 || target >= roomPriority.value.length) return
  const next = [...roomPriority.value]
  const [item] = next.splice(index, 1)
  next.splice(target, 0, item)
  roomPriority.value = next
  markTouched('room_priority')
}

function removePriority(index: number): void {
  if (!canEdit.value) return
  const next = [...roomPriority.value]
  next.splice(index, 1)
  roomPriority.value = next
  markTouched('room_priority')
}

/** Used by the room cards: stage this device as the default output. */
function setDefaultOutputTo(id: string): void {
  if (!canEdit.value || !props.house) return
  defaultOutput.value = id
  markTouched('default_output')
}

defineExpose({ setDefaultOutput: setDefaultOutputTo })

function buildPatch(): PatchHouseRequest | null {
  const multi: PatchHouseRequest['multi_device'] = {}
  const base = baseline.value
  if (enabled.value !== base.enabled) multi.enabled = enabled.value
  const planes = Number(maxPlanes.value)
  if (planes !== base.max_planes) multi.max_planes = planes
  const def = currentDef()
  if (def !== base.default_output) multi.default_output = def
  if (!sameList(roomPriority.value, base.room_priority)) {
    multi.room_priority = [...roomPriority.value]
  }
  if (Object.keys(multi).length === 0) return null
  return { multi_device: multi }
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
  if (!dirty.value || validationErrors.value > 0 || !buildPatch()) return
  confirmOpen.value = true
}

function closeConfirm(): void {
  if (!saving.value) confirmOpen.value = false
}

function mapServerPath(path: string): string {
  let next = path.trim()
  if (next.startsWith('multi_device.')) next = next.slice('multi_device.'.length)
  if (next === 'arbitration.room_priority') return 'room_priority'
  return next in FIELD_KEYS ? next : ''
}

function applyServerFields(err: unknown): void {
  const copy = saveError.value ?? describeError(err)
  const map: Record<string, string> = {}
  for (const field of copy.fields) {
    const key = mapServerPath(field.path)
    if (!key) continue
    map[key] = field.message || t('v2.house.invalidField')
  }
  serverFields.value = map
  // Mapped fields are shown next to their control; only unmapped paths stay
  // in the top banner (with the exact backend path).
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
    const response = await api.patchHouse(patch)
    lastRestart.value = response.restart
    saved.value = true
    restartDone.value = false
    confirmOpen.value = false
    syncFrom(response.house)
    emit('updated', response.house)
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
  if (busy.value || !props.house) return
  syncFrom(props.house)
  saved.value = false
  restartDone.value = false
  lastRestart.value = null
  resetErrors()
  submitted.value = false
}

</script>

<template>
  <section class="v2-card v2-policy" data-testid="policy-editor">
    <div class="v2-card-head">
      <div>
        <h2 class="v2-card-title">{{ t('v2.policy.title') }}</h2>
        <p class="v2-card-lede">{{ t('v2.policy.lede') }}</p>
      </div>
      <span class="badge" :class="dirty ? 'badge-warn' : 'badge-muted'" data-testid="policy-dirty">
        {{ dirty ? t('v2.policy.dirty', { n: changes.length }) : t('v2.policy.clean') }}
      </span>
    </div>

    <div v-if="!writeEnabled && house" class="banner banner-warn v2-banner" role="status">
      <AppIcon name="lock" :size="18" />
      <div class="v2-banner-body">
        <strong>{{ t('v2.meta.featureOff') }}</strong>
        <p class="note">{{ t('v2.rooms.featureOffHint') }}</p>
      </div>
    </div>

    <ErrorNotice v-if="saveError" :error="saveError" />

    <!--
      Without `house` the setup defaults are not a policy snapshot: the form
      must stay unmounted so it can never show a fabricated enabled/max_planes.
    -->
    <StateBlock
      v-if="!house"
      kind="unavailable"
      :title="t(writeEnabled ? 'v2.rooms.houseUnavailable' : 'v2.meta.featureOff')"
      :hint="t(writeEnabled ? 'v2.rooms.houseUnavailableHint' : 'v2.rooms.featureOffHint')"
    />

    <template v-else>
      <div class="v2-house-form">
        <div class="v2-house-field">
          <label class="v2-house-label" for="policy-enabled">{{ t('v2.policy.enabled') }}</label>
          <div class="v2-house-control">
            <label class="v2-house-check">
              <input
                id="policy-enabled"
                type="checkbox"
                :checked="enabled"
                :disabled="!canEdit"
                data-testid="policy-enabled"
                :aria-invalid="Boolean(visibleError('enabled'))"
                @change="setEnabled"
              />
              <span>{{ enabled ? t('v2.common.enabled') : t('v2.common.disabled') }}</span>
            </label>
            <p class="note">{{ t('v2.policy.enabledHint') }}</p>
            <p v-if="visibleError('enabled')" class="v2-field-error" data-testid="policy-enabled-error">
              {{ visibleError('enabled') }}
            </p>
          </div>
        </div>

        <div class="v2-house-field">
          <label class="v2-house-label" for="policy-max-planes">{{ t('v2.policy.maxPlanes') }}</label>
          <div class="v2-house-control">
            <input
              id="policy-max-planes"
              class="input v2-input v2-house-input"
              :class="{ 'is-invalid': Boolean(visibleError('max_planes')) }"
              type="number"
              min="1"
              max="8"
              step="1"
              :value="maxPlanes"
              :disabled="!canEdit"
              data-testid="policy-max-planes"
              :aria-invalid="Boolean(visibleError('max_planes'))"
              @input="setMaxPlanes"
              @blur="markTouched('max_planes')"
            />
            <p class="note">{{ t('v2.policy.maxPlanesHint') }}</p>
            <p v-if="visibleError('max_planes')" class="v2-field-error" data-testid="policy-max-planes-error">
              {{ visibleError('max_planes') }}
            </p>
          </div>
        </div>

        <div class="v2-house-field">
          <label class="v2-house-label" for="policy-default-output">
            {{ t('v2.policy.defaultOutput') }}
          </label>
          <div class="v2-house-control">
            <select
              id="policy-default-output"
              class="select v2-select v2-house-select"
              :class="{ 'is-invalid': Boolean(visibleError('default_output')) }"
              :value="defaultOutput"
              :disabled="!canEdit"
              data-testid="policy-default-output"
              :aria-invalid="Boolean(visibleError('default_output'))"
              @change="setDefaultOutput"
            >
              <option value="">{{ t('v2.policy.defaultOutputNone') }}</option>
              <option v-for="option in defaultOptions" :key="option.id" :value="option.id">
                {{ option.label }}
              </option>
            </select>
            <p class="note">{{ t('v2.policy.defaultOutputHint') }}</p>
            <p v-if="visibleError('default_output')" class="v2-field-error" data-testid="policy-default-output-error">
              {{ visibleError('default_output') }}
            </p>
          </div>
        </div>

        <div class="v2-house-field">
          <span class="v2-house-label">{{ t('v2.policy.roomPriority') }}</span>
          <div class="v2-house-control">
            <p class="note">{{ t('v2.policy.roomPriorityHint') }}</p>
            <p v-if="visibleError('room_priority')" class="v2-field-error" data-testid="policy-priority-error">
              {{ visibleError('room_priority') }}
            </p>
            <p v-if="!roomPriority.length" class="empty">{{ t('v2.policy.priorityEmpty') }}</p>
            <ol v-else class="v2-priority-list" data-testid="policy-priority-list">
              <li v-for="(id, index) in roomPriority" :key="id" class="v2-priority-row">
                <span class="v2-priority-index mono">{{ index + 1 }}</span>
                <span class="v2-priority-name mono" data-testid="policy-priority-item">
                  {{ deviceLabel(id) }}
                </span>
                <button
                  type="button"
                  class="btn btn-icon btn-sm"
                  :disabled="!canEdit || index === 0"
                  :aria-label="t('v2.policy.moveUp')"
                  :title="t('v2.policy.moveUp')"
                  @click="movePriority(index, -1)"
                >
                  <AppIcon name="arrowUp" :size="14" />
                </button>
                <button
                  type="button"
                  class="btn btn-icon btn-sm"
                  :disabled="!canEdit || index === roomPriority.length - 1"
                  :aria-label="t('v2.policy.moveDown')"
                  :title="t('v2.policy.moveDown')"
                  @click="movePriority(index, 1)"
                >
                  <AppIcon name="arrowDown" :size="14" />
                </button>
                <button
                  type="button"
                  class="btn btn-icon btn-sm"
                  :disabled="!canEdit"
                  :aria-label="t('v2.policy.remove')"
                  :title="t('v2.policy.remove')"
                  @click="removePriority(index)"
                >
                  <AppIcon name="close" :size="14" />
                </button>
              </li>
            </ol>
            <div class="v2-priority-add">
              <label class="sr-only" for="policy-priority-pick">{{ t('v2.policy.priorityAddLabel') }}</label>
              <select
                id="policy-priority-pick"
                class="select v2-select v2-house-select"
                v-model="priorityPick"
                :disabled="!canEdit || !availablePriority.length"
                data-testid="policy-priority-pick"
              >
                <option value="">{{ t('v2.policy.priorityAddLabel') }}</option>
                <option v-for="device in availablePriority" :key="device.id" :value="device.id">
                  {{ deviceLabel(device.id) }}
                </option>
              </select>
              <button
                type="button"
                class="btn btn-sm"
                :disabled="!canEdit || !priorityPick"
                data-testid="policy-priority-add"
                @click="addPriority"
              >
                <AppIcon name="plus" :size="14" />
                {{ t('v2.policy.add') }}
              </button>
            </div>
            <p v-if="!devices.length" class="note">{{ t('v2.policy.noDevices') }}</p>
          </div>
        </div>
      </div>

      <div class="v2-audio-toolbar v2-house-toolbar">
        <p class="note v2-audio-toolbar-note">{{ t('v2.house.changes') }}: {{ changes.length }}</p>
        <button type="button" class="btn" :disabled="busy || !dirty" @click="discard">
          {{ t('v2.policy.discard') }}
        </button>
        <button
          type="button"
          class="btn btn-primary"
          data-testid="policy-save"
          :disabled="!canEdit || !dirty"
          @click="requestSave"
        >
          <AppIcon name="check" :size="15" />
          {{ saving ? t('v2.policy.saving') : t('v2.policy.save') }}
        </button>
      </div>

      <p v-if="cooldownActive" class="note v2-cooldown" role="status" data-testid="policy-cooldown">
        {{ t('v2.house.cooldown', { n: cooldownSeconds }) }}
      </p>

      <div v-if="saved" class="v2-house-saved" data-testid="policy-saved">
        <div class="banner banner-ok v2-banner" role="status">
          <AppIcon name="check" :size="18" />
          <div class="v2-banner-body">
            <strong>{{ t('v2.policy.saved') }}</strong>
            <p class="note">{{ t('v2.policy.savedService') }}</p>
          </div>
        </div>

        <p class="note v2-restart-command" data-testid="policy-restart-command">
          {{ t('v2.policy.restartCommand') }}
          <code class="mono">docker compose restart rui</code>
        </p>
        <p class="note">{{ t('v2.policy.restartCommandHint') }}</p>

        <template v-if="lastRestart">
          <p class="meta">{{ restartDone ? t('v2.policy.restartResult') : t('v2.policy.savedService') }}</p>
          <RestartNotice :restart="lastRestart" :show-retry="false" />
        </template>

        <p class="note">{{ t('v2.policy.restartHint') }}</p>
        <button
          type="button"
          class="btn"
          data-testid="policy-restart"
          :disabled="busy || cooldownActive"
          @click="restartAudio"
        >
          <AppIcon name="refresh" :size="14" />
          {{ restarting ? t('v2.policy.restarting') : t('v2.policy.restartNow') }}
        </button>
        <ErrorNotice v-if="restartError" :error="restartError" />
      </div>
    </template>

    <ConfirmDialog
      v-if="confirmOpen"
      :title="t('v2.policy.confirmTitle')"
      :confirm-label="t('v2.policy.confirmAction')"
      :cancel-label="t('v2.policy.confirmCancel')"
      :busy="saving"
      :busy-label="t('v2.policy.saving')"
      @confirm="save"
      @cancel="closeConfirm"
    >
      <p class="note v2-confirm-lead">{{ t('v2.policy.confirmBody') }}</p>
      <dl class="v2-kv v2-confirm-kv">
        <div>
          <dt>{{ t('v2.policy.confirmScope') }}</dt>
          <dd class="mono">{{ t('v2.policy.serviceScope') }}</dd>
        </div>
        <div>
          <dt>{{ t('v2.policy.confirmFields') }}</dt>
          <dd class="mono">{{ changes.map((change) => change.path).join(', ') }}</dd>
        </div>
      </dl>
      <ul class="v2-confirm-changes" data-testid="policy-changes">
        <li v-for="change in changes" :key="change.path">
          <span class="mono">{{ t(change.labelKey) }}</span>
          <span class="v2-audio-change">
            {{ change.from }} → {{ change.to }}
          </span>
        </li>
      </ul>
    </ConfirmDialog>
  </section>
</template>

<style scoped>
.v2-house-form {
  display: flex;
  flex-direction: column;
  gap: var(--s4);
  margin-top: var(--s3);
}

.v2-house-field {
  display: grid;
  grid-template-columns: minmax(140px, 0.42fr) minmax(0, 1fr);
  gap: var(--s3);
  padding-bottom: var(--s4);
  border-bottom: 1px solid var(--line);
}

.v2-house-label {
  font-size: 12.5px;
  font-weight: 650;
  color: var(--ink-2);
}

.v2-house-control {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.v2-house-control .note {
  margin: 0;
}

.v2-house-check {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  font-size: 13px;
  cursor: pointer;
}

.v2-house-check input {
  width: 16px;
  height: 16px;
  accent-color: var(--accent);
}

.v2-house-input,
.v2-house-select {
  max-width: 320px;
}

.v2-house-input.is-invalid,
.v2-house-select.is-invalid {
  border-color: var(--fail);
  box-shadow: 0 0 0 2px var(--fail-soft);
}

.v2-field-error {
  margin: 2px 0 0;
  color: var(--fail);
  font-size: 11.5px;
  line-height: 1.45;
}

.v2-priority-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 4px 0;
  padding: 0;
  list-style: none;
}

.v2-priority-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 6px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-2);
}

.v2-priority-index {
  width: 2ch;
  color: var(--muted);
  font-size: 11px;
}

.v2-priority-name {
  flex: 1 1 auto;
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 12.5px;
}

.v2-priority-add {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  margin-top: var(--s2);
}

.v2-house-toolbar {
  margin-top: var(--s4);
}

.v2-house-saved {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--s2);
  margin-top: var(--s4);
  padding-top: var(--s3);
  border-top: 1px solid var(--line);
}

.v2-house-saved .note {
  margin: 0;
}

.v2-restart-command code {
  display: inline-block;
  margin-left: 4px;
  padding: 1px 6px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-sm);
  background: var(--surface-2);
  font-size: 11.5px;
}

@media (max-width: 640px) {
  .v2-house-field {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--s1);
  }
}
</style>
