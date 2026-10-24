<script setup lang="ts">
/**
 * The install wizard (P2.2): a plug picker in the header and eight cards whose
 * order, status and allowlisted actions all come from `GET /api/v1/setup/step`.
 *
 * The view never invents completion: every badge is the server's `status`, every
 * piece of evidence is the server's `evidence`, and the only actions offered are
 * the ones the server put in `step.actions`. Secrets are write-only: key/secret
 * inputs start empty, are never bound to a response value, and never leave this
 * component except in the one POST that stores them. Diagnostics are assembled
 * explicitly (no token, host, key or full config path) and only copied.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import {
  api,
  ApiError,
  apiErrorObject,
  type CatalogDoc,
  type DeviceConnectInfo,
  type DeviceDoc,
  type SetupDeviceManifest,
  type SetupParamSpec,
  type SetupPlugDetail,
  type SetupPlugSummary,
  type SetupStepResponse,
  type SetupStepState,
  type SetupStepStatus,
  type ValidationIssue,
} from '../api'
import {
  localizeBackend,
  localizeIssue,
  pickLocalized,
  providerName,
  t,
  type MsgKey,
} from '../i18n'

type Stage = 'llm' | 'asr' | 'tts'

interface SetupKey {
  name: string
  stages: Stage[]
  providers: string[]
  hasValue: boolean
  hint: string
  missing: boolean
}

interface StepIssue {
  code: string
  message: string
  hint: string
  details: string
  issues: ValidationIssue[]
}

interface TestCheck {
  id?: string
  ok?: boolean
  detail?: Record<string, unknown>
}

interface LastTurn {
  reply?: string
  audio_bytes?: number
  error?: string
}

interface RunOptions {
  retry?: () => Promise<unknown>
  forcePrefill?: boolean
  forceConnect?: boolean
}

const STAGES: Stage[] = ['llm', 'asr', 'tts']
const STAGE_LABELS: Record<Stage, MsgKey> = {
  llm: 'setup.keys.stage.llm',
  asr: 'setup.keys.stage.asr',
  tts: 'setup.keys.stage.tts',
}

const STEP_TITLES: Record<string, MsgKey> = {
  keys: 'setup.step.keys',
  models: 'setup.step.models',
  prepare: 'setup.step.prepare',
  params: 'setup.step.params',
  wake: 'setup.step.wake',
  connect: 'setup.step.connect',
  test: 'setup.step.test',
  verify: 'setup.step.verify',
}

const STEP_USER: Record<string, MsgKey> = {
  keys: 'setup.v2.keys.user',
  models: 'setup.v2.models.user',
  prepare: 'setup.v2.prepare.user',
  params: 'setup.v2.params.user',
  wake: 'setup.v2.wake.user',
  connect: 'setup.v2.connect.user',
  test: 'setup.v2.test.user',
  verify: 'setup.v2.verify.user',
}

const STEP_RUI: Record<string, MsgKey> = {
  keys: 'setup.v2.keys.rui',
  models: 'setup.v2.models.rui',
  prepare: 'setup.v2.prepare.rui',
  params: 'setup.v2.params.rui',
  wake: 'setup.v2.wake.rui',
  connect: 'setup.v2.connect.rui',
  test: 'setup.v2.test.rui',
  verify: 'setup.v2.verify.rui',
}

const STATUS_LABEL: Record<SetupStepStatus, MsgKey> = {
  pending: 'setup.v2.status.pending',
  running: 'setup.v2.status.running',
  done: 'setup.v2.status.done',
  error: 'setup.v2.status.error',
  skipped: 'setup.v2.status.skipped',
}

const STATUS_CLASS: Record<SetupStepStatus, string> = {
  pending: 'badge-muted',
  running: 'badge-accent',
  done: 'badge-ok',
  error: 'badge-fail',
  skipped: 'badge-muted',
}

const ERROR_KEYS: Record<string, MsgKey> = {
  auth_required: 'setup.error.auth_required',
  unknown_plug: 'setup.error.unknown_plug',
  unknown_step: 'setup.error.unknown_step',
  unknown_device: 'setup.error.unknown_device',
  no_config: 'setup.error.no_config',
  plug_unavailable: 'setup.error.plug_unavailable',
  plug_incompatible: 'setup.error.plug_incompatible',
  missing_prerequisite: 'setup.error.missing_prerequisite',
  step_in_progress: 'setup.error.step_in_progress',
  device_not_connected: 'setup.error.device_not_connected',
  audio_plane_unavailable: 'setup.error.audio_plane_unavailable',
  turn_in_progress: 'setup.error.turn_in_progress',
  invalid_values: 'setup.error.invalid_values',
  unsupported_param_type: 'setup.error.unsupported_param_type',
  invalid_secret_name: 'setup.error.invalid_secret_name',
  secret_rejected: 'setup.error.secret_rejected',
  unknown_action: 'setup.error.unknown_action',
  third_party_not_supported: 'setup.error.third_party_not_supported',
  sidecar_not_supported: 'setup.error.sidecar_not_supported',
  no_audio: 'setup.error.no_audio',
  prepare_not_confirmed: 'setup.error.prepare_not_confirmed',
  rollback_not_acknowledged: 'setup.error.rollback_not_acknowledged',
  device_token_missing: 'setup.error.device_token_missing',
  adapter_unavailable: 'setup.error.adapter_unavailable',
  keywords_missing: 'setup.error.keywords_missing',
  missing_keys: 'setup.error.missing_keys',
  missing_models: 'setup.error.missing_models',
  plane_restart_failed: 'setup.error.plane_restart_failed',
}

const CHECK_LABELS: Record<string, MsgKey> = {
  plane: 'setup.v2.test.check.plane',
  host: 'setup.v2.test.check.host',
}

const ARTIFACT_KEYS: Record<string, MsgKey> = {
  client: 'setup.v2.artifact.client',
  firmware: 'setup.v2.artifact.firmware',
  app: 'setup.v2.artifact.app',
  none: 'setup.v2.artifact.none',
}

const TRUST_KEYS: Record<string, MsgKey> = {
  builtin: 'setup.v2.trust.builtin',
  trusted: 'setup.v2.trust.trusted',
  'third-party': 'setup.v2.trust.third-party',
}

const SOURCE_KEYS: Record<string, MsgKey> = {
  builtin: 'setup.v2.source.builtin',
  user: 'setup.v2.source.user',
}

const PARAM_TYPES = new Set(['string', 'int', 'bool', 'list', 'secret', 'choice'])
const RESERVED_PARAMS = new Set(['host', 'room', 'wake_words'])

// ————————————————————————————————————————————————— state —————
const state = ref<SetupStepResponse | null>(null)
const plugs = ref<SetupPlugSummary[]>([])
const plugDetail = ref<SetupPlugDetail | null>(null)
const plugDetailError = ref('')
const selectedPlug = ref('')
const loading = ref(true)
const loadError = ref('')
const notice = ref('')
const busyStep = ref('')
const busyGlobal = ref('')
const issues = ref<Record<string, StepIssue>>({})
const expanded = ref<Record<string, boolean>>({})
const retryFns: Record<string, () => Promise<unknown>> = {}

const catalog = ref<CatalogDoc | null>(null)
const keyDrafts = ref<Record<string, string>>({})

const deviceDraft = ref<DeviceDoc | null>(null)
const deviceDraftKey = ref('')
const hostDraft = ref('')
const roomDraft = ref('')
const wakeWords = ref<string[]>([''])
const paramTexts = ref<Record<string, string>>({})
const paramBools = ref<Record<string, boolean>>({})
const secretDrafts = ref<Record<string, string>>({})
const fieldErrors = ref<Record<string, string>>({})

const prepareConfirmed = ref(false)
const prepareRollbackAck = ref(false)

const connectInfo = ref<DeviceConnectInfo | null>(null)
const connectBusy = ref(false)
const connectError = ref('')
const connectKey = ref('')
const tokenVisible = ref(false)

const copied = ref('')
const copyFailed = ref(false)

const waiting = ref(false)
const waitTicks = ref(0)
let waitTimer: number | undefined

const verifyText = ref('')
const verifyReply = ref('')
const verifyError = ref('')
const verifyAudio = ref(0)
const verifySkipped = ref(false)

// ————————————————————————————————————————————— derived state —————
function stepById(id: string | null | undefined, from = state.value): SetupStepState | undefined {
  if (!id) return undefined
  return from?.steps.find((step) => step.id === id)
}

const keysStep = computed(() => stepById('keys'))
const paramsStep = computed(() => stepById('params'))
const wakeStep = computed(() => stepById('wake'))
const connectStep = computed(() => stepById('connect'))
const testStep = computed(() => stepById('test'))
const verifyStep = computed(() => stepById('verify'))

const steps = computed(() => state.value?.steps ?? [])
const hasDevice = computed(() => Boolean(deviceId.value))
const plugLocked = computed(() => hasDevice.value)

const deviceId = computed<string | null>(() => {
  const raw = paramsStep.value?.evidence?.device
  return typeof raw === 'string' && raw ? raw : null
})

const selectedPlugSummary = computed<SetupPlugSummary | null>(() => state.value?.plug ?? null)
const plugOptions = computed(() => plugs.value.filter((plug) => plug.id !== null))
const failedPlugs = computed(() => plugs.value.filter((plug) => plug.id === null))
const blockReason = computed(() => state.value?.blocked ?? null)

const selectedDeviceManifest = computed<SetupDeviceManifest | null>(() => {
  const devices = plugDetail.value?.devices ?? []
  const adapter = state.value?.plug.adapter ?? ''
  return devices.find((device) => device.id === adapter) ?? devices[0] ?? null
})

const manifestParams = computed<SetupParamSpec[]>(() => selectedDeviceManifest.value?.params ?? [])
const deviceSide = computed(() => selectedDeviceManifest.value?.device_side ?? null)
const manifestClass = computed(
  () => selectedDeviceManifest.value?.device_class ?? selectedDeviceManifest.value?.class ?? '',
)
const capabilities = computed(() => selectedDeviceManifest.value?.capabilities ?? [])
const manifestVerified = computed<boolean | null>(() => {
  const manifest = selectedDeviceManifest.value
  return manifest ? manifest.acceptance?.verified === true : null
})
const manifestTransport = computed(() => selectedDeviceManifest.value?.transport ?? '')
const listenPort = computed(() => {
  const runtime = selectedDeviceManifest.value?.runtime
  if (runtime?.listen_port) return runtime.listen_port
  const resolved = plugDetail.value?.resolved?.devices?.find(
    (device) => device.id === selectedDeviceManifest.value?.id,
  )
  return resolved?.listen_port ?? 4412
})
const tokenEnv = computed(() => {
  const runtime = selectedDeviceManifest.value?.runtime
  if (runtime?.token_env) return runtime.token_env
  const resolved = plugDetail.value?.resolved?.devices?.find(
    (device) => device.id === selectedDeviceManifest.value?.id,
  )
  return resolved?.token_env || 'RUI_DEVICE_TOKEN'
})

const extraParams = computed(() =>
  manifestParams.value.filter((spec) => !RESERVED_PARAMS.has(spec.key)),
)
const hostSpec = computed(() => manifestParams.value.find((spec) => spec.key === 'host') ?? null)
const roomSpec = computed(() => manifestParams.value.find((spec) => spec.key === 'room') ?? null)
const wakeSpec = computed(
  () => manifestParams.value.find((spec) => spec.key === 'wake_words') ?? null,
)
const unsupportedParam = computed<SetupParamSpec | null>(() => {
  const spec = manifestParams.value.find(
    (item) =>
      !PARAM_TYPES.has(String(item.type)) ||
      (item.type === 'choice' && !(item.options?.length ?? 0)),
  )
  return spec ?? null
})

const audioState = computed<'online' | 'offline' | 'error' | 'unknown'>(() => {
  if (!state.value) return 'unknown'
  if (state.value.plane_error) return 'error'
  const ev = evidence(connectStep.value)
  if (ev.transport === 'outbound') return 'unknown'
  return ev.speaker_connected ? 'online' : 'offline'
})
const audioLabel = computed(() =>
  t(`setup.v2.audio.${audioState.value}` as MsgKey),
)
const audioClass = computed(() => {
  switch (audioState.value) {
    case 'online':
      return 'badge-ok'
    case 'offline':
      return 'badge-warn'
    case 'error':
      return 'badge-fail'
    default:
      return 'badge-muted'
  }
})

const doneCount = computed(
  () => steps.value.filter((step) => ['done', 'skipped'].includes(statusOf(step))).length,
)
const totalCount = computed(() => steps.value.length)

const missingKeyNames = computed<string[]>(() => {
  const raw = evidence(keysStep.value).missing
  return Array.isArray(raw) ? raw.map(String) : []
})

const stageOk = computed<Record<string, boolean>>(() => {
  const raw = evidence(keysStep.value).stages
  const out: Record<string, boolean> = {}
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    for (const [name, value] of Object.entries(raw as Record<string, unknown>)) {
      out[name] = Boolean(value)
    }
  }
  return out
})

/**
 * Keys the wizard must show, derived from the live catalog (active chain →
 * provider env names → secrets table) plus any name the server reports missing.
 * Only presence is known; no value is ever read back.
 */
