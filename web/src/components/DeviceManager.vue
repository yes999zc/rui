<script setup lang="ts">
/**
 * P2.4 device manager (the `devices-admin` panel's escape-hatch component).
 *
 * One page owns the whole day-after lifecycle: list what config.yaml says, show
 * live evidence next to it, add/edit through the selected plug's device manifest,
 * run the evidence-only connection test, copy the client connection details, and
 * delete with the plane/setup impact stated up front.
 *
 * Data path:
 *   GET    /api/v1/devices                  list + status/capabilities/public params
 *   GET    /api/v1/devices/{id}             editable config + live adapter report
 *   GET    /api/v1/plugs[/{id}]             plug summary + manifest (incl. params[])
 *   PUT    /api/v1/devices/{id}             edit (merge; omitted fields survive)
 *   POST   /api/v1/setup/step               create via the existing device.upsert action
 *                                           (the backend exposes no POST /devices)
 *   POST   /api/v1/devices/{id}/test        host/plane/token/capabilities evidence
 *   GET    /api/v1/devices/{id}/connect     ws_url + token (the only token endpoint)
 *   DELETE /api/v1/devices/{id}             remove + restart the audio plane
 *
 * Secret contract: manifest `secret` params are write-only. Their draft starts
 * empty on every open, is omitted when blank, is never pre-filled, logged or
 * persisted, and is cleared when the drawer closes. The connect token lives in
 * memory only while this page is mounted.
 */
import { computed, onMounted, reactive, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import {
  api,
  ApiError,
  apiErrorObject,
  type Device,
  type DeviceConnectInfo,
  type DeviceDetail,
  type DeviceStatusInfo,
  type DeviceTestCheck,
  type DeviceTestResult,
  type DeviceTokenRotation,
  type DeviceWarning,
  type SetupDeviceManifest,
  type SetupParamSpec,
  type SetupPlugDetail,
  type SetupPlugSummary,
  type ValidationIssue,
} from '../api'
import { localizeIssue, t } from '../i18n'

const RESERVED_PARAMS = new Set(['host', 'room', 'wake_words'])
const KNOWN_PARAM_TYPES = new Set(['string', 'int', 'bool', 'list', 'secret', 'choice'])
/** The built-in A-class plug ships in code; a fresh install always has it. */
const DEFAULT_PLUG_ID = 'xiaoai-lx06'

// ————————————————————————————————————————————————— state —————
const devices = ref<Device[]>([])
const plugs = ref<SetupPlugSummary[]>([])
const plugDetails = reactive<Record<string, SetupPlugDetail | undefined>>({})
const loading = ref(true)
const loadError = ref('')
const plugError = ref('')
const notice = ref('')
const actionError = ref('')

const selectedId = ref('')
const selectedDevice = ref<Device | null>(null)
const detail = ref<DeviceDetail | null>(null)
const detailBusy = ref(false)
const detailError = ref('')
let selectSeq = 0
let connectSeq = 0
let testSeq = 0
let statusSeq = 0

// P3.4 status/toggle
const statusInfo = ref<DeviceStatusInfo | null>(null)
const statusBusy = ref(false)
const statusError = ref('')
const toggleBusyId = ref('')

// token rotation (show-once dialog)
const rotateOpen = ref(false)
const rotateBusy = ref(false)
const rotateError = ref('')
const rotated = ref<DeviceTokenRotation | null>(null)
const rotateEnv = ref('')

const testBusy = ref(false)
const testResult = ref<DeviceTestResult | null>(null)
const testError = ref('')

const connectInfo = ref<DeviceConnectInfo | null>(null)
const connectBusy = ref(false)
const connectError = ref('')
const revealed = ref(false)

const copied = ref('')
const copyFailed = ref(false)

// add/edit drawer
const editorMode = ref<'create' | 'edit' | null>(null)
const formBusy = ref(false)
const formError = ref('')
const fieldErrors = reactive<Record<string, string>>({})
const form = reactive({
  id: '',
  room: '',
  host: '',
  adapterId: '',
  plugId: '',
  enabled: true,
  listenPort: '',
  wakeWords: [''] as string[],
})
const paramTexts = reactive<Record<string, string>>({})
const paramBools = reactive<Record<string, boolean>>({})
const secretDrafts = reactive<Record<string, string>>({})

// delete confirmation
const deleteTarget = ref<Device | null>(null)
const deleteBusy = ref(false)
const deleteError = ref('')

// ————————————————————————————————————————————— derived state —————
const editorOpen = computed(() => editorMode.value !== null)
const plugOptions = computed(() => plugs.value.filter((plug) => plug.id))
const availablePlugs = computed(() => plugOptions.value.filter((plug) => plug.available))
const editorPlug = computed<SetupPlugSummary | null>(
  () => plugs.value.find((plug) => plug.id === form.plugId) ?? null,
)
const editorDoc = computed<SetupPlugDetail | undefined>(() => plugDetails[form.plugId])
const editorDevices = computed<SetupDeviceManifest[]>(() => editorDoc.value?.devices ?? [])
const editorManifest = computed<SetupDeviceManifest | null>(
  () =>
    editorDevices.value.find((device) => device.id === form.adapterId) ??
    editorDevices.value[0] ??
    null,
)
const editorExtraParams = computed<SetupParamSpec[]>(
  () => (editorManifest.value?.params ?? []).filter((spec) => !RESERVED_PARAMS.has(spec.key)),
)
const editorUnknownParam = computed<SetupParamSpec | null>(
  () =>
    editorExtraParams.value.find(
      (spec) =>
        !KNOWN_PARAM_TYPES.has(String(spec.type)) ||
        (spec.type === 'choice' && !(spec.options?.length ?? 0)),
    ) ?? null,
)
const editorTitle = computed(() =>
  editorMode.value === 'create'
    ? t('dm.addTitle')
    : t('dm.editTitle', { id: form.id || selectedId.value }),
)
const currentStatus = computed(
  () => detail.value?.live?.status ?? selectedDevice.value?.status ?? 'unknown',
)
const wsDisplay = computed(() => {
  const info = connectInfo.value
  if (!info?.ws_url) return '—'
  if (revealed.value || !info.token) return info.ws_url
  // The backend percent-encodes the token in the query string, so match the
  // `token=` parameter instead of the raw value: encoded tokens must not leak.
  return info.ws_url.replace(/([?&]token=)[^&]*/i, '$1••••••••')
})
const tokenDisplay = computed(() => {
  const value = connectInfo.value?.token ?? ''
  if (!value) return '—'
  return revealed.value ? value : '••••••••••••••••'
})
const detailShape = computed(() => {
  const doc = detail.value
  if (!doc) return null
  return {
    ...doc.device,
    plug_id: doc.plug_id ?? selectedDevice.value?.plug_id ?? null,
    params: doc.params ?? doc.device.params,
    capabilities: doc.live?.capabilities ?? selectedDevice.value?.capabilities,
  }
})
const detailCapabilities = computed(() => capabilitiesFor(detailShape.value))
const detailParamRows = computed(() => paramRows(detailShape.value))
const detailEnabled = computed(
  () => detail.value?.device.enabled ?? selectedDevice.value?.enabled ?? true,
)
const multiWarning = computed<DeviceWarning | null>(() => {
  const rows = devices.value.flatMap((device) => device.warnings ?? [])
  return rows.find((warning) => warning.code === 'multiple_enabled_inbound') ?? null
})
const multiWarningBody = computed(() => {
  const details = multiWarning.value?.details ?? {}
  const active = String(details.active_device ?? '—')
  const ignored = Array.isArray(details.ignored_devices)
    ? details.ignored_devices.map(String).join(', ')
    : '—'
  return t('dm.multi.body', { active, ignored })
})
const failedChecks = computed(() =>
  (testResult.value?.checks ?? [])
    .filter((check) => !check.ok)
    .map((check) => checkLabel(check.name))
    .join(', '),
)

// ————————————————————————————————————————————— display helpers —————
function errorText(exc: unknown): string {
  return exc instanceof Error ? exc.message : String(exc)
}

function statusLabel(status: string | null | undefined): string {
  switch (status) {
    case 'ready':
      return t('dm.status.ready')
    case 'disconnected':
      return t('dm.status.disconnected')
    case 'unavailable':
      return t('dm.status.unavailable')
    case 'disabled':
      return t('dm.status.disabled')
    default:
      return t('dm.status.unknown')
  }
}

function statusClass(status: string | null | undefined): string {
  switch (status) {
    case 'ready':
      return 'badge badge-ok'
    case 'disconnected':
      return 'badge badge-fail'
    case 'unavailable':
      return 'badge badge-warn'
    case 'disabled':
      return 'badge badge-muted'
    default:
      return 'badge badge-muted'
  }
}

function trustLabel(trust: string | null | undefined): string {
  switch (trust) {
    case 'builtin':
      return t('dm.trust.builtin')
    case 'trusted':
      return t('dm.trust.trusted')
    case 'third_party':
      return t('dm.trust.thirdParty')
    default:
      return trust || '—'
  }
}

function classLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return t('dm.classValue', { value })
}

function transportLabel(value: string | null | undefined): string {
  if (!value) return '—'
  if (value === 'inbound') return t('dm.transport.inbound')
  if (value === 'outbound') return t('dm.transport.outbound')
  return value
}

function capLabel(capability: string): string {
  switch (capability) {
    case 'mic_array':
      return t('dm.cap.micArray')
    case 'reference':
      return t('dm.cap.reference')
    case 'playback':
      return t('dm.cap.playback')
    case 'led':
      return t('dm.cap.led')
    case 'keys':
      return t('dm.cap.keys')
    case 'volume':
      return t('dm.cap.volume')
    default:
      return capability
  }
}

function checkLabel(name: string): string {
  switch (name) {
    case 'host':
      return t('dm.test.check.host')
    case 'plane':
      return t('dm.test.check.plane')
    case 'token':
      return t('dm.test.check.token')
    case 'capabilities':
      return t('dm.test.check.capabilities')
    default:
      return name
  }
}