const requiredKeys = computed<SetupKey[]>(() => {
  const missing = new Set(missingKeyNames.value)
  const doc = catalog.value
  const buckets = new Map<string, SetupKey>()

  const remember = (
    env: string,
    stage: Stage,
    provider: string,
    hasValue: boolean,
    hint: string,
  ) => {
    const existing = buckets.get(env)
    if (existing) {
      if (!existing.stages.includes(stage)) existing.stages.push(stage)
      if (provider && !existing.providers.includes(provider)) existing.providers.push(provider)
      existing.hasValue = existing.hasValue || hasValue
      existing.missing = missing.has(env) || !existing.hasValue
      return
    }
    buckets.set(env, {
      name: env,
      stages: [stage],
      providers: provider ? [provider] : [],
      hasValue,
      hint,
      missing: missing.has(env) || !hasValue,
    })
  }

  if (doc) {
    for (const stage of STAGES) {
      const rawChain = doc.values[`providers.${stage}.chain`]
      const chain = Array.isArray(rawChain) ? rawChain.map(String) : []
      for (const provider of chain) {
        const prefix = `providers.catalog.${provider}.`
        for (const field of doc.fields) {
          if (!field.path.startsWith(prefix)) continue
          if (!/(api_key_env|app_id_env|api_secret_env)$/.test(field.path)) continue
          const env = String(doc.values[field.path] ?? '').trim()
          if (!env) continue
          const meta = doc.secrets?.[env]
          remember(env, stage, provider, Boolean(meta?.has_value), pickLocalized(meta?.hint))
        }
      }
    }
  }

  for (const name of missing) {
    if (!buckets.has(name)) {
      buckets.set(name, {
        name,
        stages: STAGES.filter((stage) => !stageOk.value[stage]),
        providers: [],
        hasValue: false,
        hint: '',
        missing: true,
      })
    }
  }
  return [...buckets.values()]
})

const anyKeyInput = computed(() =>
  Object.values(keyDrafts.value).some((value) => value.trim()),
)

const missingParams = computed<string[]>(() => {
  const raw = evidence(paramsStep.value).missing
  return Array.isArray(raw) ? raw.map(String) : []
})

const wakeErrorText = computed(() => validateWakeWords())

const testChecks = computed<TestCheck[]>(() => {
  const raw = testStep.value?.detail?.checks
  if (!Array.isArray(raw)) return []
  return raw.filter(
    (item): item is TestCheck => Boolean(item) && typeof item === 'object',
  )
})

const verifyAudioBytes = computed(() => {
  const raw = evidence(verifyStep.value).audio_bytes
  return typeof raw === 'number' ? raw : Number(raw ?? 0) || 0
})

const maskedToken = computed(() =>
  connectInfo.value?.token ? '••••••••••••••••' : '—',
)
const wsDisplay = computed(() => {
  const info = connectInfo.value
  if (!info?.ws_url) return ''
  if (tokenVisible.value || !info.token) return info.ws_url
  return info.ws_url.includes(info.token)
    ? info.ws_url.replace(info.token, '••••••••')
    : info.ws_url
})
const wsCopyDisabled = computed(() => {
  const info = connectInfo.value
  return !info || (!info.configured && !info.allow_no_token)
})
const connectSkipped = computed(
  () => connectStep.value?.status === 'skipped' || manifestTransport.value === 'outbound',
)

const currentWakeWord = computed(
  () => cleanWakeWords()[0] || t('setup.device.wakePlaceholder'),
)

// —————————————————————————————————————————————— formatting —————
function evidence(step: SetupStepState | undefined): Record<string, unknown> {
  const raw = step?.evidence
  return raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}
}

function detail(step: SetupStepState | undefined): Record<string, unknown> {
  const raw = step?.detail
  return raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}
}

function statusOf(step: SetupStepState): SetupStepStatus {
  // The server only reports `running` while a POST holds the lock; show the same
  // chip locally while this tab's request is in flight.
  if (busyStep.value === step.id) return 'running'
  if (step.id === 'verify' && verifySkipped.value && step.status !== 'done') return 'skipped'
  return step.status
}

function stepTitle(id: string | null | undefined): string {
  if (!id) return ''
  const key = STEP_TITLES[id]
  return key ? t(key) : localizeBackend(id)
}

function stepUser(id: string): string {
  const key = STEP_USER[id]
  return key ? t(key) : ''
}

function stepRui(id: string): string {
  const key = STEP_RUI[id]
  return key ? t(key) : ''
}

function statusLabel(step: SetupStepState): string {
  return t(STATUS_LABEL[statusOf(step)])
}

function statusClass(step: SetupStepState): string {
  return STATUS_CLASS[statusOf(step)]
}

function isExpanded(step: SetupStepState): boolean {
  return (
    expanded.value[step.id] ??
    (statusOf(step) !== 'done' && statusOf(step) !== 'skipped')
  )
}

function toggleStep(id: string): void {
  const step = stepById(id)
  if (!step) return
  expanded.value = { ...expanded.value, [id]: !isExpanded(step) }
}

function hasAction(step: SetupStepState, action: string): boolean {
  return step.actions?.includes(action) ?? false
}

function stageLabel(stage: Stage): string {
  return t(STAGE_LABELS[stage])
}

function flag(value: unknown): string {
  return value ? t('setup.v2.yes') : t('setup.v2.no')
}

function text(value: unknown): string {
  if (value === null || value === undefined || value === '') return t('setup.v2.none')
  if (Array.isArray(value)) return value.length ? value.map(String).join(', ') : t('setup.v2.none')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function evidenceText(step: SetupStepState): string {
  const ev = evidence(step)
  switch (step.id) {
    case 'keys': {
      const stages = (ev.stages ?? {}) as Record<string, unknown>
      const stagesText = STAGES.map(
        (stage) => `${stageLabel(stage)}: ${flag(stages[stage])}`,
      ).join(' · ')
      const missing = Array.isArray(ev.missing) ? ev.missing.map(String) : []
      return missing.length
        ? `${stagesText} · ${t('setup.missing', { items: missing.join(', ') })}`
        : stagesText
    }
    case 'models':
      return [
        `${t('setup.v2.models.asr')}: ${flag(ev.asr)}`,
        `${t('setup.v2.models.vad')}: ${flag(ev.vad)}`,
        `${t('setup.v2.models.kws')}: ${flag(ev.kws)}`,
      ].join(' · ')
    case 'prepare':
      return [
        `${t('setup.v2.prepare.artifact')}: ${artifactLabel(String(ev.artifact ?? 'none'))}`,
        `${t('setup.v2.prepare.confirmedLabel')}: ${flag(ev.confirmed)}`,
        ev.rollback_required
          ? `${t('setup.v2.prepare.rollbackAckLabel')}: ${flag(ev.rollback_ack)}`
          : '',
      ]
        .filter(Boolean)
        .join(' · ')
    case 'params':
      return [
        `${t('setup.v2.params.device')}: ${flag(ev.device)}`,
        `${t('setup.v2.params.adapter')}: ${text(ev.adapter)}`,
        Array.isArray(ev.missing) && ev.missing.length
          ? `${t('setup.missing', { items: ev.missing.map(String).join(', ') })}`
          : '',
        ev.plane_error ? `${t('setup.v2.audio.error')}: ${String(ev.plane_error)}` : '',
      ]
        .filter(Boolean)
        .join(' · ')
    case 'wake':
      return [
        `${t('setup.v2.params.wakeWords')}: ${text(ev.words)}`,
        `${t('setup.v2.wake.exists')}: ${flag(ev.exists)}`,
        ev.plane_error ? `${t('setup.v2.audio.error')}: ${String(ev.plane_error)}` : '',
      ]
        .filter(Boolean)
        .join(' · ')
    case 'connect':
      return [
        `${t('setup.v2.source')}: ${text(ev.transport)}`,
        `${t('setup.v2.connect.connected')}: ${flag(ev.speaker_connected)}`,
        `${t('setup.v2.connect.listenPort')}: ${text(ev.listen_port)}`,
      ].join(' · ')
    case 'test':
      return [
        `${t('setup.v2.test.stored')}: ${flag(ev.stored)}`,
        `${t('setup.v2.test.at', { at: text(ev.at) })}`,
        `${t('setup.v2.connect.connected')}: ${flag(ev.speaker_connected)}`,
      ].join(' · ')
    case 'verify':
      return [
        `${t('setup.v2.verify.audio', { n: text(ev.audio_bytes) })}`,
        `${t('setup.v2.verify.turn')}: ${flag(ev.turn)}`,
        `${t('setup.v2.verify.manual')}: ${flag(ev.manual)}`,
      ].join(' · ')
    default:
      return JSON.stringify(ev)
  }
}

function rawEvidence(step: SetupStepState): string {
  return JSON.stringify({ evidence: step.evidence, detail: step.detail }, null, 2)
}

function artifactLabel(artifact: string): string {
  const key = ARTIFACT_KEYS[artifact]
  return key ? t(key) : artifact
}

function trustLabel(trust: string | null): string {
  const key = trust ? TRUST_KEYS[trust] : undefined
  return key ? t(key) : trust || ''
}

function sourceLabel(source: string | null): string {
  const key = source ? SOURCE_KEYS[source] : undefined
  return key ? t(key) : source || ''
}

function classLabel(value: string | null): string {
  return value ? t('setup.v2.class', { value }) : ''
}

function plugReason(plug: SetupPlugSummary): string {
  const code = plug.error || ''
  const key = ERROR_KEYS[code]
  return key ? t(key) : code || t('setup.v2.none')
}

function blockedText(block: { code: string; message: string }): string {
  const key = ERROR_KEYS[block.code]
  return key ? t(key) : block.message
}

function selectInput(event: Event): void {
  const input = event.target as HTMLInputElement | null
  input?.select()
}

function plugOptionLabel(plug: SetupPlugSummary): string {
  const name = plug.name || plug.id || t('setup.v2.plugFailed')
  const bits: string[] = [name]
  if (plug.class) bits.push(classLabel(plug.class))
  if (plug.trust) bits.push(trustLabel(plug.trust))
  if (plug.source) bits.push(sourceLabel(plug.source))
  if (!plug.available) bits.push(`${t('setup.v2.plugUnavailable')}: ${plugReason(plug)}`)
  return bits.filter(Boolean).join(' · ')
}

function safeHref(url: string): string {
  return /^https?:\/\//i.test(url) ? url : ''
}

function checkLabel(check: TestCheck): string {
  const key = CHECK_LABELS[check.id ?? '']
  return key ? t(key) : check.id || t('setup.v2.details')
}

function checkDetail(check: TestCheck): string {
  const raw = check.detail ?? {}
  if (check.id === 'plane') {
    return [
      `${t('setup.v2.test.runningState')}: ${flag(raw.running)}`,
      raw.error ? `${t('setup.v2.test.error')}: ${String(raw.error)}` : '',
    ]
      .filter(Boolean)
      .join(' · ')
  }
  if (check.id === 'host') {
    const ssh =
      raw.ssh && typeof raw.ssh === 'object'
        ? (raw.ssh as Record<string, unknown>)
        : {}
    return [
      `${t('setup.v2.test.ssh')}: ${flag(ssh.port_open)}`,
      `${t('setup.v2.test.banner')}: ${flag(ssh.banner)}`,
      raw.hint ? String(raw.hint) : '',
    ]
      .filter(Boolean)
      .join(' · ')
  }
  return raw && Object.keys(raw).length ? JSON.stringify(raw) : ''
}

// ——————————————————————————————————————————————— params —————
function splitList(value: string): string[] {
  return value
    .split(/[\n,，;；]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function cleanWakeWords(): string[] {
  return wakeWords.value.map((word) => word.trim()).filter(Boolean)
}

function defaultText(spec: SetupParamSpec): string {
  if (spec.type === 'list') {
    return Array.isArray(spec.default) ? spec.default.map(String).join(', ') : ''
  }
  if (spec.type === 'bool') return ''
  return spec.default === null || spec.default === undefined ? '' : String(spec.default)
}

function defaultBool(spec: SetupParamSpec): boolean {
  return spec.default === true
}

function paramPending(spec: SetupParamSpec): boolean {
  return secretConfigured(spec.key)
}

function secretConfigured(key: string): boolean {
  if (!deviceId.value) return false
  return !missingParams.value.includes(key)
}

function initParamDrafts(): void {
  const nextTexts = { ...paramTexts.value }
  const nextBools = { ...paramBools.value }
  const nextSecrets = { ...secretDrafts.value }
  for (const spec of manifestParams.value) {
    if (RESERVED_PARAMS.has(spec.key)) continue
    if (spec.type === 'secret') {
      if (!(spec.key in nextSecrets)) nextSecrets[spec.key] = ''
      continue
    }
    if (spec.type === 'bool') {
      if (!(spec.key in nextBools)) nextBools[spec.key] = defaultBool(spec)
      continue
    }
    if (!(spec.key in nextTexts)) nextTexts[spec.key] = defaultText(spec)
  }
  paramTexts.value = nextTexts
  paramBools.value = nextBools
  secretDrafts.value = nextSecrets
  if (!wakeWords.value.length) wakeWords.value = ['']
  if (!deviceId.value) {
    const roomDefault = roomSpec.value?.default
    if (!roomDraft.value && roomDefault !== null && roomDefault !== undefined) {
      roomDraft.value = String(roomDefault)
    }
    const hostDefault = hostSpec.value?.default
    if (!hostDraft.value && hostDefault !== null && hostDefault !== undefined) {
      hostDraft.value = String(hostDefault)
    }
    const wakeDefault = wakeSpec.value?.default
    if (
      wakeWords.value.length === 1 &&
      !wakeWords.value[0].trim() &&
      Array.isArray(wakeDefault) &&
      wakeDefault.length
    ) {
      wakeWords.value = wakeDefault.map(String).slice(0, 3)
    }
  }
}

function prefillParams(device: DeviceDoc, clearSecrets: boolean): void {
  const texts: Record<string, string> = {}
  const bools: Record<string, boolean> = {}
  const secrets: Record<string, string> = clearSecrets ? {} : { ...secretDrafts.value }
  const stored = device.params && typeof device.params === 'object' ? device.params : {}
  for (const spec of manifestParams.value) {
    if (RESERVED_PARAMS.has(spec.key)) continue
    if (spec.type === 'secret') {
      if (!(spec.key in secrets)) secrets[spec.key] = ''
      continue
    }
    const raw = stored[spec.key]
    if (spec.type === 'bool') {
      bools[spec.key] = raw === undefined || raw === null ? defaultBool(spec) : Boolean(raw)
      continue
    }
    if (raw === undefined || raw === null) {
      texts[spec.key] = defaultText(spec)
    } else if (spec.type === 'list') {
      texts[spec.key] = Array.isArray(raw) ? raw.map(String).join(', ') : String(raw)
    } else {
      texts[spec.key] = String(raw)
    }
  }
  paramTexts.value = texts
  paramBools.value = bools
  secretDrafts.value = secrets
}

function setFieldError(key: string, message: string): void {
  fieldErrors.value = { ...fieldErrors.value, [key]: message }
}

function validateWakeWords(): string {
  const words = cleanWakeWords()
  if (!words.length) return t('setup.v2.wake.empty')
  if (words.length > 3) return t('setup.v2.wake.hint')
  for (const word of words) {
    if (word.length > 12) return t('setup.v2.wake.tooLong')
    if (/\s/.test(word) || /[\u0000-\u001f]/.test(word)) return t('setup.v2.wake.hint')
  }
  return ''
}

function validateParams(): boolean {
  fieldErrors.value = {}
  const host = hostDraft.value.trim()
  if (!host) {
    setFieldError('host', t('setup.v2.params.invalidRequired'))
  } else if (host.length > 64 || /\s/.test(host) || host.includes('://') || host.startsWith('-') || host.endsWith('-')) {
    setFieldError('host', t('setup.v2.params.hostInvalid'))
  }
  if (roomDraft.value.trim().length > 32 || /[\u0000-\u001f]/.test(roomDraft.value)) {
    setFieldError('room', t('setup.v2.params.roomInvalid'))
  }
  const wakeError = validateWakeWords()
  if (wakeError) setFieldError('wake_words', wakeError)
  for (const spec of extraParams.value) {
    if (spec.type === 'secret') {
      const value = (secretDrafts.value[spec.key] ?? '').trim()
      if (spec.required && !value && !secretConfigured(spec.key)) {
        setFieldError(spec.key, t('setup.v2.params.invalidRequired'))
      }
      continue
    }
    if (spec.type === 'bool') continue
    const raw = paramTexts.value[spec.key] ?? ''
    const value = raw.trim()
    if (spec.required && !value) {
      setFieldError(spec.key, t('setup.v2.params.invalidRequired'))
      continue
    }
    if (!value) continue
    if (spec.type === 'int' && !/^-?\d+$/.test(value)) {
      setFieldError(spec.key, t('setup.v2.params.invalidInt'))
    }
    if (spec.type === 'choice' && !(spec.options ?? []).includes(value)) {
      setFieldError(spec.key, t('setup.v2.params.invalidChoice'))
    }
    if (spec.type === 'list') {
      const items = splitList(value)
      if (spec.max !== null && spec.max !== undefined && items.length > spec.max) {
        setFieldError(spec.key, t('setup.v2.params.max', { n: spec.max }))
      }
    }
  }
  return Object.keys(fieldErrors.value).length === 0
}

function draftValue(spec: SetupParamSpec): unknown {
  if (spec.type === 'bool') return Boolean(paramBools.value[spec.key])
  const raw = String(paramTexts.value[spec.key] ?? '')
  if (spec.type === 'int') {
    const parsed = Number.parseInt(raw, 10)
    return Number.isFinite(parsed) ? parsed : 0
  }
  if (spec.type === 'list') return splitList(raw)
  return raw.trim()
}

function buildParamsPayload(): Record<string, unknown> {
  const params: Record<string, unknown> = {}
  for (const spec of manifestParams.value) {
    if (RESERVED_PARAMS.has(spec.key)) continue
    if (spec.type === 'secret') {
      const value = (secretDrafts.value[spec.key] ?? '').trim()
      if (value) params[spec.key] = value
      continue
    }
    // Empty optional fields are omitted, so a merge keeps the stored value
    // instead of overwriting it with "" or 0.
    if (spec.type !== 'bool' && !String(paramTexts.value[spec.key] ?? '').trim()) continue
    params[spec.key] = draftValue(spec)
  }
  const values: Record<string, unknown> = {
    id: deviceDraft.value?.id ?? deviceId.value ?? 'living',
    host: hostDraft.value.trim(),
    room: roomDraft.value.trim(),
    wake_words: cleanWakeWords(),
    params,
  }
  const adapter = selectedDeviceManifest.value?.id
  if (adapter) values.adapter = adapter
  return values
}

// ————————————————————————————————————————————— error text —————
function describeError(exc: unknown): StepIssue {
  const object = apiErrorObject(exc)
  if (object) {
    const key = ERROR_KEYS[object.code]
    const serverMessage = pickLocalized(object.message)
    return {
      code: object.code,
      message: key ? t(key) : serverMessage || t('common.error'),
      hint: key && serverMessage ? serverMessage : '',
      details:
        Object.keys(object.details).length > 0
          ? JSON.stringify(object.details, null, 2)
          : '',
      issues: [],
    }
  }
  if (exc instanceof ApiError) {
    const raw = exc.detail
    if (Array.isArray(raw)) {
      const list = raw as ValidationIssue[]
      const lines = list.map((issue) => {
        const path = (issue.loc ?? []).slice(1).join('.')
        const message = localizeIssue(issue.msg)
        return path ? `${path}: ${message}` : message
      })
      return {
        code: 'invalid_values',
        message: t('setup.error.invalid_values'),
        hint: lines.join('; '),
        details: JSON.stringify(
          list.map((issue) => ({ loc: issue.loc ?? [], msg: issue.msg })),
          null,
          2,
        ),
        issues: list,
      }
    }
    return {
      code: `http_${exc.status}`,
      message:
        typeof raw === 'string' ? localizeBackend(raw) : exc.message || t('common.error'),
      hint: '',
      details:
        raw && typeof raw === 'object' ? JSON.stringify(raw, null, 2) : '',
      issues: [],
    }
  }
  return {
    code: 'unknown',
    message: exc instanceof Error ? exc.message : String(exc),
    hint: '',
    details: '',
    issues: [],
  }
}

// ————————————————————————————————————————————— diagnostics —————
/** Evidence is safe to copy except the keywords path; keep it symbolic. */
function safeEvidence(step: SetupStepState): Record<string, unknown> {
  const ev = { ...step.evidence }
  if (step.id === 'wake' && 'path' in ev) ev.path = '<keywords-file>'
  return ev
}

function diagnostics(stepId?: string): Record<string, unknown> {
  const current = state.value
  const step = stepId ? stepById(stepId) : undefined
  const issue = stepId ? issues.value[stepId] : undefined
  const statuses: Record<string, string> = {}
  for (const item of current?.steps ?? []) statuses[item.id] = statusOf(item)
  const paramsEvidence = evidence(stepById('params'))
  const wakeEvidence = evidence(stepById('wake'))
  return {
    version: 1,
    plug: current
      ? {
          id: current.plug.id,
          source: current.plug.source,
          trust: current.plug.trust,
          class: current.plug.class,
          available: current.plug.available,
        }
      : null,
    step: step
      ? {
          id: step.id,
          status: statusOf(step),
          evidence: safeEvidence(step),
          last_error: issue ? { code: issue.code, message: issue.message } : null,
        }
      : null,
    statuses,
    next_step: current?.next_step ?? null,
    complete: current?.complete ?? false,
    verified: current?.verified ?? false,
    plane_error: current?.plane_error ?? '',
    // Host, token, key values and full config paths are deliberately absent.
    device: paramsEvidence.device
      ? {
          id: paramsEvidence.device,
          adapter: paramsEvidence.adapter ?? null,
          enabled: paramsEvidence.enabled ?? null,
          params: detail(stepById('params')).params ?? [],
        }
      : null,
    keywords_exists: wakeEvidence.exists ?? null,
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
  }
}

function copyDiagnostics(stepId?: string): void {
  void copyText(JSON.stringify(diagnostics(stepId), null, 2), `diag:${stepId ?? 'all'}`)
}

// ————————————————————————————————————————————— data loading —————
async function loadPlugDetail(id: string): Promise<void> {
  plugDetailError.value = ''
  if (!id) {
    plugDetail.value = null
    return
  }
  try {
    plugDetail.value = await api.plug(id)
    plugDetailError.value = ''
  } catch (exc) {
    plugDetail.value = null
    plugDetailError.value = describeError(exc).message
  }
}

function deviceIdFromResponse(response: SetupStepResponse): string | null {
  const step = response.steps.find((item) => item.id === 'params')
  const raw = step?.evidence?.device
  return typeof raw === 'string' && raw ? raw : null
}

function initExpanded(response: SetupStepResponse, force: boolean): void {
  const next = { ...expanded.value }
  for (const step of response.steps) {
    if (force || next[step.id] === undefined) {
      next[step.id] = step.status !== 'done' && step.status !== 'skipped'
    }
  }
  if (verifySkipped.value) next.verify = false
  expanded.value = next
}

async function syncDeviceDraft(id: string, force: boolean): Promise<void> {
  const key = `${selectedPlug.value}:${id}`
  if (!force && deviceDraftKey.value === key) return
  try {
    const detail = await api.deviceDetail(id)
    deviceDraft.value = detail.device
    const words = Array.isArray(detail.device.wake_words) ? detail.device.wake_words : []
    hostDraft.value = detail.device.host ?? ''
    roomDraft.value = detail.device.room ?? ''
    wakeWords.value = (words.length ? words : ['']).slice(0, 3)
    prefillParams(detail.device, force)
    deviceDraftKey.value = key
  } catch {
    // The step evidence is still usable; leave the user's drafts untouched.
  }
}

function resetDeviceDraft(): void {
  if (deviceDraftKey.value === '' && deviceDraft.value === null) return
  deviceDraft.value = null
  deviceDraftKey.value = ''
  hostDraft.value = ''
  roomDraft.value = ''
  wakeWords.value = ['']
  paramTexts.value = {}
  paramBools.value = {}
  secretDrafts.value = {}
}

async function loadConnect(id: string | null, force: boolean): Promise<void> {
  if (!id || connectSkipped.value) {
    connectInfo.value = null
    connectError.value = ''
    connectKey.value = ''
    return
  }
  const key = `${selectedPlug.value}:${id}`
  if (!force && connectKey.value === key) return
  connectBusy.value = true
  connectError.value = ''
  try {
    connectInfo.value = await api.deviceConnect(id)
    connectKey.value = key
    tokenVisible.value = false
  } catch (exc) {
    connectInfo.value = null
    connectError.value = describeError(exc).message
  } finally {
    connectBusy.value = false
  }
}

async function applyState(
  next: SetupStepResponse,
  options: { force?: boolean; prefill?: boolean; forceConnect?: boolean } = {},
): Promise<void> {
  state.value = next
  const nextPlug = next.plug.id ?? ''
  if (nextPlug && nextPlug !== selectedPlug.value) {
    selectedPlug.value = nextPlug
    options.force = true
  }
  initExpanded(next, options.force === true)
  if (
    options.force === true ||
    !plugDetail.value ||
    plugDetail.value.manifest.id !== selectedPlug.value
  ) {
    await loadPlugDetail(selectedPlug.value)
  }
  const id = deviceIdFromResponse(next)
  if (id) {
    await syncDeviceDraft(id, options.prefill === true || options.force === true)
  } else {
    resetDeviceDraft()
  }
  await loadConnect(id, options.forceConnect === true)
  initParamDrafts()
}

async function refreshState(
  options: {
    silent?: boolean
    force?: boolean
    prefill?: boolean
    forceConnect?: boolean
  } = {},
): Promise<boolean> {
  if (busyStep.value) return false
  if (!options.silent) {
    busyGlobal.value = 'recheck'
    loadError.value = ''
  }
  try {
    const next = await api.setupStepState(selectedPlug.value || undefined)
    await applyState(next, options)
    if (!options.silent) notice.value = t('setup.v2.rechecked')
    return true
  } catch (exc) {
    if (!options.silent) loadError.value = describeError(exc).message
    return false
  } finally {
    if (!options.silent) busyGlobal.value = ''
  }
}

async function loadInitial(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const [list, doc] = await Promise.all([api.plugs(), api.catalog().catch(() => null)])
    plugs.value = Array.isArray(list.plugs) ? list.plugs : []
    catalog.value = doc
    const next = await api.setupStepState()
    await applyState(next, { force: true, prefill: true, forceConnect: true })
  } catch (exc) {
    loadError.value = describeError(exc).message
  } finally {
    loading.value = false
  }
}

async function onPlugChange(event: Event): Promise<void> {
  const value = (event.target as HTMLSelectElement).value
  if (!value || value === selectedPlug.value) return
  selectedPlug.value = value
  deviceDraftKey.value = ''
  await refreshState({ force: true, prefill: true, forceConnect: true })
}

// ————————————————————————————————————————————— actions —————
async function runStep(
  stepId: string,
  action: string,
  values: Record<string, unknown>,
  options: RunOptions = {},
): Promise<boolean> {
  if (busyStep.value || busyGlobal.value) return false
  busyStep.value = stepId
  const remaining = { ...issues.value }
  delete remaining[stepId]
  issues.value = remaining
  delete retryFns[stepId]
  expanded.value = { ...expanded.value, [stepId]: true }
  try {
    const next = await api.setupStep({
      plug: selectedPlug.value,
      step: stepId,
      action,
      values,
    })
    await applyState(next, {
      prefill: options.forcePrefill === true,
      forceConnect: options.forceConnect === true,
    })
    const status = next.steps.find((step) => step.id === stepId)?.status
    expanded.value = { ...expanded.value, [stepId]: status !== 'done' && status !== 'skipped' }
    return true
  } catch (exc) {
    issues.value = { ...issues.value, [stepId]: describeError(exc) }
    retryFns[stepId] = options.retry ?? (() => runStep(stepId, action, values, options))
    expanded.value = { ...expanded.value, [stepId]: true }
    return false
  } finally {
    busyStep.value = ''
  }
}

function retryStep(stepId: string): void {
  const retry = retryFns[stepId]
  if (retry) void retry()
  else void refreshState({ force: true })
}

async function saveKeys(): Promise<void> {
  const values: Record<string, string> = {}
  for (const [name, value] of Object.entries(keyDrafts.value)) {
    if (value.trim()) values[name] = value.trim()
  }
  if (!Object.keys(values).length) return
  const ok = await runStep('keys', 'keys.save', { values }, { retry: saveKeys })
  if (ok) {
    keyDrafts.value = {}
    catalog.value = await api.catalog().catch(() => catalog.value)
    notice.value = t('setup.v2.keys.saved')
  }
}

async function saveParams(): Promise<void> {
  if (unsupportedParam.value) return
  if (!validateParams()) return
  const ok = await runStep('params', 'device.upsert', buildParamsPayload(), {
    retry: saveParams,
    forcePrefill: true,
    forceConnect: true,
  })
  if (ok) {
    secretDrafts.value = {}
    notice.value = t('setup.v2.params.saved')
  }
}

async function confirmPrepare(): Promise<void> {
  if (!prepareConfirmed.value) return
  if (manifestClass.value === 'B' && !prepareRollbackAck.value) return
  const ok = await runStep(
    'prepare',
    'manual.confirm',
    { confirmed: true, rollback_ack: prepareRollbackAck.value },
    { retry: confirmPrepare },
  )
  if (ok) {
    prepareConfirmed.value = false
    prepareRollbackAck.value = false
    notice.value = t('setup.v2.prepare.confirmed')
  }
}

function addWakeWord(): void {
  if (wakeWords.value.length >= 3) return
  wakeWords.value = [...wakeWords.value, '']
}

function removeWakeWord(index: number): void {
  if (wakeWords.value.length <= 1) return
  wakeWords.value = wakeWords.value.filter((_, at) => at !== index)
}

async function saveWake(): Promise<void> {
  if (!deviceId.value || wakeErrorText.value) return
  const values: Record<string, unknown> = { wake_words: cleanWakeWords() }
  if (deviceId.value) values.device_id = deviceId.value
  const ok = await runStep('wake', 'wake.set', values, { retry: saveWake })
  if (ok) notice.value = t('setup.v2.wake.saved')
}

async function inspectConnect(): Promise<void> {
  if (!deviceId.value) return
  const ok = await runStep('connect', 'connect.inspect', {}, { retry: inspectConnect })
  if (ok) await loadConnect(deviceId.value, true)
}

async function runTest(): Promise<void> {
  if (!deviceId.value) return
  await runStep(
    'test',
    'device.test',
    { device_id: deviceId.value },
    { retry: runTest },
  )
}

function stopWait(): void {
  if (waitTimer !== undefined) {
    window.clearInterval(waitTimer)
    waitTimer = undefined
  }
  waiting.value = false
}

async function pollConnect(): Promise<void> {
  if (busyGlobal.value || busyStep.value) return
  waitTicks.value += 1
  await refreshState({ silent: true })
  if (stepById('connect')?.status === 'done') {
    stopWait()
    notice.value = t('setup.v2.connect.connected')
    return
  }
  if (waitTicks.value >= 20) {
    stopWait()
    notice.value = t('setup.v2.connect.waitTimeout')
  }
}

function toggleWait(): void {
  if (waiting.value) {
    stopWait()
    return
  }
  waiting.value = true
  waitTicks.value = 0
  waitTimer = window.setInterval(() => void pollConnect(), 3000)
}

async function runVerify(): Promise<void> {
  const body = verifyText.value.trim()
  if (!body || busyStep.value || !deviceId.value) return
  verifySkipped.value = false
  verifyReply.value = ''
  verifyError.value = ''
  verifyAudio.value = 0
  busyStep.value = 'verify'
  const remaining = { ...issues.value }
  delete remaining.verify
  issues.value = remaining
  expanded.value = { ...expanded.value, verify: true }
  try {
    const next = await api.setupStep({
      plug: selectedPlug.value,
      step: 'verify',
      action: 'try.run',
      values: { text: body },
    })
    await applyState(next)
    const turns = await api
      .json<{ last: LastTurn | null }>('/api/v1/turns?limit=1')
      .catch(() => null)
    const last = turns?.last ?? null
    verifyReply.value = last?.reply ?? ''
    verifyAudio.value = Number(last?.audio_bytes ?? evidence(stepById('verify')).audio_bytes ?? 0) || 0
    if (last?.error) verifyError.value = String(last.error)
    else if (!verifyAudio.value) verifyError.value = t('setup.v2.verify.noAudio')
  } catch (exc) {
    issues.value = { ...issues.value, verify: describeError(exc) }
    retryFns.verify = runVerify
  } finally {
    busyStep.value = ''
  }
}

async function manualVerify(): Promise<void> {
  const ok = await runStep('verify', 'manual.confirm', { confirmed: true }, {
    retry: manualVerify,
  })
  if (ok) notice.value = t('setup.v2.verify.manualDone')
}

function skipVerify(): void {
  verifySkipped.value = true
  const remaining = { ...issues.value }
  delete remaining.verify
  issues.value = remaining
  delete retryFns.verify
  expanded.value = { ...expanded.value, verify: false }
}

function unskipVerify(): void {
  verifySkipped.value = false
  expanded.value = { ...expanded.value, verify: true }
}

onMounted(loadInitial)
onBeforeUnmount(stopWait)
</script>

<template>
  <header class="page-head setup-head">
    <div>
      <div class="kicker">{{ t('setup.kicker') }}</div>
      <h1>{{ t('setup.title') }}</h1>
      <p class="lede">{{ t('setup.lede') }}</p>
    </div>
    <div v-if="state" class="page-head-side setup-head-side">
      <span class="badge" :class="state.complete ? 'badge-ok' : 'badge-warn'">
        {{
          state.complete
            ? state.verified
              ? t('setup.allDone')
              : t('setup.v2.completeUnverified')
            : t('setup.next', { step: stepTitle(state.next_step) })
        }}
      </span>
      <span class="badge" :class="audioClass">
        {{ t('setup.v2.audio') }}: {{ audioLabel }}
      </span>
      <span class="badge badge-muted mono">
        {{ t('setup.v2.progress', { done: doneCount, total: totalCount }) }}
      </span>
    </div>
  </header>

  <div class="setup-plugbar">
    <label class="field-label" for="setup-plug">{{ t('setup.v2.plug') }}</label>
    <select
      id="setup-plug"
      :value="selectedPlug"
      :disabled="loading || plugLocked"
      @change="onPlugChange"
    >
      <option v-for="plug in plugOptions" :key="plug.id || ''" :value="plug.id || ''" :disabled="!plug.available">
        {{ plugOptionLabel(plug) }}
      </option>
      <option v-if="!plugOptions.length" value="" disabled>{{ t('setup.v2.noPlugs') }}</option>
    </select>
    <span v-if="plugLocked" class="field-hint">{{ t('setup.v2.plugLocked') }}</span>
    <span v-else class="field-hint">{{ t('setup.v2.plugHint') }}</span>
    <div class="setup-plugbar-actions">
      <button type="button" class="btn btn-sm" :disabled="busyGlobal !== '' || busyStep !== '' || loading" @click="refreshState({ force: true })">
        {{ busyGlobal === 'recheck' ? t('common.loading') : t('setup.v2.recheck') }}
      </button>
      <button type="button" class="btn btn-sm" @click="copyDiagnostics()">
        {{ copied === 'diag:all' ? t('setup.v2.diagCopied') : t('setup.v2.copyDiag') }}
      </button>
    </div>
  </div>

  <div v-if="selectedPlugSummary" class="setup-plugmeta">
    <div class="setup-plugmeta-primary">
      <strong>{{ selectedPlugSummary.name || selectedPlugSummary.id }}</strong>
      <span v-if="selectedPlugSummary.version" class="badge badge-muted mono">{{ selectedPlugSummary.version }}</span>
      <span v-if="selectedPlugSummary.class" class="badge badge-accent">{{ classLabel(selectedPlugSummary.class) }}</span>
      <span v-if="selectedPlugSummary.trust" class="badge">{{ trustLabel(selectedPlugSummary.trust) }}</span>
      <span v-if="selectedPlugSummary.source" class="badge badge-muted">{{ sourceLabel(selectedPlugSummary.source) }}</span>
      <span v-if="manifestVerified === true" class="badge badge-ok">{{ t('setup.v2.verified') }}</span>
      <span v-else-if="manifestVerified === false" class="badge badge-warn">{{ t('setup.v2.unverified') }}</span>
    </div>
    <p v-if="manifestVerified === false" class="field-hint">
      {{ t('setup.v2.unverifiedHint') }}
    </p>
    <div v-if="capabilities.length" class="setup-caps">
      <span class="field-hint">{{ t('setup.v2.capabilities') }}</span>
      <span v-for="cap in capabilities" :key="cap" class="tag mono">{{ cap }}</span>
    </div>
    <div v-if="failedPlugs.length" class="setup-failed-plugs">
      <span v-for="(plug, index) in failedPlugs" :key="index" class="badge badge-fail">
        {{ t('setup.v2.plugFailed') }}: {{ plug.error || t('common.na') }}
      </span>
    </div>
    <p v-if="plugDetailError" class="note fail" role="alert">{{ plugDetailError }}</p>
  </div>

  <div v-if="loadError" class="banner banner-fail" role="alert">
    <AppIcon name="alert" :size="18" class="banner-icon" />
    <span>{{ t('setup.v2.loadError', { message: loadError }) }}</span>
    <button type="button" class="btn btn-sm" @click="loadInitial">{{ t('setup.v2.retryLoad') }}</button>
  </div>

  <div v-if="blockReason" class="banner banner-fail" role="alert">
    <AppIcon name="alert" :size="18" class="banner-icon" />
    <span>{{ t('setup.v2.blocked', { reason: blockedText(blockReason) }) }}</span>
  </div>

  <div v-if="notice" class="banner banner-accent" role="status">
    <AppIcon name="check" :size="18" class="banner-icon" />
    <span>{{ notice }}</span>
  </div>

  <p v-if="copyFailed" class="note fail" role="alert">{{ t('setup.v2.copyFailed') }}</p>

  <div v-if="!state && loading" class="setup-loading" role="status">
    <div class="skeleton skeleton-line" style="width: 60%" />
    <div class="skeleton skeleton-line" style="width: 42%" />
    <p class="note">{{ t('setup.v2.loading') }}</p>
  </div>

  <template v-if="state">
    <section
      v-for="(step, index) in steps"
      :key="step.id"
      class="setup-section"
      :class="{ 'setup-section-collapsed': !isExpanded(step) }"
    >
      <div class="setup-index">{{ String(index + 1).padStart(2, '0') }}</div>
      <div class="setup-body">
        <div class="setup-step-head">
          <div class="setup-step-title">
            <h2>{{ stepTitle(step.id) }}</h2>
            <span class="badge" :class="statusClass(step)">{{ statusLabel(step) }}</span>
            <span v-if="step.id === 'verify'" class="badge badge-muted">{{ t('setup.v2.optional') }}</span>
            <span v-if="step.status === 'error' && state.plane_error" class="badge badge-fail">
              {{ t('setup.v2.audio.error') }}
            </span>
          </div>
          <button type="button" class="btn btn-sm" @click="toggleStep(step.id)">
            {{ isExpanded(step) ? t('setup.v2.collapse') : t('setup.v2.expand') }}
          </button>
        </div>

        <p class="setup-evidence-line">
          <span class="setup-evidence-label">{{ t('setup.v2.evidence') }}</span>
          <span class="mono">{{ evidenceText(step) }}</span>
        </p>

        <div v-if="isExpanded(step)" class="setup-step-content">
          <div class="setup-role-pair">
            <div>
              <strong>{{ t('setup.v2.user') }}</strong>
              <p>{{ stepUser(step.id) }}</p>
            </div>
            <div>
              <strong>{{ t('setup.v2.rui') }}</strong>
              <p>{{ stepRui(step.id) }}</p>
            </div>
          </div>

          <div v-if="issues[step.id]" class="setup-error" role="alert">
            <div class="setup-error-head">
              <span class="badge badge-fail mono">{{ issues[step.id]?.code }}</span>
              <strong>{{ issues[step.id]?.message }}</strong>
            </div>
            <p v-if="issues[step.id]?.hint" class="field-hint">{{ issues[step.id]?.hint }}</p>
            <details v-if="issues[step.id]?.details" class="setup-raw">
              <summary>{{ t('setup.v2.errorDetail') }}</summary>
              <pre class="cmd mono">{{ issues[step.id]?.details }}</pre>
              <button type="button" class="btn btn-sm" @click="copyText(issues[step.id]?.details, `err:${step.id}`)">
                {{ copied === `err:${step.id}` ? t('common.copied') : t('common.copy') }}
              </button>
            </details>
            <div class="actions">
              <button type="button" class="btn btn-sm" :disabled="busyStep !== '' || busyGlobal !== ''" @click="retryStep(step.id)">
                {{ t('setup.v2.retry') }}
              </button>
              <button type="button" class="btn btn-sm" :disabled="busyStep !== '' || busyGlobal !== ''" @click="refreshState({ force: true })">
                {{ t('setup.v2.recheck') }}
              </button>
            </div>
          </div>

          <!-- keys: write-only provider keys, presence from evidence -->
          <template v-if="step.id === 'keys'">
            <p class="field-hint">{{ t('setup.keys.lede') }}</p>
            <div v-if="requiredKeys.length" class="setup-fields">
              <div v-for="key in requiredKeys" :key="key.name" class="field-row setup-key-row">
                <div class="field-meta">
                  <div class="field-label"><code class="mono">{{ key.name }}</code></div>
                  <div class="setup-key-tags">
                    <span v-for="stage in key.stages" :key="stage" class="badge">{{ stageLabel(stage) }}</span>
                    <span class="badge" :class="key.missing ? 'badge-warn' : 'badge-ok'">
                      {{ key.missing ? t('setup.keys.missing') : t('setup.keys.configured') }}
                    </span>
                    <span v-if="key.providers.length" class="field-hint">
                      {{ t('setup.keys.usedBy', { providers: key.providers.map(providerName).join(' / ') }) }}
                    </span>
                  </div>
                  <p v-if="key.hint" class="field-hint setup-key-hint">{{ key.hint }}</p>
                </div>
                <div class="field-control">
                  <input
                    :id="`setup-key-${key.name}`"
                    v-model="keyDrafts[key.name]"
                    type="password"
                    autocomplete="off"
                    spellcheck="false"
                    :placeholder="t('secrets.placeholder')"
                  />
                </div>
              </div>
            </div>
            <p v-else-if="!catalog" class="field-hint">{{ t('setup.keys.unavailable') }}</p>
            <p v-else class="field-hint">{{ t('setup.v2.keys.none') }}</p>
            <div class="actions">
              <button
                type="button"
                class="btn btn-primary"
                :disabled="busyStep !== '' || busyGlobal !== '' || !anyKeyInput"
                @click="saveKeys"
              >
                {{ busyStep === 'keys' ? t('common.saving') : t('setup.v2.keys.save') }}
              </button>
            </div>
            <p class="field-hint">{{ t('setup.keys.writeOnly') }}</p>
          </template>

          <!-- models: read-only evidence -->
          <template v-else-if="step.id === 'models'">
            <p class="field-hint">{{ t('setup.v2.models.note') }}</p>
            <div class="setup-evidence-grid">
              <span>{{ t('setup.v2.models.asr') }} <strong :class="evidence(step).asr ? 'ok' : 'fail'">{{ flag(evidence(step).asr) }}</strong></span>
              <span>{{ t('setup.v2.models.vad') }} <strong :class="evidence(step).vad ? 'ok' : 'fail'">{{ flag(evidence(step).vad) }}</strong></span>
              <span>{{ t('setup.v2.models.kws') }} <strong :class="evidence(step).kws ? 'ok' : 'fail'">{{ flag(evidence(step).kws) }}</strong></span>
            </div>
            <div class="actions">
              <button type="button" class="btn" :disabled="busyGlobal !== '' || busyStep !== ''" @click="refreshState({ force: true })">
                {{ t('setup.models.refresh') }}
              </button>
            </div>
          </template>

          <!-- prepare: manifest device_side + manual confirmation -->
          <template v-else-if="step.id === 'prepare'">
            <div v-if="manifestClass === 'B'" class="setup-risk">
              <strong>{{ t('setup.v2.prepare.riskTitle') }}</strong>
              <p>{{ t('setup.v2.prepare.riskBody') }}</p>
            </div>
            <div v-if="deviceSide && deviceSide.artifact !== 'none'" class="setup-manifest">
              <div class="setup-manifest-row">
                <span class="field-label">{{ t('setup.v2.prepare.artifact') }}</span>
                <span class="badge">{{ artifactLabel(deviceSide.artifact) }}</span>
                <span v-if="deviceSide.version_min" class="field-hint">
                  {{ t('setup.v2.prepare.versionMin') }}: <code class="mono">{{ deviceSide.version_min }}</code>
                </span>
              </div>
              <div v-if="deviceSide.name" class="setup-manifest-row">
                <span class="field-label">{{ t('setup.v2.prepare.source') }}</span>
                <span>{{ deviceSide.name }}</span>
                <a v-if="safeHref(deviceSide.source)" :href="safeHref(deviceSide.source)" target="_blank" rel="noopener noreferrer">
                  {{ t('setup.v2.prepare.openLink') }} <AppIcon name="external" :size="12" />
                </a>
                <code v-else-if="deviceSide.source" class="mono">{{ deviceSide.source }}</code>
              </div>
              <div v-if="deviceSide.install_steps.length" class="setup-manifest-row setup-manifest-block">
                <span class="field-label">{{ t('setup.v2.prepare.steps') }}</span>
                <ol>
                  <li v-for="(line, at) in deviceSide.install_steps" :key="at">{{ line }}</li>
                </ol>
              </div>
              <div v-if="deviceSide.rollback" class="setup-manifest-row setup-manifest-block">
                <span class="field-label">{{ t('setup.v2.prepare.rollback') }}</span>
                <p>{{ deviceSide.rollback }}</p>
              </div>
              <p v-else-if="manifestClass === 'B'" class="note fail">{{ t('setup.v2.prepare.rollbackMissing') }}</p>
              <p v-if="evidence(step).confirmed" class="ok">{{ t('setup.v2.prepare.confirmed') }}</p>
            </div>
            <p v-else-if="deviceSide" class="field-hint">{{ t('setup.v2.prepare.noArtifact') }}</p>

            <div v-if="hasAction(step, 'manual.confirm') && deviceSide && deviceSide.artifact !== 'none'" class="setup-fields">
              <label class="setup-check">
                <input v-model="prepareConfirmed" type="checkbox" :disabled="busyStep !== ''" />
                <span>{{ t('setup.v2.prepare.confirm') }}</span>
              </label>
              <label v-if="manifestClass === 'B'" class="setup-check">
                <input v-model="prepareRollbackAck" type="checkbox" :disabled="busyStep !== ''" />
                <span>{{ t('setup.v2.prepare.rollbackAck') }}</span>
              </label>
              <div class="actions">
                <button
                  type="button"
                  class="btn btn-primary"
                  :disabled="busyStep !== '' || busyGlobal !== '' || !prepareConfirmed || (manifestClass === 'B' && !prepareRollbackAck)"
                  @click="confirmPrepare"
                >
                  {{ busyStep === 'prepare' ? t('common.saving') : t('setup.v2.prepare.submit') }}
                </button>
                <a v-if="deviceSide && safeHref(deviceSide.source)" :href="safeHref(deviceSide.source)" target="_blank" rel="noopener noreferrer" class="btn btn-sm">
                  {{ t('setup.v2.prepare.openLink') }}
                </a>
              </div>
            </div>
            <p v-else-if="!deviceSide" class="field-hint">{{ t('setup.v2.params.noManifest') }}</p>
          </template>

          <!-- params: manifest-driven form, submitted through device.upsert -->
          <template v-else-if="step.id === 'params'">
            <p v-if="!selectedDeviceManifest" class="field-hint">{{ t('setup.v2.params.noManifest') }}</p>
            <template v-else>
              <p v-if="unsupportedParam" class="note fail">
                {{ t('setup.v2.params.unsupported', { type: unsupportedParam.type }) }}
              </p>
              <div class="setup-fields">
                <div class="field-row" :class="{ invalid: fieldErrors.host }">
                  <div class="field-meta">
                    <label class="field-label" for="setup-param-host">{{ hostSpec?.label || t('setup.v2.params.host') }}</label>
                    <span v-if="hostSpec?.required" class="badge badge-muted">{{ t('setup.v2.required') }}</span>
                  </div>
                  <div class="field-control">
                    <input
                      id="setup-param-host"
                      v-model="hostDraft"
                      :placeholder="hostSpec?.placeholder || t('setup.v2.params.hostPlaceholder')"
                      autocomplete="off"
                      spellcheck="false"
                    />
                    <p v-if="fieldErrors.host" class="field-hint fail">{{ fieldErrors.host }}</p>
                    <p v-else-if="hostSpec?.help" class="field-hint">{{ hostSpec.help }}</p>
                  </div>
                </div>

                <div class="field-row" :class="{ invalid: fieldErrors.room }">
                  <div class="field-meta">
                    <label class="field-label" for="setup-param-room">{{ roomSpec?.label || t('setup.v2.params.room') }}</label>
                  </div>
                  <div class="field-control">
                    <input
                      id="setup-param-room"
                      v-model="roomDraft"
                      :placeholder="roomSpec?.placeholder || t('setup.v2.params.roomPlaceholder')"
                    />
                    <p v-if="fieldErrors.room" class="field-hint fail">{{ fieldErrors.room }}</p>
                  </div>
                </div>

                <div class="field-row" :class="{ invalid: fieldErrors.wake_words }">
                  <div class="field-meta">
                    <label class="field-label" for="setup-param-wake">{{ wakeSpec?.label || t('setup.v2.params.wakeWords') }}</label>
                    <span class="field-hint">{{ t('setup.v2.params.listHint') }}</span>
                  </div>
                  <div class="field-control">
                    <div class="setup-wake-words">
                      <div v-for="(word, at) in wakeWords" :key="at" class="setup-wake-row">
                        <input
                          v-model="wakeWords[at]"
                          maxlength="12"
                          :placeholder="t('setup.v2.wake.wordPlaceholder')"
                          :aria-label="`${t('setup.v2.params.wakeWords')} ${at + 1}`"
                        />
                        <button
                          v-if="wakeWords.length > 1"
                          type="button"
                          class="btn btn-sm"
                          @click="removeWakeWord(at)"
                        >
                          {{ t('setup.v2.wake.remove') }}
                        </button>
                      </div>
                      <button
                        v-if="wakeWords.length < 3"
                        type="button"
                        class="btn btn-sm"
                        @click="addWakeWord"
                      >
                        {{ t('setup.v2.wake.add') }}
                      </button>
                    </div>
                    <p v-if="fieldErrors.wake_words" class="field-hint fail">{{ fieldErrors.wake_words }}</p>
                  </div>
                </div>

                <div
                  v-for="spec in extraParams"
                  :key="spec.key"
                  class="field-row"
                  :class="{ invalid: fieldErrors[spec.key] }"
                >
                  <div class="field-meta">
                    <label class="field-label" :for="`setup-param-${spec.key}`">{{ spec.label }}</label>
                    <span v-if="spec.required" class="badge badge-muted">{{ t('setup.v2.required') }}</span>
                    <span v-if="spec.type === 'secret' && secretConfigured(spec.key)" class="badge badge-ok">
                      {{ t('setup.v2.params.secretConfigured') }}
                    </span>
                    <p v-if="spec.help" class="field-hint">{{ spec.help }}</p>
                  </div>
                  <div class="field-control">
                    <select
                      v-if="spec.type === 'choice'"
                      :id="`setup-param-${spec.key}`"
                      v-model="paramTexts[spec.key]"
                    >
                      <option value="">{{ t('setup.v2.params.invalidChoice') }}</option>
                      <option v-for="option in spec.options" :key="option" :value="option">{{ option }}</option>
                    </select>
                    <input
                      v-else-if="spec.type === 'bool'"
                      :id="`setup-param-${spec.key}`"
                      v-model="paramBools[spec.key]"
                      type="checkbox"
                      class="setup-checkbox"
                    />
                    <input
                      v-else-if="spec.type === 'secret'"
                      :id="`setup-param-${spec.key}`"
                      v-model="secretDrafts[spec.key]"
                      type="password"
                      autocomplete="off"
                      spellcheck="false"
                      :placeholder="secretConfigured(spec.key) ? t('setup.v2.params.secretNew') : ''"
                    />
                    <input
                      v-else-if="spec.type === 'int'"
                      :id="`setup-param-${spec.key}`"
                      v-model="paramTexts[spec.key]"
                      inputmode="numeric"
                      :placeholder="spec.placeholder"
                    />
                    <input
                      v-else-if="spec.type === 'list'"
                      :id="`setup-param-${spec.key}`"
                      v-model="paramTexts[spec.key]"
                      :placeholder="spec.placeholder || t('setup.v2.params.listHint')"
                    />
                    <input
                      v-else-if="spec.type === 'string'"
                      :id="`setup-param-${spec.key}`"
                      v-model="paramTexts[spec.key]"
                      :placeholder="spec.placeholder"
                    />
                    <input
                      v-else
                      :id="`setup-param-${spec.key}`"
                      :value="paramTexts[spec.key]"
                      disabled
                    />
                    <p v-if="spec.max !== null && spec.max !== undefined && spec.type === 'list'" class="field-hint">
                      {{ t('setup.v2.params.max', { n: spec.max }) }}
                    </p>
                    <p v-if="fieldErrors[spec.key]" class="field-hint fail">{{ fieldErrors[spec.key] }}</p>
                  </div>
                </div>
              </div>

              <div class="actions">
                <button
                  type="button"
                  class="btn btn-primary"
                  :disabled="busyStep !== '' || busyGlobal !== '' || Boolean(unsupportedParam)"
                  @click="saveParams"
                >
                  {{ busyStep === 'params' ? t('common.saving') : t('setup.v2.params.save') }}
                </button>
              </div>
              <p class="field-hint">{{ t('setup.v2.params.note') }}</p>
            </template>
          </template>

          <!-- wake: 1–3 words, rewritten to keywords.txt -->
          <template v-else-if="step.id === 'wake'">
            <p v-if="!deviceId" class="field-hint">{{ t('setup.v2.wake.needDevice') }}</p>
            <div class="setup-wake-words">
              <div v-for="(word, at) in wakeWords" :key="at" class="setup-wake-row">
                <input
                  v-model="wakeWords[at]"
                  maxlength="12"
                  :placeholder="t('setup.v2.wake.wordPlaceholder')"
                  :aria-label="`${t('setup.v2.params.wakeWords')} ${at + 1}`"
                />
                <button
                  v-if="wakeWords.length > 1"
                  type="button"
                  class="btn btn-sm"
                  @click="removeWakeWord(at)"
                >
                  {{ t('setup.v2.wake.remove') }}
                </button>
              </div>
              <button
                v-if="wakeWords.length < 3"
                type="button"
                class="btn btn-sm"
                @click="addWakeWord"
              >
                {{ t('setup.v2.wake.add') }}
              </button>
            </div>
            <p v-if="wakeErrorText" class="field-hint fail">{{ wakeErrorText }}</p>
            <p class="field-hint">{{ t('setup.v2.wake.hint') }}</p>
            <div class="actions">
              <button
                type="button"
                class="btn btn-primary"
                :disabled="busyStep !== '' || busyGlobal !== '' || !deviceId || Boolean(wakeErrorText)"
                @click="saveWake"
              >
                {{ busyStep === 'wake' ? t('common.saving') : t('setup.v2.wake.save') }}
              </button>
            </div>
          </template>

          <!-- connect: ws URL + token from the one endpoint that returns it -->
          <template v-else-if="step.id === 'connect'">
            <p v-if="connectSkipped" class="field-hint">{{ t('setup.v2.connect.outbound') }}</p>
            <p v-else-if="!deviceId" class="field-hint">{{ t('setup.v2.connect.noDevice') }}</p>
            <template v-else>
              <div class="setup-connect-meta">
                <span class="field-hint">{{ t('setup.v2.connect.listenPort') }}: <code class="mono">{{ listenPort }}</code></span>
                <span class="field-hint">{{ t('setup.v2.connect.tokenEnv') }}: <code class="mono">{{ tokenEnv }}</code></span>
              </div>
              <p v-if="connectBusy" class="field-hint">{{ t('setup.connect.loading') }}</p>
              <div v-else-if="connectError" class="note fail" role="alert">
                {{ t('setup.connect.loadError', { message: connectError }) }}
                <button type="button" class="btn btn-sm" @click="loadConnect(deviceId, true)">
                  {{ t('setup.connect.retry') }}
                </button>
              </div>
              <template v-else-if="connectInfo">
                <div class="connect-fields">
                  <div class="connect-field">
                    <label class="field-label" for="connect-ws">{{ t('setup.connect.wsUrl') }}</label>
                    <div class="copy-row">
                      <input
                        id="connect-ws"
                        :value="wsDisplay"
                        readonly
                        spellcheck="false"
                        class="mono"
                        @focus="selectInput"
                      />
                      <button
                        type="button"
                        class="btn btn-sm"
                        :disabled="wsCopyDisabled"
                        @click="copyText(connectInfo.ws_url, 'ws')"
                      >
                        {{ copied === 'ws' ? t('setup.connect.copied') : t('setup.connect.copy') }}
                      </button>
                    </div>
                  </div>
                  <div class="connect-field">
                    <div class="connect-label-row">
                      <label class="field-label" for="connect-token">{{ t('setup.connect.token') }}</label>
                      <span class="field-hint mono">{{ connectInfo.token_env }}</span>
                    </div>
                    <div class="copy-row">
                      <input
                        id="connect-token"
                        :value="tokenVisible ? connectInfo.token : maskedToken"
                        readonly
                        spellcheck="false"
                        autocomplete="off"
                        class="mono"
                        :aria-label="t('setup.connect.token')"
                      />
                      <button
                        type="button"
                        class="btn btn-sm"
                        :disabled="!connectInfo.token"
                        @click="tokenVisible = !tokenVisible"
                      >
                        {{ tokenVisible ? t('setup.connect.hide') : t('setup.connect.show') }}
                      </button>
                      <button
                        type="button"
                        class="btn btn-sm"
                        :disabled="!connectInfo.token"
                        @click="copyText(connectInfo.token, 'token')"
                      >
                        {{ copied === 'token' ? t('setup.connect.copied') : t('setup.connect.copy') }}
                      </button>
                    </div>
                    <p class="field-hint">{{ t('setup.connect.tokenHint') }}</p>
                  </div>
                </div>

                <div v-if="!connectInfo.configured" class="connect-warning">
                  <strong>{{ t('setup.connect.missingTitle') }}</strong>
                  <p>{{ t('setup.connect.missingBody', { env: connectInfo.token_env || tokenEnv }) }}</p>
                  <pre class="connect-command mono">{{ t('setup.connect.rotate') }}</pre>
                </div>
                <div v-else-if="connectInfo.allow_no_token" class="connect-warning">
                  <strong>{{ t('setup.connect.allowNoToken') }}</strong>
                  <p>{{ t('setup.connect.allowNoTokenHint') }}</p>
                </div>

                <div class="actions">
                  <button
                    type="button"
                    class="btn"
                    :disabled="busyStep !== '' || busyGlobal !== ''"
                    @click="inspectConnect"
                  >
                    {{ busyStep === 'connect' ? t('setup.v2.connect.inspecting') : t('setup.v2.connect.inspect') }}
                  </button>
                  <button type="button" class="btn btn-sm" @click="toggleWait">
                    {{ waiting ? t('setup.v2.connect.stopWait') : t('setup.v2.connect.waiting') }}
                  </button>
                  <button
                    type="button"
                    class="btn btn-sm"
                    :disabled="busyGlobal !== '' || busyStep !== ''"
                    @click="refreshState({ force: true })"
                  >
                    {{ t('setup.v2.recheck') }}
                  </button>
                </div>
                <p class="field-hint">
                  {{ waiting ? t('setup.v2.connect.waitActive', { n: waitTicks }) : t('setup.v2.connect.waitHint') }}
                </p>
              </template>
            </template>
          </template>

          <!-- test: allowlisted device.test, checks rendered below -->
          <template v-else-if="step.id === 'test'">
            <p v-if="!deviceId" class="field-hint">{{ t('setup.v2.test.needDevice') }}</p>
            <p v-else class="field-hint">{{ t('setup.v2.test.note') }}</p>
            <div class="actions">
              <button
                type="button"
                class="btn btn-primary"
                :disabled="busyStep !== '' || busyGlobal !== '' || !deviceId"
                @click="runTest"
              >
                {{ busyStep === 'test' ? t('setup.v2.test.running') : t('setup.v2.test.run') }}
              </button>
              <button
                type="button"
                class="btn btn-sm"
                :disabled="busyStep !== '' || busyGlobal !== '' || !deviceId"
                @click="inspectConnect"
              >
                {{ t('setup.v2.connect.inspect') }}
              </button>
            </div>
            <div v-if="testChecks.length" class="setup-checks">
              <div class="field-label">{{ t('setup.v2.test.checks') }}</div>
              <div v-for="(check, at) in testChecks" :key="check.id || at" class="setup-check-row">
                <span class="badge" :class="check.ok ? 'badge-ok' : 'badge-fail'">
                  {{ check.ok ? t('setup.v2.test.ok') : t('setup.v2.test.fail') }}
                </span>
                <strong>{{ checkLabel(check) }}</strong>
                <span class="field-hint mono">{{ checkDetail(check) }}</span>
              </div>
            </div>
            <p v-else class="field-hint">{{ t('setup.v2.test.noChecks') }}</p>
            <p v-if="evidence(step).at" class="field-hint">
              {{ t('setup.v2.test.at', { at: text(evidence(step).at) }) }}
            </p>
          </template>

          <!-- verify: try.run + reply, or manual confirmation / skip -->
          <template v-else-if="step.id === 'verify'">
            <div v-if="verifySkipped" class="setup-skip">
              <span class="badge badge-muted">{{ t('setup.v2.verify.skipped') }}</span>
              <p>{{ t('setup.v2.verify.unverified') }}</p>
              <button type="button" class="btn btn-sm" @click="unskipVerify">
                {{ t('setup.v2.verify.say') }}
              </button>
            </div>
            <p v-else-if="!deviceId" class="field-hint">{{ t('setup.v2.verify.needDevice') }}</p>
            <template v-else>
              <div class="say-bar">
                <input
                  v-model="verifyText"
                  :placeholder="t('setup.try.placeholder')"
                  :disabled="busyStep !== ''"
                  @keyup.enter="runVerify"
                />
                <button
                  type="button"
                  class="btn btn-primary"
                  :disabled="busyStep !== '' || busyGlobal !== '' || !verifyText.trim()"
                  @click="runVerify"
                >
                  {{ busyStep === 'verify' ? t('block.sending') : t('setup.v2.verify.say') }}
                </button>
              </div>
              <blockquote v-if="verifyReply" class="say-reply">
                <span class="field-label">{{ t('setup.v2.verify.reply') }}</span>
                {{ verifyReply }}
              </blockquote>
              <p v-if="verifyAudio" class="ok">
                {{ t('setup.v2.verify.audio', { n: verifyAudio }) }}
              </p>
              <p v-if="verifyError" class="note fail" role="alert">{{ verifyError }}</p>
              <p class="field-hint">{{ t('setup.v2.verify.note') }}</p>
            </template>
            <div class="actions">
              <button
                type="button"
                class="btn btn-sm"
                :disabled="busyStep !== '' || busyGlobal !== '' || !deviceId"
                @click="manualVerify"
              >
                {{ t('setup.v2.verify.manual') }}
              </button>
              <button
                v-if="!state.verified"
                type="button"
                class="btn btn-sm"
                :disabled="busyStep !== '' || busyGlobal !== ''"
                @click="skipVerify"
              >
                {{ t('setup.v2.verify.skip') }}
              </button>
            </div>
            <p v-if="state.verified" class="ok">{{ t('setup.v2.verified') }}</p>
          </template>

          <!-- unknown step id: the server drives the list; show its evidence as-is -->
          <template v-else>
            <p class="field-hint">{{ rawEvidence(step) }}</p>
          </template>

          <details class="setup-raw">
            <summary>{{ t('setup.v2.rawEvidence') }}</summary>
            <pre class="cmd mono">{{ rawEvidence(step) }}</pre>
          </details>

          <div class="setup-step-foot">
            <button
              type="button"
              class="btn btn-sm"
              :disabled="busyGlobal !== '' || busyStep !== ''"
              @click="refreshState({ force: true })"
            >
              {{ t('setup.v2.recheck') }}
            </button>
            <button type="button" class="btn btn-sm" @click="copyDiagnostics(step.id)">
              {{ copied === `diag:${step.id}` ? t('setup.v2.diagCopied') : t('setup.v2.copyDiag') }}
            </button>
          </div>
        </div>
      </div>
    </section>

    <div v-if="state.complete" class="banner banner-ok setup-done">
      <AppIcon name="check" :size="18" class="banner-icon" />
      <div>
        <strong>{{ state.verified ? t('setup.done.title') : t('setup.v2.completeUnverified') }}</strong>
        <p>
          {{ t('setup.done.text', { wake: currentWakeWord }) }}
          <RouterLink to="/panel/overview" class="setup-done-link">{{ t('setup.done.open') }}</RouterLink>
        </p>
        <p v-if="!state.verified" class="field-hint">{{ t('setup.v2.verify.unverified') }}</p>
      </div>
    </div>

  </template>
</template>

<style scoped>
.setup-head-side {
  flex-wrap: wrap;
}

.setup-plugbar {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  padding: var(--s3) 0;
  border-bottom: 1px solid var(--line);
}

.setup-plugbar select {
  min-width: 240px;
  max-width: 100%;
}

.setup-plugbar .field-hint {
  margin: 0;
}

.setup-plugbar-actions {
  display: flex;
  gap: var(--s2);
  margin-left: auto;
  flex-wrap: wrap;
}

.setup-plugmeta {
  padding: var(--s3) 0;
  border-bottom: 1px solid var(--line);
}

.setup-plugmeta-primary {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.setup-caps {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  margin-top: var(--s2);
}

.setup-failed-plugs {
  display: flex;
  gap: var(--s2);
  flex-wrap: wrap;
  margin-top: var(--s2);
}

.setup-loading {
  padding: var(--s5) 0;
}

.setup-section {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  gap: var(--s3);
  padding: var(--s6) 0;
  border-top: 1px solid var(--line);
}

.setup-section-collapsed {
  padding: var(--s4) 0;
}

.setup-index {
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.06em;
  color: var(--accent);
  padding-top: 6px;
}

.setup-body {
  min-width: 0;
}

.setup-body h2 {
  font-size: 21px;
}

.setup-step-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s2);
  flex-wrap: wrap;
}

.setup-step-title {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.setup-evidence-line {
  margin: var(--s2) 0 0;
  padding: 6px 8px;
  background: var(--surface-2);
  border-radius: var(--r-xs);
  color: var(--muted);
  font-size: 11.5px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}

.setup-evidence-label {
  color: var(--ink-2);
  font-weight: 600;
  margin-right: 6px;
}

.setup-step-content {
  margin-top: var(--s3);
}

.setup-role-pair {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--s3);
  padding: var(--s2) var(--s3);
  border-left: 2px solid var(--accent);
  background: var(--accent-soft);
  border-radius: 0 var(--r-sm) var(--r-sm) 0;
  margin-bottom: var(--s3);
}

.setup-role-pair strong {
  font-size: 11.5px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--accent);
}