/** Stable backend status error codes -> console copy (never a raw secret). */
function statusErrorLabel(code: string): string {
  switch (code) {
    case 'device_disabled':
      return t('dm.status.err.deviceDisabled')
    case 'token_missing':
      return t('dm.status.err.tokenMissing')
    case 'adapter_unavailable':
      return t('dm.status.err.adapterUnavailable')
    case 'plug_unresolved':
      return t('dm.status.err.plugUnresolved')
    case 'audio_plane_stopped':
      return t('dm.status.err.planeStopped')
    case 'audio_plane_error':
      return t('dm.status.err.planeError')
    case 'not_connected':
      return t('dm.status.err.notConnected')
    default:
      return code
  }
}

function toggleChecked(event: Event): boolean {
  return event.target instanceof HTMLInputElement ? event.target.checked : false
}

function formatLastSeen(value: number | null | undefined): string {
  if (!value) return t('dm.status.never')
  return new Date(value * 1000).toLocaleString()
}

function toggleErrorText(exc: unknown, enabled: boolean): string {
  const object = apiErrorObject(exc)
  if (object?.code === 'port_conflict') {
    const details = object.details ?? {}
    const port = String(details.port ?? details.listen_port ?? '—')
    const ids = Array.isArray(details.device_ids)
      ? details.device_ids.map(String).join(', ')
      : '—'
    return t('dm.portConflict', { port, ids })
  }
  if (object?.code === 'invalid_values') {
    return object.message || t('dm.err.validationFailed')
  }
  return t('dm.err.toggleFailed', {
    action: enabled ? t('dm.toggle.enable') : t('dm.toggle.disable'),
    message: errorText(exc),
  })
}

function plugName(plug: SetupPlugSummary | null | undefined): string {
  return plug?.name || plug?.id || t('dm.plugUnknown')
}

function looksSecret(key: string): boolean {
  return /pass|secret|token|api_?key|credential/i.test(key)
}

function formatParamValue(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).join(', ')
  if (typeof value === 'boolean') return value ? t('common.on') : t('common.off')
  if (value && typeof value === 'object') {
    try {
      return JSON.stringify(value)
    } catch {
      return '—'
    }
  }
  return value == null || value === '' ? '—' : String(value)
}

function manifestFor(device: {
  adapter?: string
  plug_id?: string | null
  params?: Record<string, unknown>
} | null): SetupDeviceManifest | null {
  if (!device) return null
  const plugId = String(device.plug_id ?? device.params?.plug_id ?? '')
  const doc = plugDetails[plugId]
  const list = doc?.devices ?? []
  return list.find((item) => item.id === device.adapter) ?? list[0] ?? null
}

function plugForDevice(device: {
  plug_id?: string | null
  params?: Record<string, unknown>
}): SetupPlugSummary | null {
  const id = String(device.plug_id ?? device.params?.plug_id ?? '')
  return plugs.value.find((plug) => plug.id === id) ?? null
}

interface ParamRow {
  key: string
  label: string
  value: string
  masked: boolean
}

/**
 * Plug manifests are third-party data; their label is already human copy.
 * Built-in keys get a translated fallback, and an unlabelled key never becomes
 * the main label (it stays as a small technical hint instead).
 */
function paramLabel(key: string, manifestLabel?: string): string {
  if (manifestLabel) return manifestLabel
  switch (key) {
    case 'volume_max':
      return t('dm.param.volumeMax')
    case 'mic_gain_db':
      return t('dm.param.micGain')
    case 'listen_port':
      return t('dm.param.listenPort')
    default:
      return t('dm.param.other')
  }
}

function paramRows(device: {
  adapter?: string
  plug_id?: string | null
  params?: Record<string, unknown>
} | null): ParamRow[] {
  if (!device) return []
  const manifest = manifestFor(device)
  const stored = (device.params ?? {}) as Record<string, unknown>
  const rows: ParamRow[] = []
  const seen = new Set<string>()
  for (const spec of manifest?.params ?? []) {
    if (RESERVED_PARAMS.has(spec.key) || spec.key === 'plug_id') continue
    seen.add(spec.key)
    if (spec.type === 'secret' || looksSecret(spec.key)) {
      rows.push({ key: spec.key, label: paramLabel(spec.key, spec.label), value: '••••', masked: true })
      continue
    }
    if (stored[spec.key] === undefined) continue
    rows.push({
      key: spec.key,
      label: paramLabel(spec.key, spec.label),
      value: formatParamValue(stored[spec.key]),
      masked: false,
    })
  }
  for (const key of Object.keys(stored)) {
    if (seen.has(key) || RESERVED_PARAMS.has(key) || key === 'plug_id') continue
    rows.push({
      key,
      label: paramLabel(key),
      value: looksSecret(key) ? '••••' : formatParamValue(stored[key]),
      masked: looksSecret(key),
    })
  }
  return rows
}

function paramSummary(device: {
  adapter?: string
  plug_id?: string | null
  params?: Record<string, unknown>
} | null): string {
  const rows = paramRows(device)
  if (!rows.length) return t('dm.params.none')
  return rows.map((row) => `${row.label}: ${row.value}`).join(' · ')
}

function capabilitiesFor(device: {
  adapter?: string
  plug_id?: string | null
  params?: Record<string, unknown>
  capabilities?: string[]
} | null): string[] {
  if (!device) return []
  const caps = new Set(device.capabilities ?? [])
  for (const capability of manifestFor(device)?.capabilities ?? []) caps.add(capability)
  return [...caps].sort()
}

// ————————————————————————————————————————————— data loading —————
async function loadPlugs(): Promise<void> {
  plugError.value = ''
  try {
    const response = await api.plugs()
    plugs.value = Array.isArray(response.plugs) ? response.plugs : []
    await Promise.all(
      plugOptions.value.map(async (plug) => {
        if (!plug.id || plugDetails[plug.id]) return
        try {
          plugDetails[plug.id] = await api.plug(plug.id)
        } catch {
          // A broken/unavailable manifest is already shown from the list row; the
          // editor can still render its identity fields.
        }
      }),
    )
  } catch (exc) {
    plugError.value = errorText(exc)
  }
}

async function loadDevices(): Promise<void> {
  const list = await api.devices()
  devices.value = Array.isArray(list) ? list : []
  if (!selectedId.value) return
  const next = devices.value.find((device) => device.id === selectedId.value) ?? null
  selectedDevice.value = next
  if (!next) selectedId.value = ''
}

async function init(): Promise<void> {
  loading.value = true
  loadError.value = ''
  await Promise.all([
    loadPlugs(),
    loadDevices().catch((exc: unknown) => {
      loadError.value = errorText(exc)
    }),
  ])
  loading.value = false
  if (!selectedId.value && devices.value.length) await select(devices.value[0].id)
}

async function select(id: string): Promise<void> {
  const seq = ++selectSeq
  selectedId.value = id
  selectedDevice.value = devices.value.find((device) => device.id === id) ?? null
  detail.value = null
  detailBusy.value = true
  detailError.value = ''
  testResult.value = null
  testError.value = ''
  connectInfo.value = null
  connectError.value = ''
  revealed.value = false
  statusInfo.value = null
  statusError.value = ''
  testSeq += 1
  statusSeq += 1
  testBusy.value = false
  statusBusy.value = false
  void loadStatus(id)
  try {
    const next = await api.deviceDetail(id)
    if (seq !== selectSeq) return
    detail.value = next
    selectedDevice.value = devices.value.find((device) => device.id === id) ?? selectedDevice.value
    await loadConnect(id)
  } catch (exc) {
    if (seq !== selectSeq) return
    detail.value = null
    detailError.value = errorText(exc)
  } finally {
    if (seq === selectSeq) detailBusy.value = false
  }
}

async function refreshListAfterWrite(): Promise<string> {
  try {
    await loadDevices()
    return ''
  } catch (exc) {
    return errorText(exc)
  }
}

async function reloadSelected(): Promise<void> {
  try {
    await loadDevices()
    if (selectedId.value) {
      await select(selectedId.value)
      notice.value = t('dm.reloaded')
    }
  } catch (exc) {
    actionError.value = t('dm.err.loadFailed', { message: errorText(exc) })
  }
}

async function loadConnect(id: string): Promise<void> {
  const seq = ++connectSeq
  connectBusy.value = true
  connectError.value = ''
  try {
    const info = await api.deviceConnect(id)
    if (seq !== connectSeq) return
    connectInfo.value = info
    revealed.value = false
  } catch (exc) {
    if (seq !== connectSeq) return
    connectInfo.value = null
    connectError.value = errorText(exc)
  } finally {
    if (seq === connectSeq) connectBusy.value = false
  }
}

/** P3.4 status: evidence plus stable error codes; never blocks the detail page. */
async function loadStatus(id: string): Promise<void> {
  const seq = ++statusSeq
  statusBusy.value = true
  statusError.value = ''
  try {
    const next = await api.deviceStatus(id)
    if (seq !== statusSeq) return
    statusInfo.value = next
  } catch (exc) {
    if (seq !== statusSeq) return
    statusInfo.value = null
    statusError.value = errorText(exc)
  } finally {
    if (seq === statusSeq) statusBusy.value = false
  }
}

async function copyText(value: string | null | undefined, key: string): Promise<void> {
  if (!value) return
  copyFailed.value = false
  try {
    await navigator.clipboard.writeText(value)
    copied.value = key
    window.setTimeout(() => {
      if (copied.value === key) copied.value = ''
    }, 2000)
  } catch {
    copyFailed.value = true
    window.setTimeout(() => {
      copyFailed.value = false
    }, 2500)
  }
}

function copyCheck(check: DeviceTestCheck): void {
  void copyText(
    JSON.stringify({ name: check.name, ok: check.ok, detail: check.detail }, null, 2),
    `check:${check.name}`,
  )
}

function copyTestResult(): void {
  if (!testResult.value) return
  void copyText(JSON.stringify(testResult.value, null, 2), 'test:all')
}

// ————————————————————————————————————————————— connection test —————
async function runTest(): Promise<void> {
  const id = detail.value?.device.id ?? selectedId.value
  if (!id || testBusy.value) return
  const seq = ++testSeq
  testBusy.value = true
  testResult.value = null
  testError.value = ''
  actionError.value = ''
  try {
    const result = await api.deviceTest(id)
    if (seq !== testSeq) return
    testResult.value = result
  } catch (exc) {
    if (seq !== testSeq) return
    testError.value = errorText(exc)
  } finally {
    if (seq === testSeq) testBusy.value = false
  }
}