.setup-role-pair p {
  margin-top: 3px;
  font-size: 12px;
  line-height: 1.55;
  color: var(--ink-2);
}

.setup-fields {
  margin-top: var(--s3);
}

.setup-key-row {
  align-items: start;
}

.setup-key-tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 3px;
}

.setup-key-tags .field-hint {
  margin: 0;
  font-size: 11.5px;
}

.setup-key-hint {
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.setup-evidence-grid {
  display: flex;
  gap: var(--s4);
  flex-wrap: wrap;
  margin-top: var(--s2);
  font-size: 12.5px;
}

.setup-risk {
  margin-top: var(--s2);
  padding: 10px 12px;
  border-left: 2px solid var(--fail);
  border-radius: 0 var(--r-sm) var(--r-sm) 0;
  background: var(--fail-soft);
  color: var(--ink-2);
  font-size: 12px;
  line-height: 1.55;
}

.setup-risk p {
  margin-top: 3px;
}

.setup-manifest {
  margin-top: var(--s2);
  padding: var(--s3);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-2);
}

.setup-manifest-row {
  display: flex;
  align-items: baseline;
  gap: var(--s2);
  flex-wrap: wrap;
  padding: 3px 0;
  font-size: 12px;
}

.setup-manifest-block {
  align-items: flex-start;
  flex-direction: column;
  gap: 3px;
  margin-top: var(--s2);
}

.setup-manifest-block ol,
.setup-manifest-block p {
  margin: 0;
  padding-left: 18px;
  color: var(--ink-2);
  line-height: 1.6;
}

.setup-manifest-block p {
  padding-left: 0;
}

.setup-manifest a {
  color: var(--accent);
}

.setup-check {
  display: flex;
  align-items: flex-start;
  gap: var(--s2);
  font-size: 12.5px;
  line-height: 1.55;
  margin: var(--s2) 0;
}

.setup-check input {
  margin-top: 3px;
}

.setup-checkbox {
  width: auto;
}

.setup-wake-words {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
}

.setup-wake-row {
  display: flex;
  gap: var(--s2);
  align-items: center;
}

.setup-wake-row input {
  flex: 1;
  min-width: 0;
}

.setup-connect-meta {
  display: flex;
  gap: var(--s3);
  flex-wrap: wrap;
  margin-bottom: var(--s2);
}

.setup-checks {
  margin-top: var(--s3);
  padding: var(--s3);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
}

.setup-check-row {
  display: flex;
  align-items: baseline;
  gap: var(--s2);
  flex-wrap: wrap;
  padding: 4px 0;
  border-top: 1px solid var(--line);
  font-size: 12px;
}

.setup-check-row:first-of-type {
  border-top: 0;
}