// ————————————————————————————————————————————— enable / disable —————
async function setEnabled(id: string, enabled: boolean): Promise<void> {
  if (!id || toggleBusyId.value) return
  toggleBusyId.value = id
  actionError.value = ''
  try {
    const updated = await api.setDeviceEnabled(id, enabled)
    notice.value = enabled
      ? t('dm.enabledNotice', { id })
      : t('dm.disabledNotice', { id })
    if (updated.plane_error) {
      actionError.value = t('dm.planeError', { error: updated.plane_error })
    }
    if (selectedId.value === id) {
      detail.value = updated
      void loadStatus(id)
      void loadConnect(id)
    }
    const listError = await refreshListAfterWrite()
    if (listError) actionError.value = t('dm.err.loadFailed', { message: listError })
  } catch (exc) {
    actionError.value = toggleErrorText(exc, enabled)
    // A failed toggle must not leave the switch showing the un-saved state.
    await loadDevices().catch(() => undefined)
    if (selectedId.value === id) void loadStatus(id)
  } finally {
    toggleBusyId.value = ''
  }
}

// ————————————————————————————————————————————— token rotation —————
function openRotate(): void {
  const id = detail.value?.device.id ?? selectedId.value
  if (!id) return
  rotateOpen.value = true
  rotateBusy.value = false
  rotateError.value = ''
  rotated.value = null
  rotateEnv.value = detail.value?.token_env
    ?? selectedDevice.value?.token_env
    ?? connectInfo.value?.token_env
    ?? 'RUI_DEVICE_TOKEN'
}

function closeRotate(): void {
  if (rotateBusy.value) return
  rotateOpen.value = false
  rotateError.value = ''
  rotated.value = null
}

async function confirmRotate(): Promise<void> {
  const id = detail.value?.device.id ?? selectedId.value
  if (!id || rotateBusy.value) return
  rotateBusy.value = true
  rotateError.value = ''
  try {
    const result = await api.rotateDeviceToken(id)
    rotated.value = result
    void loadStatus(id)
    void loadConnect(id)
    notice.value = t('dm.rotate.doneNotice', { id })
  } catch (exc) {
    const object = apiErrorObject(exc)
    if (object?.code === 'token_from_environment') {
      rotateError.value = t('dm.rotate.envError')
    } else if (object?.code === 'rate_limited') {
      const retry = object.details?.retry_after_s
      rotateError.value = t('dm.rotate.rateLimited', { seconds: String(retry ?? '—') })
    } else {
      rotateError.value = t('dm.rotate.error', { message: errorText(exc) })
    }
  } finally {
    rotateBusy.value = false
  }
}

function clearFieldErrors(): void {
  for (const key of Object.keys(fieldErrors)) delete fieldErrors[key]
}

function clearParamDrafts(): void {
  for (const key of Object.keys(paramTexts)) delete paramTexts[key]
  for (const key of Object.keys(paramBools)) delete paramBools[key]
  for (const key of Object.keys(secretDrafts)) delete secretDrafts[key]
}

function defaultText(spec: SetupParamSpec): string {
  if (spec.type === 'list') {
    return Array.isArray(spec.default) ? spec.default.map(String).join(', ') : ''
  }
  if (spec.type === 'bool') return ''
  return spec.default === null || spec.default === undefined ? '' : String(spec.default)
}

function defaultWakeWords(manifest: SetupDeviceManifest | null): string[] {
  const spec = manifest?.params?.find((item) => item.key === 'wake_words')
  if (spec && Array.isArray(spec.default) && spec.default.length) {
    return spec.default.map(String).slice(0, 3)
  }
  return ['你好小瑞']
}

function initParamDrafts(
  manifest: SetupDeviceManifest | null,
  stored: Record<string, unknown> | undefined,
): void {
  clearParamDrafts()
  for (const spec of manifest?.params ?? []) {
    if (RESERVED_PARAMS.has(spec.key) || spec.key === 'plug_id') continue
    if (spec.type === 'secret') {
      secretDrafts[spec.key] = ''
      continue
    }
    if (spec.type === 'bool') {
      const raw = stored?.[spec.key]
      paramBools[spec.key] = raw === undefined ? spec.default === true : Boolean(raw)
      continue
    }
    const raw = stored?.[spec.key]
    if (raw === undefined || raw === null) {
      paramTexts[spec.key] = defaultText(spec)
    } else if (spec.type === 'list') {
      paramTexts[spec.key] = Array.isArray(raw) ? raw.map(String).join(', ') : String(raw)
    } else {
      paramTexts[spec.key] = String(raw)
    }
  }
}

function applyPlugSelection(
  plugId: string,
  stored: Record<string, unknown> | undefined,
  options: { resetWake?: boolean; keepAdapter?: boolean } = {},
): void {
  form.plugId = plugId
  const list = plugDetails[plugId]?.devices ?? []
  if (!options.keepAdapter || !list.some((device) => device.id === form.adapterId)) {
    form.adapterId = list[0]?.id ?? ''
  }
  const manifest =
    list.find((device) => device.id === form.adapterId) ?? list[0] ?? null
  initParamDrafts(manifest, stored)
  if (options.resetWake) form.wakeWords = defaultWakeWords(manifest)
}

function defaultPlugId(): string {
  if (availablePlugs.value.some((plug) => plug.id === DEFAULT_PLUG_ID)) return DEFAULT_PLUG_ID
  return availablePlugs.value[0]?.id ?? ''
}

function openCreate(): void {
  clearFieldErrors()
  formError.value = ''
  form.id = 'living'
  form.room = ''
  form.host = ''
  form.adapterId = ''
  form.enabled = true
  form.listenPort = ''
  form.wakeWords = ['']
  editorMode.value = 'create'
  const plugId = defaultPlugId()
  applyPlugSelection(plugId, undefined, { resetWake: true })
  const roomSpec = editorManifest.value?.params?.find((spec) => spec.key === 'room')
  form.room = roomSpec?.default == null ? '' : String(roomSpec.default)
}

function resolvePlugIdFor(device: {
  adapter: string
  plug_id?: string | null
  params?: Record<string, unknown>
}): string {
  const explicit = String(device.plug_id ?? device.params?.plug_id ?? '')
  if (explicit && plugs.value.some((plug) => plug.id === explicit)) return explicit
  const match = plugs.value.find((plug) => plug.devices.includes(device.adapter))
  return match?.id ?? ''
}

/** From the list: fetch the full document before prefilling wake words/enabled. */
async function editDevice(id: string): Promise<void> {
  await select(id)
  if (detail.value) openEdit()
}

function openEdit(): void {
  const doc = detail.value?.device ?? null
  const summary = selectedDevice.value
  const base = doc ?? summary
  if (!base) return
  clearFieldErrors()
  formError.value = ''
  form.id = base.id
  form.room = base.room
  form.host = base.host
  form.adapterId = base.adapter
  form.enabled = doc ? Boolean(doc.enabled) : true
  form.listenPort = doc?.listen_port == null ? '' : String(doc.listen_port)
  form.wakeWords = doc && Array.isArray(doc.wake_words) && doc.wake_words.length
    ? [...doc.wake_words].slice(0, 3)
    : ['']
  const plugId = resolvePlugIdFor(base)
  applyPlugSelection(plugId, base.params ?? {}, { resetWake: false, keepAdapter: true })
  if (!plugId) form.adapterId = base.adapter
  editorMode.value = 'edit'
}

function closeEditor(force = false): void {
  if (formBusy.value && !force) return
  editorMode.value = null
  formError.value = ''
  clearFieldErrors()
  clearParamDrafts()
  form.wakeWords = ['']
  form.plugId = ''
  form.adapterId = ''
  form.listenPort = ''
}

function handlePlugChange(): void {
  applyPlugSelection(form.plugId, undefined, { resetWake: true })
}

function snapshotParamDrafts(): Record<string, unknown> {
  const stored: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(paramTexts)) {
    if (value.trim()) stored[key] = value
  }
  for (const [key, value] of Object.entries(paramBools)) stored[key] = value
  return stored
}

function handleAdapterChange(): void {
  applyPlugSelection(form.plugId, snapshotParamDrafts(), { resetWake: false, keepAdapter: true })
}

function cleanWakeWords(): string[] {
  return form.wakeWords.map((word) => word.trim()).filter(Boolean)
}

function addWakeWord(): void {
  if (form.wakeWords.length >= 3) return
  form.wakeWords = [...form.wakeWords, '']
}

function removeWakeWord(index: number): void {
  if (form.wakeWords.length <= 1) return
  form.wakeWords = form.wakeWords.filter((_, at) => at !== index)
}

function splitList(value: string): string[] {
  return value
    .split(/[\n,，;；]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function validateForm(): boolean {
  clearFieldErrors()
  let ok = true
  const setError = (key: string, message: string): void => {
    fieldErrors[key] = message
    ok = false
  }
  if (editorMode.value === 'create') {
    const id = form.id.trim().toLowerCase()
    if (!id) setError('id', t('dm.err.required'))
    else if (!/^[a-z0-9_-]{1,32}$/.test(id)) setError('id', t('dm.err.id'))
    else if (devices.value.some((device) => device.id === id)) setError('id', t('dm.err.idExists'))
    if (!form.plugId) setError('plugId', t('dm.err.plugRequired'))
  }
  const host = form.host.trim()
  if (!host) setError('host', t('dm.err.required'))
  else if (
    host.includes('://') ||
    host.length > 64 ||
    /\s/.test(host) ||
    host.startsWith('-') ||
    host.endsWith('-')
  ) {
    setError('host', t('dm.err.host'))
  }
  if (form.room.trim().length > 32 || /[\u0000-\u001f]/.test(form.room)) {
    setError('room', t('dm.err.room'))
  }
  const rawPort = form.listenPort.trim()
  let port = 4412
  if (rawPort) {
    if (!/^\d+$/.test(rawPort) || Number(rawPort) < 1 || Number(rawPort) > 65535) {
      setError('listen_port', t('dm.err.port'))
    } else {
      port = Number(rawPort)
    }
  }
  if (!fieldErrors.listen_port && form.enabled) {
    const selfId = form.id.trim().toLowerCase()
    const clash = devices.value.find(
      (device) =>
        device.id !== selfId &&
        device.enabled !== false &&
        device.transport !== 'outbound' &&
        (device.listen_port ?? 4412) === port,
    )
    if (clash) {
      setError(
        'listen_port',
        t('dm.err.portConflictInline', { id: clash.id, port }),
      )
    }
  }
  const words = cleanWakeWords()
  if (!words.length || words.length > 3) {
    setError('wake_words', t('dm.err.wakeCount'))
  } else {
    for (const word of words) {
      if (word.length > 12 || /\s/u.test(word)) {
        setError('wake_words', t('dm.err.wakeWord'))
        break
      }
    }
  }
  for (const spec of editorExtraParams.value) {
    const key = `param:${spec.key}`
    if (spec.type === 'secret') {
      if (
        editorMode.value === 'create' &&
        spec.required &&
        !(secretDrafts[spec.key] ?? '').trim()
      ) {
        setError(key, t('dm.err.paramRequired'))
      }
      continue
    }
    if (spec.type === 'bool') continue
    const raw = String(paramTexts[spec.key] ?? '').trim()
    if (spec.required && !raw) {
      setError(key, t('dm.err.paramRequired'))
      continue
    }
    if (!raw) continue
    if (spec.type === 'int' && !/^-?\d+$/.test(raw)) setError(key, t('dm.err.int'))
    if (spec.type === 'choice' && !(spec.options ?? []).includes(raw)) {
      setError(key, t('dm.err.choice'))
    }
    if (spec.type === 'list' && spec.max != null && splitList(raw).length > spec.max) {
      setError(key, t('dm.err.listMax', { n: spec.max }))
    }
  }
  if (editorUnknownParam.value) {
    formError.value = t('dm.err.unknownType', {
      key: editorUnknownParam.value.key,
      type: String(editorUnknownParam.value.type),
    })
    ok = false
  } else if (!ok) {
    formError.value = t('dm.err.validationFailed')
  }
  return ok
}

function buildParamPayload(): Record<string, unknown> {
  const params: Record<string, unknown> = {}
  if (form.plugId) params.plug_id = form.plugId
  for (const spec of editorExtraParams.value) {
    const key = spec.key
    if (spec.type === 'secret') {
      const value = (secretDrafts[key] ?? '').trim()
      if (value) params[key] = value
      continue
    }
    if (spec.type === 'bool') {
      params[key] = Boolean(paramBools[key])
      continue
    }
    const raw = String(paramTexts[key] ?? '').trim()
    if (!raw) continue
    if (spec.type === 'int') {
      const parsed = Number.parseInt(raw, 10)
      params[key] = Number.isFinite(parsed) ? parsed : 0
    } else if (spec.type === 'list') {
      params[key] = splitList(raw)
    } else {
      params[key] = raw
    }
  }
  return params
}

function issueFieldKey(loc: (string | number)[] | undefined): string {
  const parts = (loc ?? []).map((part) => String(part))
  const paramsAt = parts.indexOf('params')
  if (paramsAt >= 0) {
    const key = parts
      .slice(paramsAt + 1)
      .find((part) => /^[a-z][a-z0-9_]*$/.test(part))
    if (key) return `param:${key}`
  }
  for (let at = parts.length - 1; at >= 0; at -= 1) {
    if (['host', 'room', 'wake_words', 'id', 'adapter', 'enabled', 'listen_port'].includes(parts[at])) {
      return parts[at]
    }
  }
  return ''
}

function applyIssues(issues: ValidationIssue[]): number {
  let applied = 0
  for (const issue of issues) {
    const key = issueFieldKey(issue.loc)
    const message = localizeIssue(issue.msg)
    if (key) {
      fieldErrors[key] = message
      applied += 1
    }
  }
  return applied
}

/** Map a 422 (PUT list, setup object, or loose string) onto individual inputs. */
function applyFormError(exc: unknown): void {
  formError.value = ''
  const object = apiErrorObject(exc)
  if (object) {
    if (object.code === 'port_conflict') {
      const details = object.details ?? {}
      const port = String(details.port ?? details.listen_port ?? '—')
      const ids = Array.isArray(details.device_ids)
        ? details.device_ids.map(String).join(', ')
        : '—'
      fieldErrors.listen_port = t('dm.err.portConflictInline', { id: ids, port })
      formError.value = t('dm.portConflict', { port, ids })
      return
    }
    let applied = 0
    const details = object.details ?? {}
    if (Array.isArray(details.errors)) {
      applied += applyIssues(details.errors as ValidationIssue[])
    }
    if (Array.isArray(details.missing)) {
      for (const key of details.missing) {
        if (typeof key === 'string') {
          fieldErrors[`param:${key}`] = t('dm.err.paramRequired')
          applied += 1
        }
      }
    }
    if (typeof details.param === 'string') {
      fieldErrors[`param:${details.param}`] =
        localizeIssue(String(object.message || '')) || t('dm.err.paramRequired')
      applied += 1
    }
    formError.value = applied
      ? t('dm.err.validationFailed')
      : t('dm.err.saveFailed', { message: object.message || errorText(exc) })
    return
  }
  if (exc instanceof ApiError && Array.isArray(exc.detail)) {
    const applied = applyIssues(exc.detail as ValidationIssue[])
    formError.value = applied
      ? t('dm.err.validationFailed')
      : t('dm.err.saveFailed', { message: exc.message })
    return
  }
  formError.value = t('dm.err.saveFailed', { message: errorText(exc) })
}

async function saveForm(): Promise<void> {
  if (formBusy.value) return
  formError.value = ''
  actionError.value = ''
  if (!validateForm()) return
  const params = buildParamPayload()
  const host = form.host.trim()
  const room = form.room.trim()
  const words = cleanWakeWords()
  const adapter = form.adapterId || editorManifest.value?.id || ''
  const listenPort = form.listenPort.trim() ? Number(form.listenPort.trim()) : null
  formBusy.value = true
  try {
    if (editorMode.value === 'create') {
      const id = form.id.trim().toLowerCase()
      const plug = form.plugId
      const next = await api.setupStep({
        plug,
        step: 'params',
        action: 'device.upsert',
        values: {
          id, host, room, wake_words: words, listen_port: listenPort,
          params, ...(adapter ? { adapter } : {}),
        },
      })
      const listError = await refreshListAfterWrite()
      selectedId.value = id
      await select(id)
      notice.value = t('dm.created', { id })
      if (next.plane_error) {
        actionError.value = t('dm.createPlaneError', { error: next.plane_error })
      } else if (listError) {
        actionError.value = t('dm.err.loadFailed', { message: listError })
      }
    } else {
      const id = detail.value?.device.id ?? selectedId.value
      if (!id) return
      const patch: Record<string, unknown> = {
        host,
        room,
        wake_words: words,
        enabled: form.enabled,
        listen_port: listenPort,
        params,
        ...(adapter ? { adapter } : {}),
      }
      const updated = await api.updateDevice(id, patch)
      detail.value = updated
      testResult.value = null
      testError.value = ''
      const listError = await refreshListAfterWrite()
      selectedId.value = id
      void loadStatus(id)
      await loadConnect(id)
      notice.value = t('dm.updated', { id })
      if (updated.plane_error) {
        actionError.value = t('dm.planeError', { error: updated.plane_error })
      } else if (listError) {
        actionError.value = t('dm.err.loadFailed', { message: listError })
      }
    }
    formBusy.value = false
    closeEditor(true)
  } catch (exc) {
    applyFormError(exc)
  } finally {
    formBusy.value = false
  }
}

// ————————————————————————————————————————————— delete —————
function askDelete(device: Device): void {
  deleteTarget.value = device
  deleteError.value = ''
}

/** The detail endpoint can return a device the list has not caught up with. */
function askDeleteSelected(): void {
  if (selectedDevice.value) {
    askDelete(selectedDevice.value)
    return
  }
  const device = detail.value?.device
  if (!device) return
  askDelete({
    id: device.id,
    adapter: device.adapter,
    model: '',
    room: device.room,
    host: device.host,
    transport: '',
    status: currentStatus.value,
    capabilities: [],
    missing_for_barge_in: [],
  })
}

async function confirmDelete(): Promise<void> {
  const target = deleteTarget.value
  if (!target || deleteBusy.value) return
  deleteBusy.value = true
  deleteError.value = ''
  actionError.value = ''
  try {
    const result = await api.deleteDevice(target.id)
    deleteTarget.value = null
    notice.value = t('dm.deleted', { id: target.id })
    if (result.plane_error) {
      actionError.value = t('dm.deletePlaneError', { error: result.plane_error })
    }
    await loadDevices()
    if (selectedId.value === target.id) {
      selectedId.value = ''
      detail.value = null
      selectedDevice.value = null
      connectInfo.value = null
      testResult.value = null
      if (devices.value.length) await select(devices.value[0].id)
    }
  } catch (exc) {
    deleteError.value = t('dm.err.deleteFailed', { message: errorText(exc) })
  } finally {
    deleteBusy.value = false
  }
}

onMounted(() => {
  void init()
})
</script>

<template>
  <p class="lede dm-intro">{{ t('dm.lede') }}</p>

  <p v-if="plugError" class="field-hint fail" role="status">{{ t('dm.plugLoadError', { message: plugError }) }}</p>
  <div v-if="copyFailed" class="banner banner-warn dm-banner" role="status">
    <AppIcon name="alert" :size="16" class="banner-icon" />
    <span>{{ t('dm.copyFailed') }}</span>
  </div>
  <div v-if="notice" class="banner banner-ok dm-banner" role="status">
    <AppIcon name="check" :size="16" class="banner-icon" />
    <span>{{ notice }}</span>
  </div>
  <div v-if="actionError" class="banner banner-warn dm-banner" role="alert">
    <AppIcon name="alert" :size="16" class="banner-icon" />
    <span>{{ actionError }}</span>
  </div>
  <div v-if="multiWarning" class="banner banner-warn dm-banner" role="status">
    <AppIcon name="alert" :size="16" class="banner-icon" />
    <div>
      <strong>{{ t('dm.multi.title') }}</strong>
      <p>{{ multiWarningBody }}</p>
    </div>
  </div>

  <!-- list ---------------------------------------------------------------- -->
  <section class="block">
    <div class="block-head">
      <h2>{{ t('dm.listTitle') }}</h2>
      <div class="actions">
        <button type="button" class="btn btn-sm" :disabled="detailBusy || formBusy" @click="reloadSelected">
          <AppIcon name="refresh" :size="13" />
          {{ t('common.refresh') }}
        </button>
        <button type="button" class="btn btn-sm btn-primary" @click="openCreate">
          <AppIcon name="plus" :size="13" />
          {{ t('dm.add') }}
        </button>
      </div>
    </div>

    <p v-if="loading" class="field-hint" role="status">{{ t('dm.loading') }}</p>
    <div v-else-if="loadError" class="banner banner-fail" role="alert">
      <AppIcon name="alert" :size="16" class="banner-icon" />
      <div>
        <p>{{ t('dm.err.loadFailed', { message: loadError }) }}</p>
        <button type="button" class="btn btn-sm" style="margin-top: 8px" @click="init">
          {{ t('common.retry') }}
        </button>
      </div>
    </div>
    <div v-else-if="!devices.length" class="empty">{{ t('dm.empty') }}</div>

    <div v-else class="dm-list">
      <article
        v-for="device in devices"
        :key="device.id"
        class="dm-card"
        :class="{ active: selectedId === device.id }"
      >
        <div
          class="dm-card-main"
          role="button"
          tabindex="0"
          :aria-pressed="selectedId === device.id"
          @click="select(device.id)"
          @keydown.enter.prevent="select(device.id)"
          @keydown.space.prevent="select(device.id)"
        >
          <div class="dm-card-head">
            <span class="dm-id mono">{{ device.id }}</span>
            <span class="dm-room">{{ device.room || t('dm.roomUnset') }}</span>
            <span :class="statusClass(device.status)">{{ statusLabel(device.status) }}</span>
          </div>
          <div class="dm-meta mono">
            <span>{{ device.adapter }}</span>
            <span v-if="device.model">{{ device.model }}</span>
            <span>{{ plugName(plugForDevice(device)) }}</span>
            <span v-if="plugForDevice(device)?.class">{{ classLabel(plugForDevice(device)?.class) }}</span>
            <span v-if="plugForDevice(device)?.trust">{{ trustLabel(plugForDevice(device)?.trust) }}</span>
          </div>
          <div class="dm-chips">
            <span v-if="device.active" class="dm-chip accent">{{ t('dm.activeChip') }}</span>
            <span class="dm-chip">{{ t('dm.portChip', { port: device.listen_port ?? 4412 }) }}</span>
            <span class="dm-chip" :class="{ muted: !device.token_configured }">
              {{ device.token_configured ? t('dm.token.configured') : t('dm.token.missing') }}
            </span>
            <span v-if="!capabilitiesFor(device).length" class="dm-chip muted">{{ t('dm.cap.none') }}</span>
            <span v-for="capability in capabilitiesFor(device)" :key="capability" class="dm-chip">
              {{ capLabel(capability) }}
            </span>
          </div>
          <p class="dm-param-line">
            <span class="dm-label">{{ t('dm.params') }}</span>
            <span class="mono dm-param-text">{{ paramSummary(device) }}</span>
          </p>
        </div>
        <div class="dm-card-actions">
          <label class="switch dm-card-switch" @click.stop>
            <input
              type="checkbox"
              :checked="device.enabled !== false"
              :disabled="toggleBusyId === device.id"
              :aria-label="device.enabled !== false ? t('dm.toggle.disable') : t('dm.toggle.enable')"
              @change="setEnabled(device.id, toggleChecked($event))"
            />
            <span class="track" />
            <span class="switch-text">
              {{ toggleBusyId === device.id ? t('dm.toggle.saving') : (device.enabled !== false ? t('common.enabled') : t('common.disabled')) }}
            </span>
          </label>
          <button type="button" class="btn btn-sm" @click.stop="editDevice(device.id)">
            {{ t('dm.edit') }}
          </button>
          <button type="button" class="btn btn-sm btn-danger" @click.stop="askDelete(device)">
            <AppIcon name="trash" :size="12" />
            {{ t('dm.delete') }}
          </button>
        </div>
      </article>
    </div>
  </section>

  <!-- detail -------------------------------------------------------------- -->
  <section v-if="selectedId" class="block dm-detail">
    <div v-if="detailBusy && !detail" class="field-hint" role="status">{{ t('dm.detailLoading') }}</div>
    <div v-else-if="detailError" class="banner banner-fail" role="alert">
      <AppIcon name="alert" :size="16" class="banner-icon" />
      <span>{{ t('dm.err.detailFailed', { message: detailError }) }}</span>
    </div>
    <template v-else-if="detail">
      <div class="block-head">
        <h2>{{ t('dm.detailTitle', { id: detail.device.id }) }}</h2>
        <span :class="statusClass(currentStatus)">{{ statusLabel(currentStatus) }}</span>
      </div>

      <div v-if="detail.plane_error" class="banner banner-warn dm-inline-banner" role="alert">
        <AppIcon name="alert" :size="16" class="banner-icon" />
        <span>{{ t('dm.planeError', { error: detail.plane_error }) }}</span>
      </div>

      <div class="dm-subsection dm-status">
        <div class="dm-subhead-row">
          <h3 class="dm-subhead">{{ t('dm.status.title') }}</h3>
          <div class="actions">
            <span
              v-if="statusInfo"
              :class="statusInfo.ready ? 'badge badge-ok' : 'badge badge-warn'"
            >
              {{ statusInfo.ready ? t('dm.status.ready') : t('dm.status.notReady') }}
            </span>
            <label class="switch">
              <input
                type="checkbox"
                :checked="detailEnabled"
                :disabled="toggleBusyId === detail.device.id"
                :aria-label="detailEnabled ? t('dm.toggle.disable') : t('dm.toggle.enable')"
                @change="setEnabled(detail.device.id, toggleChecked($event))"
              />
              <span class="track" />
              <span class="switch-text">
                {{ toggleBusyId === detail.device.id ? t('dm.toggle.saving') : (detailEnabled ? t('common.enabled') : t('common.disabled')) }}
              </span>
            </label>
          </div>
        </div>
        <p v-if="statusBusy" class="field-hint" role="status">{{ t('dm.status.loading') }}</p>
        <p v-else-if="statusError" class="field-hint fail" role="status">
          {{ t('dm.status.loadError', { message: statusError }) }}
        </p>
        <template v-else-if="statusInfo">
          <div class="dm-facts dm-status-facts">
            <div class="dm-fact">
              <span class="dm-label">{{ t('dm.status.token') }}</span>
              <span :class="statusInfo.token_configured ? 'badge badge-ok' : 'badge badge-warn'">
                {{ statusInfo.token_configured ? t('dm.connect.configured') : t('dm.connect.notConfigured') }}
              </span>
            </div>
            <div class="dm-fact">
              <span class="dm-label">{{ t('dm.status.listenPort') }}</span>
              <span class="mono">{{ statusInfo.listen_port }}</span>
            </div>
            <div class="dm-fact">
              <span class="dm-label">{{ t('dm.status.plane') }}</span>
              <span>
                {{ statusInfo.plane.audio_plane ? (statusInfo.plane.speaker_connected ? t('dm.status.planeConnected') : t('dm.status.planeWaiting')) : t('dm.status.planeStopped') }}
              </span>
            </div>
            <div class="dm-fact">
              <span class="dm-label">{{ t('dm.status.lastSeen') }}</span>
              <span>{{ formatLastSeen(statusInfo.plane.last_seen) }}</span>
            </div>
            <div v-if="(statusInfo.plane.restarts ?? 0) > 0" class="dm-fact">
              <span class="dm-label">{{ t('dm.status.restarts') }}</span>
              <span class="mono">{{ statusInfo.plane.restarts }}</span>
            </div>
            <div v-if="statusInfo.plane.wake_candidates !== undefined" class="dm-fact">
              <span class="dm-label">{{ t('dm.status.wakes') }}</span>
              <span class="mono">{{ statusInfo.plane.wake_candidates }} / {{ statusInfo.plane.wake_admitted ?? 0 }}</span>
            </div>
          </div>
          <div v-if="statusInfo.errors.length" class="dm-chips">
            <span v-for="code in statusInfo.errors" :key="code" class="dm-chip warn">
              {{ statusErrorLabel(code) }}
            </span>
          </div>
        </template>
      </div>

      <div class="dm-facts">
        <div class="dm-fact">
          <span class="dm-label">{{ t('dm.field.room') }}</span>
          <span>{{ detail.device.room || t('dm.roomUnset') }}</span>
        </div>
        <div class="dm-fact">
          <span class="dm-label">{{ t('dm.field.host') }}</span>
          <span class="mono">{{ detail.device.host }}</span>
        </div>
        <div class="dm-fact">
          <span class="dm-label">{{ t('dm.adapter') }}</span>
          <span class="mono">{{ detail.device.adapter }}</span>
        </div>
        <div class="dm-fact">
          <span class="dm-label">{{ t('dm.model') }}</span>
          <span>{{ detail.live?.model || '—' }}</span>
        </div>
        <div class="dm-fact">
          <span class="dm-label">{{ t('dm.plug') }}</span>
          <span>{{ plugName(plugForDevice(detail.device)) }}</span>
        </div>
        <div class="dm-fact">
          <span class="dm-label">{{ t('dm.class') }}</span>
          <span>{{ classLabel(plugForDevice(detail.device)?.class) }}</span>
        </div>
        <div class="dm-fact">
          <span class="dm-label">{{ t('dm.trust') }}</span>
          <span>{{ trustLabel(plugForDevice(detail.device)?.trust) }}</span>
        </div>
        <div class="dm-fact">
          <span class="dm-label">{{ t('dm.transport') }}</span>
          <span>{{ transportLabel(detail.live?.transport || selectedDevice?.transport) }}</span>
        </div>
        <div class="dm-fact">
          <span class="dm-label">{{ t('dm.field.listenPort') }}</span>
          <span class="mono">{{ detail.listen_port ?? detail.device.listen_port ?? 4412 }}</span>
        </div>
      </div>

      <div class="dm-subsection">
        <h3 class="dm-subhead">{{ t('dm.capabilities') }}</h3>
        <div class="dm-chips">
          <span v-for="capability in detailCapabilities" :key="capability" class="dm-chip">
            {{ capLabel(capability) }}
          </span>
          <span v-if="!detailCapabilities.length" class="dm-chip muted">{{ t('dm.cap.none') }}</span>
        </div>
      </div>

      <div class="dm-subsection">
        <h3 class="dm-subhead">{{ t('dm.params') }}</h3>
        <div v-if="!detailParamRows.length" class="field-hint">{{ t('dm.params.none') }}</div>
        <div v-else class="dm-param-grid">
          <div v-for="row in detailParamRows" :key="row.key" class="dm-param">
            <span class="dm-label">{{ row.label }}</span>
            <span class="mono" :class="{ masked: row.masked }">{{ row.value }}</span>
            <code class="dm-param-key mono">{{ row.key }}</code>
          </div>
        </div>
        <p class="field-hint">{{ t('dm.params.note') }}</p>
      </div>

      <div class="actions dm-actions">
        <button type="button" class="btn" @click="openEdit">
          {{ t('dm.edit') }}
        </button>
        <button type="button" class="btn" :disabled="connectBusy" @click="loadConnect(detail.device.id)">
          {{ connectBusy ? t('dm.connect.loading') : t('dm.connect.reload') }}
        </button>
        <button type="button" class="btn" @click="openRotate">
          {{ t('dm.rotate.open') }}
        </button>
        <button type="button" class="btn btn-danger" @click="askDeleteSelected">
          <AppIcon name="trash" :size="13" />
          {{ t('dm.delete') }}
        </button>
      </div>

      <!-- connection test -->
      <div class="dm-subsection dm-test">
        <div class="dm-subhead-row">
          <h3 class="dm-subhead">{{ t('dm.test.title') }}</h3>
          <button type="button" class="btn btn-sm" :disabled="testBusy" @click="runTest">
            {{ testBusy ? t('dm.test.running') : t('dm.test.run') }}
          </button>
        </div>
        <p class="field-hint">{{ t('dm.test.note') }}</p>
        <p v-if="testError" class="note fail" role="alert">
          {{ t('dm.test.error', { message: testError }) }}
        </p>
        <template v-else-if="testResult">
          <div class="dm-test-summary">
            <span :class="testResult.ok ? 'badge badge-ok' : 'badge badge-fail'">
              {{ testResult.ok ? t('dm.test.ok') : t('dm.test.fail') }}
            </span>
            <span v-if="!testResult.ok" class="field-hint">
              {{ t('dm.test.failedList', { checks: failedChecks }) }}
            </span>
            <button type="button" class="btn btn-sm actions-end" @click="copyTestResult">
              {{ copied === 'test:all' ? t('common.copied') : t('dm.test.copyRaw') }}
            </button>
          </div>
          <div class="dm-checks">
            <div
              v-for="check in testResult.checks"
              :key="check.name"
              class="dm-check"
              :class="check.ok ? 'pass' : 'fail'"
            >
              <div class="dm-check-head">
                <span class="dm-check-name">{{ checkLabel(check.name) }}</span>
                <span :class="check.ok ? 'badge badge-ok' : 'badge badge-fail'">
                  {{ check.ok ? t('dm.test.pass') : t('dm.test.failShort') }}
                </span>
                <button type="button" class="btn btn-sm actions-end" @click="copyCheck(check)">
                  {{ copied === `check:${check.name}` ? t('common.copied') : t('dm.test.copyDetail') }}
                </button>
              </div>
              <pre class="dm-raw mono">{{ JSON.stringify(check.detail, null, 2) }}</pre>
            </div>
          </div>
        </template>
      </div>

      <!-- connection details -->
      <div class="dm-subsection">
        <div class="dm-subhead-row">
          <h3 class="dm-subhead">{{ t('dm.connect.title') }}</h3>
          <span v-if="connectInfo" :class="connectInfo.configured ? 'badge badge-ok' : 'badge badge-warn'">
            {{ connectInfo.configured ? t('dm.connect.configured') : t('dm.connect.notConfigured') }}
          </span>
        </div>
        <p v-if="connectBusy" class="field-hint" role="status">{{ t('dm.connect.loading') }}</p>
        <p v-else-if="connectError" class="note fail" role="alert">
          {{ t('dm.connect.loadError', { message: connectError }) }}
          <button type="button" class="btn btn-sm" @click="loadConnect(detail.device.id)">
            {{ t('common.retry') }}
          </button>
        </p>
        <template v-else-if="connectInfo">
          <div class="dm-copy-field">
            <label class="dm-label" for="dm-ws-url">{{ t('dm.connect.wsUrl') }}</label>
            <div class="dm-copy-row">
              <input id="dm-ws-url" :value="wsDisplay" readonly class="mono" spellcheck="false" />
              <button type="button" class="btn btn-sm" @click="revealed = !revealed">
                {{ revealed ? t('dm.connect.hide') : t('dm.connect.show') }}
              </button>
              <button type="button" class="btn btn-sm" @click="copyText(connectInfo.ws_url, 'ws')">
                {{ copied === 'ws' ? t('common.copied') : t('common.copy') }}
              </button>
            </div>
            <p class="field-hint">{{ t('dm.connect.wsHint') }}</p>
          </div>
          <div class="dm-copy-field">
            <div class="dm-label-row">
              <label class="dm-label" for="dm-token">{{ t('dm.connect.token') }}</label>
              <span class="field-hint mono">{{ connectInfo.token_env }}</span>
            </div>
            <div class="dm-copy-row">
              <input
                id="dm-token"
                :value="tokenDisplay"
                readonly
                class="mono"
                autocomplete="off"
                spellcheck="false"
                :type="revealed ? 'text' : 'password'"
              />
              <button type="button" class="btn btn-sm" :disabled="!connectInfo.token" @click="revealed = !revealed">
                {{ revealed ? t('dm.connect.hide') : t('dm.connect.show') }}
              </button>
              <button
                type="button"
                class="btn btn-sm"
                :disabled="!connectInfo.token"
                @click="copyText(connectInfo.token, 'token')"
              >
                {{ copied === 'token' ? t('common.copied') : t('common.copy') }}
              </button>
            </div>
            <p class="field-hint">{{ t('dm.connect.tokenHint') }}</p>
            <RouterLink class="dm-ledger-link" to="/panel/credentials">{{ t('credentials.manage') }}</RouterLink>
          </div>
          <div v-if="!connectInfo.configured" class="dm-warning">
            <strong>{{ t('dm.connect.missingTitle') }}</strong>
            <p>{{ t('dm.connect.missingBody', { env: connectInfo.token_env }) }}</p>
          </div>
          <div v-else-if="connectInfo.allow_no_token" class="dm-warning">
            <strong>{{ t('dm.connect.allowNoToken') }}</strong>
            <p>{{ t('dm.connect.allowNoTokenHint') }}</p>
          </div>
        </template>
      </div>
    </template>
  </section>
  <p v-else-if="devices.length" class="field-hint dm-pick">{{ t('dm.pickHint') }}</p>

  <!-- add / edit drawer ---------------------------------------------------- -->
  <Teleport to="body">
    <div v-if="editorOpen" class="dm-drawer-backdrop" @click.self="closeEditor()">
      <section class="dm-drawer" role="dialog" aria-modal="true" aria-labelledby="dm-drawer-title">
        <header class="dm-drawer-head">
          <div>
            <div class="kicker">{{ editorMode === 'create' ? t('dm.add') : t('dm.edit') }}</div>
            <h2 id="dm-drawer-title">{{ editorTitle }}</h2>
          </div>
          <button type="button" class="btn-icon" :aria-label="t('common.close')" @click="closeEditor()">
            <AppIcon name="close" :size="18" />
          </button>
        </header>

        <p v-if="formError" class="banner banner-fail" role="alert">{{ formError }}</p>
        <p v-if="editorUnknownParam" class="banner banner-warn" role="alert">
          {{ t('dm.err.unknownType', { key: editorUnknownParam.key, type: String(editorUnknownParam.type) }) }}
        </p>

        <div class="dm-form">
          <div class="field-row" :class="{ invalid: fieldErrors.id }">
            <div class="field-meta">
              <label class="field-label" for="dm-form-id">{{ t('dm.field.id') }}</label>
              <p class="field-hint">{{ editorMode === 'create' ? t('dm.field.idHint') : t('dm.field.idLocked') }}</p>
            </div>
            <div class="field-control">
              <input
                id="dm-form-id"
                v-model="form.id"
                :readonly="editorMode === 'edit'"
                :class="{ invalid: fieldErrors.id }"
                autocomplete="off"
                spellcheck="false"
                placeholder="living"
              />
              <p v-if="fieldErrors.id" class="field-error">{{ fieldErrors.id }}</p>
            </div>
          </div>

          <div class="field-row" :class="{ invalid: fieldErrors.plugId }">
            <div class="field-meta">
              <label class="field-label" for="dm-form-plug">{{ t('dm.field.plug') }}</label>
              <p class="field-hint">{{ t('dm.field.plugHint') }}</p>
              <p v-if="editorPlug" class="field-hint">
                {{ classLabel(editorPlug.class) }} · {{ trustLabel(editorPlug.trust) }}
              </p>
            </div>
            <div class="field-control">
              <select id="dm-form-plug" v-model="form.plugId" @change="handlePlugChange">
                <option v-for="plug in plugOptions" :key="plug.id ?? ''" :value="plug.id ?? ''" :disabled="!plug.available">
                  {{ plugName(plug) }}{{ plug.available ? '' : ` — ${t('dm.plugUnavailable')}` }}
                </option>
              </select>
              <p v-if="fieldErrors.plugId" class="field-error">{{ fieldErrors.plugId }}</p>
            </div>
          </div>

          <div v-if="editorDevices.length" class="field-row" :class="{ invalid: fieldErrors.adapter }">
            <div class="field-meta">
              <label class="field-label" for="dm-form-adapter">{{ t('dm.field.adapter') }}</label>
              <p class="field-hint">{{ t('dm.field.adapterHint') }}</p>
            </div>
            <div class="field-control">
              <select
                id="dm-form-adapter"
                v-model="form.adapterId"
                :disabled="editorDevices.length <= 1"
                @change="handleAdapterChange"
              >
                <option v-for="device in editorDevices" :key="device.id" :value="device.id">
                  {{ device.name }} ({{ device.id }})
                </option>
              </select>
              <p v-if="fieldErrors.adapter" class="field-error">{{ fieldErrors.adapter }}</p>
            </div>
          </div>

          <div class="field-row" :class="{ invalid: fieldErrors.host }">
            <div class="field-meta">
              <label class="field-label" for="dm-form-host">{{ t('dm.field.host') }}</label>
              <p class="field-hint">{{ t('dm.field.hostHint') }}</p>
            </div>
            <div class="field-control">
              <input
                id="dm-form-host"
                v-model="form.host"
                :class="{ invalid: fieldErrors.host }"
                autocomplete="off"
                spellcheck="false"
                placeholder="<speaker-ip>"
              />
              <p v-if="fieldErrors.host" class="field-error">{{ fieldErrors.host }}</p>
            </div>
          </div>

          <div class="field-row" :class="{ invalid: fieldErrors.room }">
            <div class="field-meta">
              <label class="field-label" for="dm-form-room">{{ t('dm.field.room') }}</label>
            </div>
            <div class="field-control">
              <input id="dm-form-room" v-model="form.room" :class="{ invalid: fieldErrors.room }" placeholder="客厅" />
              <p v-if="fieldErrors.room" class="field-error">{{ fieldErrors.room }}</p>
            </div>
          </div>

          <div class="field-row" :class="{ invalid: fieldErrors.listen_port }">
            <div class="field-meta">
              <label class="field-label" for="dm-form-port">{{ t('dm.field.listenPort') }}</label>
              <p class="field-hint">{{ t('dm.field.listenPortHint') }}</p>
            </div>
            <div class="field-control">
              <input
                id="dm-form-port"
                v-model="form.listenPort"
                :class="{ invalid: fieldErrors.listen_port }"
                inputmode="numeric"
                autocomplete="off"
                :placeholder="t('dm.field.listenPortPlaceholder')"
              />
              <p v-if="fieldErrors.listen_port" class="field-error">{{ fieldErrors.listen_port }}</p>
            </div>
          </div>

          <div class="field-row" :class="{ invalid: fieldErrors.wake_words }">
            <div class="field-meta">
              <label class="field-label" for="dm-form-wake-0">{{ t('dm.field.wakeWords') }}</label>
              <p class="field-hint">{{ t('dm.field.wakeHint') }}</p>
            </div>
            <div class="field-control">
              <div class="dm-wake-list">
                <div v-for="(word, index) in form.wakeWords" :key="index" class="dm-wake-row">
                  <input
                    :id="`dm-form-wake-${index}`"
                    v-model="form.wakeWords[index]"
                    maxlength="12"
                    :class="{ invalid: fieldErrors.wake_words }"
                    :placeholder="t('dm.field.wakePlaceholder')"
                    :aria-label="`${t('dm.field.wakeWords')} ${index + 1}`"
                  />
                  <button
                    v-if="form.wakeWords.length > 1"
                    type="button"
                    class="btn btn-sm"
                    @click="removeWakeWord(index)"
                  >
                    {{ t('dm.field.wakeRemove') }}
                  </button>
                </div>
                <button v-if="form.wakeWords.length < 3" type="button" class="btn btn-sm" @click="addWakeWord">
                  {{ t('dm.field.wakeAdd') }}
                </button>
              </div>
              <p v-if="fieldErrors.wake_words" class="field-error">{{ fieldErrors.wake_words }}</p>
            </div>
          </div>

          <div class="field-row">
            <div class="field-meta">
              <label class="field-label" for="dm-form-enabled">{{ t('dm.field.enabled') }}</label>
            </div>
            <div class="field-control">
              <label class="switch">
                <input id="dm-form-enabled" v-model="form.enabled" type="checkbox" />
                <span class="track" />
                <span class="switch-text">{{ form.enabled ? t('common.on') : t('common.off') }}</span>
              </label>
            </div>
          </div>

          <div v-if="editorExtraParams.length" class="dm-param-section">
            <h3 class="dm-subhead">{{ t('dm.field.params') }}</h3>
            <p class="field-hint">{{ t('dm.form.paramsNote') }}</p>
            <div
              v-for="spec in editorExtraParams"
              :key="spec.key"
              class="field-row"
              :class="{ invalid: fieldErrors[`param:${spec.key}`] }"
            >
              <div class="field-meta">
                <label class="field-label" :for="`dm-param-${spec.key}`">{{ paramLabel(spec.key, spec.label) }}</label>
                <span v-if="spec.required" class="badge badge-muted">{{ t('dm.param.required') }}</span>
                <p v-if="spec.help" class="field-hint">{{ spec.help }}</p>
                <p v-else-if="!spec.label" class="field-hint mono">{{ t('dm.param.technical', { key: spec.key }) }}</p>
              </div>
              <div class="field-control">
                <select
                  v-if="spec.type === 'choice'"
                  :id="`dm-param-${spec.key}`"
                  v-model="paramTexts[spec.key]"
                >
                  <option value="">—</option>
                  <option v-for="option in spec.options" :key="option" :value="option">{{ option }}</option>
                </select>
                <label v-else-if="spec.type === 'bool'" class="switch">
                  <input :id="`dm-param-${spec.key}`" v-model="paramBools[spec.key]" type="checkbox" />
                  <span class="track" />
                  <span class="switch-text">{{ paramBools[spec.key] ? t('common.on') : t('common.off') }}</span>
                </label>
                <input
                  v-else-if="spec.type === 'secret'"
                  :id="`dm-param-${spec.key}`"
                  v-model="secretDrafts[spec.key]"
                  type="password"
                  autocomplete="off"
                  spellcheck="false"
                  :placeholder="editorMode === 'edit' ? t('dm.param.secretKeep') : t('dm.param.secretWriteOnly')"
                />
                <input
                  v-else-if="spec.type === 'int'"
                  :id="`dm-param-${spec.key}`"
                  v-model="paramTexts[spec.key]"
                  inputmode="numeric"
                  :placeholder="spec.placeholder"
                />
                <input
                  v-else-if="spec.type === 'list'"
                  :id="`dm-param-${spec.key}`"
                  v-model="paramTexts[spec.key]"
                  :placeholder="spec.placeholder || t('dm.param.listHint')"
                />
                <input
                  v-else-if="spec.type === 'string'"
                  :id="`dm-param-${spec.key}`"
                  v-model="paramTexts[spec.key]"
                  :placeholder="spec.placeholder"
                />
                <input v-else :id="`dm-param-${spec.key}`" :value="paramTexts[spec.key]" disabled />
                <p v-if="spec.max != null && spec.type === 'list'" class="field-hint">
                  {{ t('dm.param.max', { n: spec.max }) }}
                </p>
                <p v-if="fieldErrors[`param:${spec.key}`]" class="field-error">
                  {{ fieldErrors[`param:${spec.key}`] }}
                </p>
              </div>
            </div>
          </div>

          <p v-if="!editorExtraParams.length && !editorManifest" class="field-hint">
            {{ t('dm.params.noManifest') }}
          </p>
        </div>

        <footer class="dm-drawer-foot">
          <p class="field-hint dm-foot-hint">{{ t('dm.form.note') }}</p>
          <div class="actions">
            <button type="button" class="btn" :disabled="formBusy" @click="closeEditor()">
              {{ t('common.cancel') }}
            </button>
            <button
              type="button"
              class="btn btn-primary"
              :disabled="formBusy || Boolean(editorUnknownParam)"
              @click="saveForm"
            >
              {{ formBusy ? t('common.saving') : t('dm.save') }}
            </button>
          </div>
        </footer>
      </section>
    </div>
  </Teleport>

  <!-- token rotation (show once) ------------------------------------------ -->
  <Teleport to="body">
    <div v-if="rotateOpen" class="dm-drawer-backdrop dm-confirm-backdrop" @click.self="closeRotate">
      <section class="dm-confirm" role="dialog" aria-modal="true" aria-labelledby="dm-rotate-title">
        <div class="dm-drawer-head">
          <div>
            <div class="kicker">{{ t('dm.rotate.kicker') }}</div>
            <h2 id="dm-rotate-title">{{ t('dm.rotate.title') }}</h2>
          </div>
          <button type="button" class="btn-icon" :aria-label="t('common.close')" @click="closeRotate">
            <AppIcon name="close" :size="18" />
          </button>
        </div>

        <template v-if="!rotated">
          <p>{{ t('dm.rotate.confirm', { id: detail?.device.id ?? selectedId, env: rotateEnv }) }}</p>
          <div class="dm-warning">
            <strong>{{ t('dm.rotate.warningTitle') }}</strong>
            <p>{{ t('dm.rotate.warning') }}</p>
          </div>
          <p v-if="rotateError" class="note fail" role="alert">{{ rotateError }}</p>
          <div class="actions dm-confirm-actions">
            <button type="button" class="btn" :disabled="rotateBusy" @click="closeRotate">
              {{ t('common.cancel') }}
            </button>
            <button type="button" class="btn btn-danger" :disabled="rotateBusy" @click="confirmRotate">
              {{ rotateBusy ? t('dm.rotate.running') : t('dm.rotate.run') }}
            </button>
          </div>
        </template>

        <template v-else>
          <p>{{ t('dm.rotate.done') }}</p>
          <div class="dm-warning">
            <strong>{{ t('dm.rotate.onceTitle') }}</strong>
            <p>{{ t('dm.rotate.onceBody') }}</p>
          </div>
          <div class="dm-copy-field">
            <label class="dm-label" for="dm-rotate-ws">{{ t('dm.rotate.wsUrl') }}</label>
            <div class="dm-copy-row">
              <input id="dm-rotate-ws" :value="rotated.ws_url" readonly class="mono" spellcheck="false" />
              <button type="button" class="btn btn-sm" @click="copyText(rotated.ws_url, 'rotate:ws')">
                {{ copied === 'rotate:ws' ? t('common.copied') : t('common.copy') }}
              </button>
            </div>
          </div>
          <div class="dm-copy-field">
            <label class="dm-label" for="dm-rotate-token">{{ t('dm.rotate.token') }}</label>
            <div class="dm-copy-row">
              <input
                id="dm-rotate-token"
                :value="rotated.token"
                readonly
                class="mono"
                autocomplete="off"
                spellcheck="false"
              />
              <button type="button" class="btn btn-sm" @click="copyText(rotated.token, 'rotate:token')">
                {{ copied === 'rotate:token' ? t('common.copied') : t('common.copy') }}
              </button>
            </div>
          </div>
          <div class="actions dm-confirm-actions">
            <button type="button" class="btn btn-primary" @click="closeRotate">
              {{ t('dm.rotate.close') }}
            </button>
          </div>
        </template>
      </section>
    </div>
  </Teleport>

  <!-- delete confirmation -------------------------------------------------- -->
  <Teleport to="body">
    <div v-if="deleteTarget" class="dm-drawer-backdrop dm-confirm-backdrop" @click.self="deleteTarget = null">
      <section class="dm-confirm" role="dialog" aria-modal="true" aria-labelledby="dm-delete-title">
        <div class="dm-drawer-head">
          <div>
            <div class="kicker">{{ t('dm.delete') }}</div>
            <h2 id="dm-delete-title">{{ t('dm.deleteTitle') }}</h2>
          </div>
          <button type="button" class="btn-icon" :aria-label="t('common.close')" @click="deleteTarget = null">
            <AppIcon name="close" :size="18" />
          </button>
        </div>
        <p>{{ t('dm.deleteConfirm', { id: deleteTarget.id, room: deleteTarget.room || t('dm.roomUnset') }) }}</p>
        <div class="dm-warning">
          <strong>{{ t('dm.deleteImpactTitle') }}</strong>
          <ul class="dm-impact-list">
            <li>{{ t('dm.deleteImpactPlane') }}</li>
            <li>{{ t('dm.deleteImpactSetup') }}</li>
            <li>{{ t('dm.deleteImpactDevice') }}</li>
          </ul>
        </div>
        <p v-if="deleteError" class="note fail" role="alert">{{ deleteError }}</p>
        <div class="actions dm-confirm-actions">
          <button type="button" class="btn" :disabled="deleteBusy" @click="deleteTarget = null">
            {{ t('common.cancel') }}
          </button>
          <button type="button" class="btn btn-danger" :disabled="deleteBusy" @click="confirmDelete">
            <AppIcon name="trash" :size="13" />
            {{ deleteBusy ? t('dm.deleting') : t('dm.delete') }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.dm-intro {
  margin-bottom: var(--s5);
  max-width: 72ch;
}

.dm-banner {
  margin-bottom: var(--s4);
}

.dm-inline-banner {
  margin: var(--s3) 0;
}

.dm-list {
  border-top: 1px solid var(--line-strong);
}

.dm-card {
  border-top: 1px solid var(--line);
  padding: 12px 0;
  min-width: 0;
}

.dm-card:first-child {
  border-top: 0;
}

.dm-card.active {
  background: var(--accent-soft);
  box-shadow: inset 2px 0 0 var(--accent);
}

.dm-card-main {
  padding: 4px 10px;
  border-radius: var(--r-xs);
  cursor: pointer;
  min-width: 0;
}

.dm-card-main:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}

.dm-card-head {
  display: flex;
  align-items: baseline;
  gap: var(--s2) var(--s3);
  flex-wrap: wrap;
  min-width: 0;
}

.dm-id {
  font-size: 13px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.dm-room {
  font-size: 14px;
  flex: 1 1 120px;
  min-width: 0;
  overflow-wrap: anywhere;
}

.dm-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 2px var(--s3);
  margin-top: 4px;
  color: var(--muted);
  font-size: 10.5px;
  overflow-wrap: anywhere;
}

.dm-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 7px;
  min-width: 0;
}

.dm-chip {
  display: inline-flex;
  max-width: 100%;
  padding: 1px 6px;
  border: 1px solid var(--line);
  border-radius: var(--r-xs);
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-2);
  overflow-wrap: anywhere;
}