.setup-skip {
  padding: var(--s3);
  background: var(--surface-2);
  border-radius: var(--r-sm);
  font-size: 12px;
  line-height: 1.55;
}

.setup-skip p {
  margin: var(--s2) 0;
  color: var(--ink-2);
}

.setup-error {
  margin: var(--s3) 0;
  padding: 10px 12px;
  border-left: 2px solid var(--fail);
  border-radius: 0 var(--r-sm) var(--r-sm) 0;
  background: var(--fail-soft);
  font-size: 12px;
  line-height: 1.55;
}

.setup-error-head {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.setup-raw {
  margin-top: var(--s3);
  font-size: 11.5px;
}

.setup-raw summary {
  cursor: pointer;
  color: var(--muted);
}

.setup-raw pre {
  margin-top: var(--s2);
  max-height: 320px;
  overflow: auto;
}

.setup-step-foot {
  display: flex;
  gap: var(--s2);
  flex-wrap: wrap;
  margin-top: var(--s4);
  padding-top: var(--s2);
  border-top: 1px solid var(--line);
}

.say-bar {
  display: flex;
  gap: var(--s2);
  margin-top: var(--s3);
}

.say-bar input {
  flex: 1;
  min-width: 0;
}

.say-reply {
  margin: var(--s3) 0 0;
  padding: 10px 12px;
  background: var(--surface-2);
  border-left: 2px solid var(--accent);
  border-radius: 0 var(--r-sm) var(--r-sm) 0;
  font-size: 13.5px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.say-reply .field-label {
  display: block;
  margin-bottom: 2px;
}

.setup-done {
  margin-top: var(--s5);
}

.setup-done-link {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 2px;
  margin-left: 4px;
}

.connect-fields {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
  margin-top: var(--s3);
}

.connect-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.connect-label-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--s2);
  flex-wrap: wrap;
}

.copy-row {
  display: flex;
  align-items: stretch;
  gap: var(--s2);
  min-width: 0;
}

.copy-row input {
  flex: 1;
  min-width: 0;
  min-height: 34px;
  padding: 5px 8px;
  font-size: 11.5px;
  color: var(--ink-2);
  background: var(--surface);
  overflow-wrap: anywhere;
}

.copy-row .btn {
  flex: none;
}

.connect-warning {
  margin-top: var(--s3);
  padding: 10px 12px;
  border-left: 2px solid var(--warn);
  border-radius: 0 var(--r-sm) var(--r-sm) 0;
  background: var(--warn-soft);
  color: var(--ink-2);
  font-size: 12px;
  line-height: 1.55;
}

.connect-warning p {
  margin-top: 4px;
}

.connect-command {
  margin: 6px 0 0;
  padding: 6px 8px;
  border: 1px solid var(--line);
  border-radius: var(--r-xs);
  background: var(--surface);
  font-size: 11px;
  overflow-wrap: anywhere;
}

@media (max-width: 720px) {
  .setup-section {
    grid-template-columns: 28px minmax(0, 1fr);
    padding: var(--s5) 0;
  }

  .setup-role-pair {
    grid-template-columns: minmax(0, 1fr);
  }

  .setup-plugbar select {
    min-width: 0;
    width: 100%;
  }

  .setup-plugbar-actions {
    margin-left: 0;
    width: 100%;
  }

  .say-bar {
    flex-direction: column;
  }

  .setup-wake-row {
    flex-wrap: wrap;
  }

  .copy-row {
    flex-wrap: wrap;
  }

  .copy-row input {
    flex: 1 1 100%;
    min-height: 44px;
  }

  .copy-row .btn {
    flex: 1 1 calc(50% - var(--s2));
    min-height: 44px;
  }
}
</style>