.dm-chip.muted {
  color: var(--muted);
}

.dm-param-line {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 2px 8px;
  margin-top: 7px;
  min-width: 0;
}

.dm-label {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.dm-param-text {
  font-size: 11px;
  color: var(--ink-2);
  overflow-wrap: anywhere;
  min-width: 0;
}

.dm-card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s2);
  margin-top: 8px;
  padding-left: 10px;
}

.dm-detail {
  min-width: 0;
}

.dm-facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(170px, 100%), 1fr));
  gap: var(--s3) var(--s5);
  padding: var(--s4) 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.dm-fact,
.dm-param {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  overflow-wrap: anywhere;
}

.dm-subsection {
  margin-top: var(--s5);
  min-width: 0;
}

.dm-subhead-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s3);
  flex-wrap: wrap;
}

.dm-subhead {
  font-size: 15px;
  margin-bottom: var(--s2);
}

.dm-param-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(200px, 100%), 1fr));
  gap: var(--s3) var(--s5);
  margin-top: var(--s3);
}

.dm-param {
  padding: 8px 0;
  border-top: 1px solid var(--line);
}

.dm-param-key {
  font-size: 9.5px;
  color: var(--muted);
  background: none;
  padding: 0;
}

.masked {
  letter-spacing: 0.12em;
}

.dm-actions {
  margin-top: var(--s5);
  padding-top: var(--s3);
  border-top: 1px solid var(--line);
}

.dm-test-summary {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  margin-top: var(--s2);
}

.dm-checks {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  margin-top: var(--s3);
}

.dm-check {
  border: 1px solid var(--line);
  border-left-width: 3px;
  border-radius: var(--r-sm);
  padding: var(--s3);
  min-width: 0;
}

.dm-check.pass {
  border-left-color: var(--ok);
}

.dm-check.fail {
  border-left-color: var(--fail);
}

.dm-check-head {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.dm-check-name {
  font-weight: 550;
  font-size: 13px;
}

.dm-raw {
  margin: var(--s2) 0 0;
  padding: var(--s2);
  max-height: 160px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: var(--surface-2);
  border-radius: var(--r-xs);
  font-size: 10.5px;
  line-height: 1.5;
}

.dm-copy-field {
  margin-top: var(--s3);
  min-width: 0;
}

.dm-ledger-link {
  display: inline-block;
  margin-top: 4px;
  font-size: 12px;
  color: var(--accent);
}

.dm-copy-row {
  display: flex;
  align-items: center;
  gap: var(--s2);
  margin-top: 4px;
  min-width: 0;
  flex-wrap: wrap;
}

.dm-copy-row input {
  flex: 1 1 220px;
  min-width: 0;
  font-size: 11.5px;
}

.dm-copy-row .btn {
  flex: none;
}

.dm-label-row {
  display: flex;
  align-items: baseline;
  gap: var(--s2);
  flex-wrap: wrap;
}

.dm-warning {
  margin-top: var(--s3);
  padding: var(--s3);
  border: 1px solid var(--warn);
  background: var(--warn-soft);
  border-radius: var(--r-sm);
  overflow-wrap: anywhere;
}

.dm-warning p {
  margin-top: 4px;
  font-size: 12.5px;
}

.dm-impact-list {
  margin: 6px 0 0 18px;
  font-size: 12.5px;
  line-height: 1.55;
}

.dm-impact-list li + li {
  margin-top: 4px;
}

.dm-pick {
  margin-top: var(--s5);
}

/* drawer */
.dm-drawer-backdrop {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: flex;
  justify-content: flex-end;
  background: rgba(10, 9, 7, 0.5);
  overflow: hidden;
}

.dm-drawer {
  width: min(560px, 100%);
  max-width: 100%;
  height: 100%;
  overflow-y: auto;
  overflow-x: hidden;
  background: var(--surface);
  border-left: 1px solid var(--line-strong);
  padding: var(--s5);
  box-sizing: border-box;
}

.dm-drawer-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s3);
  margin-bottom: var(--s3);
  min-width: 0;
}

.dm-drawer-head h2 {
  margin-top: 3px;
  font-size: 20px;
  overflow-wrap: anywhere;
}

.dm-form {
  min-width: 0;
}

.dm-form .field-control {
  max-width: none;
}

.dm-wake-list {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  min-width: 0;
}

.dm-wake-row {
  display: flex;
  gap: var(--s2);
  min-width: 0;
}

.dm-wake-row input {
  flex: 1 1 auto;
  min-width: 0;
}

.dm-param-section {
  margin-top: var(--s5);
  padding-top: var(--s4);
  border-top: 1px solid var(--line-strong);
}

.dm-drawer-foot {
  position: sticky;
  bottom: calc(-1 * var(--s5));
  margin: var(--s5) calc(-1 * var(--s5)) calc(-1 * var(--s5));
  padding: var(--s3) var(--s5);
  border-top: 1px solid var(--line);
  background: var(--surface);
}

.dm-foot-hint {
  margin-bottom: var(--s2);
}

.dm-confirm-backdrop {
  align-items: center;
  justify-content: center;
  padding: 16px;
}

.dm-confirm {
  width: min(480px, 100%);
  max-height: 100%;
  overflow-y: auto;
  background: var(--surface);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-md);
  box-shadow: var(--shadow-2);
  padding: var(--s5);
  box-sizing: border-box;
}

.dm-confirm-actions {
  justify-content: flex-end;
  margin-top: var(--s4);
}

.dm-status-facts {
  padding-top: var(--s2);
  border-top: 0;
  border-bottom: 1px solid var(--line);
}

.dm-card-switch {
  margin-right: auto;
}

.dm-chip.accent {
  border-color: var(--accent);
  color: var(--accent);
}

.dm-chip.warn {
  border-color: var(--warn, #a16207);
  color: var(--warn, #a16207);
}

@media (max-width: 720px) {
  .dm-card-main {
    padding-left: 4px;
    padding-right: 4px;
  }

  .dm-card-actions {
    padding-left: 4px;
  }

  .dm-drawer {
    border-left: 0;
    padding: var(--s4);
  }

  .dm-drawer-foot {
    bottom: calc(-1 * var(--s4));
    margin: var(--s5) calc(-1 * var(--s4)) calc(-1 * var(--s4));
    padding: var(--s3) var(--s4);
  }

  .dm-copy-row input {
    flex-basis: 100%;
  }

  .dm-copy-row .btn {
    flex: 1 1 auto;
  }
}
</style>
