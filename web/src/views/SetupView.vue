<script setup lang="ts">
/**
 * The install wizard (P2.2): a plug picker in the header and eight cards whose
 * order, status and allowlisted actions all come from `GET /api/v1/setup/step`.
 *
 * The view never invents completion: every badge is the server's `status`, every
 * piece of evidence is the server's `evidence`, and the only actions offered are
 * the ones the server put in `step.actions` (keys.test is shown optimistically so
 * a backend without it fails inline instead of hiding the button). Secrets are
 * write-only: key/secret inputs start empty, are never bound to a response value,
 * and never leave this component except in the one POST that stores them.
 * Diagnostics are assembled explicitly (no token, host, key or full config path)
 * and only copied.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import {
  api,
  ApiError,
  apiErrorObject,
  type Config,
  type DeviceConnectInfo,
  type DeviceDoc,
  type Provider,
  type SetupDeviceManifest,
  type SetupParamSpec,
  type SetupPlugDetail,
  type SetupPlugSummary,
  type SetupProviderEntry,
  type SetupProviderField,
  type SetupProviderVoice,
  type SetupProvidersDoc,
  type SetupStepResponse,
  type SetupStepState,
  type SetupStepStatus,
  type ValidationIssue,
} from '../api'
import {
  localizeBackend,
  localizeIssue,
  pickLocalized,
  t,
  type MsgKey,
} from '../i18n'

type Stage = 'llm' | 'asr' | 'tts'
type KeyFieldRole = 'api_key' | 'app_id' | 'api_secret' | string
type KeyTestStatus = 'ok' | 'fail' | 'error' | 'neutral'

/** One credential input inside a provider row; `env` is shown as secondary text. */
interface KeyFieldView {
  name: string
  role: KeyFieldRole
  label: string
  hint: string
  env: string
  configured: boolean
  docsUrl: string
}

interface VoiceView {
  value: string
  label: string
  isDefault: boolean
}

/** One selectable provider for a stage, with only the fields that provider needs. */
interface ProviderView {
  id: string
  name: string
  stage: Stage
  displayName: string
  hint: string
  docsUrl: string
  configured: boolean
  missing: string[]
  fields: KeyFieldView[]
  voices: VoiceView[]
  defaultVoice: string
  selected: boolean
  /** True when the provider declares no credential slots (local ASR / keyless). */
  keyless: boolean
}

/** One fixed table row: use | provider | credentials | voice (TTS only). */
interface ProviderRowView {
  stage: Stage
  providers: ProviderView[]
  selectedName: string
  selectedProvider: ProviderView | null
  fields: KeyFieldView[]
  voices: VoiceView[]
  voice: string
  configured: boolean
}

interface ProviderBaseline {
  provider: string
  voice: string
}

interface KeyTestResult {
  status: KeyTestStatus
  message: string
  details?: string
}

interface PrepareStep {
  id: string
  text: string
  fromManifest: boolean
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

/** Human names for the three uses, independent of the backend's LLM/ASR/TTS ids. */
const STAGE_USE_LABELS: Record<Stage, MsgKey> = {
  llm: 'setup.v2.keys.use.llm',
  asr: 'setup.v2.keys.use.asr',
  tts: 'setup.v2.keys.use.tts',
}

const KEY_ROLE_LABELS: Record<string, MsgKey> = {
  api_key: 'setup.v2.keys.field.apiKey',
  app_id: 'setup.v2.keys.field.appId',
  api_secret: 'setup.v2.keys.field.apiSecret',
}

/** Generic device-side checklist used only when the plug manifest has no steps. */
const PREPARE_FALLBACK_KEYS: MsgKey[] = [
  'setup.v2.prepare.fallback.ip',
  'setup.v2.prepare.fallback.ssh',
  'setup.v2.prepare.fallback.clientPath',
  'setup.v2.prepare.fallback.configPath',
  'setup.v2.prepare.fallback.restart',
  'setup.v2.prepare.fallback.log',
]

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
  keys_test_failed: 'setup.error.keys_test_failed',
  keys_test_unavailable: 'setup.error.keys_test_unavailable',
}

const CHECK_LABELS: Record<string, MsgKey> = {
  plane: 'setup.v2.test.check.plane',
  host: 'setup.v2.test.check.host',
  token: 'setup.v2.test.check.token',
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
/** Owned by the fixed controls above; a manifest copy must not render twice. */
const RESERVED_PARAMS = new Set([
  'host',
  'room',
  'wake_words',
  'volume',
  'led_enabled',
  'listen_port',
  'mic_gain_db',
])

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

const providerDoc = ref<SetupProvidersDoc | null>(null)
const providerFallback = ref(false)
const providerError = ref('')
const providerLoading = ref(false)
const providerSelections = ref<Record<Stage, string>>({ llm: '', asr: '', tts: '' })
const voiceDrafts = ref<Record<Stage, string>>({ llm: '', asr: '', tts: '' })
const providerBaseline = ref<Record<Stage, ProviderBaseline>>({
  llm: { provider: '', voice: '' },
  asr: { provider: '', voice: '' },
  tts: { provider: '', voice: '' },
})
const keySaveError = ref('')
const keyDrafts = ref<Record<string, string>>({})
const keyTestBusy = ref('')
const keyTestResults = ref<Record<string, KeyTestResult>>({})

const deviceDraft = ref<DeviceDoc | null>(null)
const deviceDraftKey = ref('')
const hostDraft = ref('')
const roomDraft = ref('')
const volumeDraft = ref('60')
const ledEnabled = ref(true)
const listenPortDraft = ref('')
const micGainDraft = ref('')
const wakeWords = ref<string[]>([''])
const paramTexts = ref<Record<string, string>>({})
const paramBools = ref<Record<string, boolean>>({})
const secretDrafts = ref<Record<string, string>>({})
const fieldErrors = ref<Record<string, string>>({})

const prepareChecks = ref<Record<string, boolean>>({})
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
  const reported = evidence(connectStep.value).listen_port
  if (typeof reported === 'number' && reported > 0) return reported
  if (typeof reported === 'string' && reported.trim()) return reported
  const runtime = selectedDeviceManifest.value?.runtime
  if (runtime?.listen_port) return runtime.listen_port
  const resolved = plugDetail.value?.resolved?.devices?.find(
    (device) => device.id === selectedDeviceManifest.value?.id,
  )
  return resolved?.listen_port ?? 4412
})
const tokenEnv = computed(() => {
  const reported = evidence(connectStep.value).token_env
  if (typeof reported === 'string' && reported.trim()) return reported
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

/** Chain order wins; the backend returns `stages[stage]` in priority order. */
function stageEntries(doc: SetupProvidersDoc | null, stage: Stage): SetupProviderEntry[] {
  if (!doc) return []
  const entries = (doc.providers ?? []).filter((entry) => entry && entry.stage === stage)
  const chain = doc.stages?.[stage]?.length
    ? doc.stages[stage]
    : entries.find((entry) => Array.isArray(entry.chain) && entry.chain.length > 0)?.chain ?? []
  if (!chain.length) return entries
  const rank = new Map<string, number>()
  chain.forEach((id, index) => {
    rank.set(id, index)
    const bare = id.includes(':') ? id.slice(id.indexOf(':') + 1) : id
    if (bare) rank.set(bare, index)
  })
  return [...entries].sort((left, right) => {
    const leftRank = rank.get(left.id ?? '') ?? rank.get(left.name) ?? 99
    const rightRank = rank.get(right.id ?? '') ?? rank.get(right.name) ?? 99
    return leftRank - rightRank
  })
}

/** The active chain entry: explicit `selected`, then a stage-level map, then chain[0]. */
function entrySelected(entry: SetupProviderEntry, doc: SetupProvidersDoc | null, index: number): boolean {
  if (typeof entry.selected === 'boolean') return entry.selected
  if (typeof entry.selected === 'string' && entry.selected.trim()) {
    return entry.selected === entry.name || entry.selected === (entry.id ?? '')
  }
  const stageSelected = doc?.stage_state?.[entry.stage]?.selected
  if (typeof stageSelected === 'string' && stageSelected.trim()) {
    return stageSelected === entry.name || stageSelected === (entry.id ?? '')
  }
  const selected = doc?.selected
  if (typeof selected === 'string' && selected.trim()) {
    return selected === entry.name || selected === (entry.id ?? '')
  }
  if (selected && typeof selected === 'object') {
    const value = (selected as Record<string, unknown>)[entry.stage]
    if (typeof value === 'string' && value.trim()) {
      return value === entry.name || value === (entry.id ?? '')
    }
  }
  return index === 0
}

function keyRoleLabel(role: KeyFieldRole): string {
  const key = KEY_ROLE_LABELS[role]
  return key ? t(key) : role
}

function toFieldView(field: SetupProviderField): KeyFieldView {
  const name = String(field.name ?? '').trim()
  return {
    name,
    role: String(field.role ?? ''),
    label: pickLocalized(field.label_i18n) || String(field.label ?? '') || keyRoleLabel(String(field.role ?? '')),
    hint: pickLocalized(field.hint_i18n) || String(field.hint ?? ''),
    env: name,
    configured: field.configured === true,
    docsUrl: String(field.docs_url ?? ''),
  }
}

function toVoiceViews(entry: SetupProviderEntry): VoiceView[] {
  const raw = Array.isArray(entry.voices) ? entry.voices : []
  const fallback = String(entry.default_voice ?? '').trim()
  const views: VoiceView[] = []
  for (const item of raw) {
    if (typeof item === 'string') {
      if (item.trim()) views.push({ value: item, label: item, isDefault: item === fallback })
      continue
    }
    if (!item || typeof item !== 'object') continue
    const voice = item as SetupProviderVoice
    const value = String(voice.value ?? voice.id ?? voice.name ?? '').trim()
    if (!value) continue
    views.push({
      value,
      label:
        pickLocalized(voice.name_i18n) ||
        pickLocalized(voice.label) ||
        String(voice.name ?? '') ||
        value,
      isDefault: voice.default === true || value === fallback,
    })
  }
  if (!views.length && fallback) views.push({ value: fallback, label: fallback, isDefault: true })
  return views
}

function toProviderView(
  entry: SetupProviderEntry,
  doc: SetupProvidersDoc | null,
  index: number,
): ProviderView {
  const voices = toVoiceViews(entry)
  const fields = (entry.fields ?? []).map(toFieldView)
  const defaultVoice =
    String(entry.default_voice ?? '').trim() ||
    voices.find((voice) => voice.isDefault)?.value ||
    voices[0]?.value ||
    ''
  return {
    id: String(entry.id ?? `${entry.stage}:${entry.name}`),
    name: entry.name,
    stage: entry.stage as Stage,
    displayName:
      pickLocalized({
        zh: entry.display_name || entry.name,
        en: entry.display_name_en || entry.display_name || entry.name,
      }) || entry.name,
    hint: pickLocalized(entry.hint_i18n) || String(entry.hint ?? ''),
    docsUrl: String(entry.docs_url ?? ''),
    configured: entry.configured === true,
    missing: Array.isArray(entry.missing) ? entry.missing.map(String) : [],
    fields,
    voices,
    defaultVoice,
    selected: entrySelected(entry, doc, index),
    keyless: fields.length === 0,
  }
}

/** The three fixed rows; a row keeps its provider even when the catalog reloads. */
const providersByStage = computed<Record<Stage, ProviderView[]>>(() => {
  const out = { llm: [], asr: [], tts: [] } as Record<Stage, ProviderView[]>
  for (const stage of STAGES) {
    out[stage] = stageEntries(providerDoc.value, stage).map((entry, index) =>
      toProviderView(entry, providerDoc.value, index),
    )
  }
  return out
})

const providerRows = computed<ProviderRowView[]>(() =>
  STAGES.map((stage) => {
    const providers = providersByStage.value[stage]
    const picked = providerSelections.value[stage]
    const selectedProvider =
      providers.find((provider) => provider.name === picked) ??
      providers.find((provider) => provider.selected) ??
      providers[0] ??
      null
    return {
      stage,
      providers,
      selectedName: selectedProvider?.name ?? '',
      selectedProvider,
      fields: selectedProvider?.fields ?? [],
      voices: selectedProvider?.voices ?? [],
      voice: voiceDrafts.value[stage] || selectedProvider?.defaultVoice || '',
      configured: selectedProvider?.configured ?? false,
    }
  }),
)

/** Drafts are namespaced by stage + provider so switching back does not lose typing. */
function keyDraftId(stage: Stage, provider: string, env: string): string {
  return `${stage}:${provider}:${env}`
}

/** A save is worthwhile when keys were typed, a provider switched, or a voice changed. */
const keyDirty = computed(() => {
  if (Object.values(keyDrafts.value).some((value) => value.trim())) return true
  return providerRows.value.some((row) => {
    const base = providerBaseline.value[row.stage]
    if (!base) return Boolean(row.selectedName)
    if (row.selectedName && row.selectedName !== base.provider) return true
    return row.stage === 'tts' && Boolean(row.voice) && row.voice !== base.voice
  })
})

function providerOptionLabel(provider: ProviderView): string {
  if (provider.keyless) return `${provider.displayName} · ${t('setup.v2.keys.noKeyNeeded')}`
  return provider.configured
    ? `${provider.displayName} · ${t('setup.keys.configured')}`
    : provider.displayName
}

/** A keyless row (local ASR) is ready, not missing: neutral means "nothing to do". */
function providerBadgeLabel(row: ProviderRowView): string {
  if (row.selectedProvider?.keyless) return t('setup.v2.keys.noKeyNeeded')
  return row.configured ? t('setup.keys.configured') : t('setup.keys.missing')
}

function providerBadgeClass(row: ProviderRowView): string {
  if (row.selectedProvider?.keyless) return 'badge-muted'
  return row.configured ? 'badge-ok' : 'badge-warn'
}

/** A changed draft invalidates the previous smoke-test result for that row. */
function clearKeyTest(stage: Stage): void {
  if (!keyTestResults.value[stage]) return
  const next = { ...keyTestResults.value }
  delete next[stage]
  keyTestResults.value = next
}

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

/** Compact pass/fail summary reused in the params card and the test card. */
const testOutcome = computed<{ status: 'ok' | 'fail'; message: string; at: string } | null>(() => {
  const step = testStep.value
  if (!step) return null
  const ev = evidence(step)
  const at = String(ev.at ?? '')
  const checks = testChecks.value
  if (!at && !checks.length && !ev.stored && !ev.speaker_connected) return null
  // A stored `ok` may only prove the SSH/ping probe: the live plane link is the test.
  const passed = Boolean(ev.speaker_connected)
  if (passed) return { status: 'ok', message: t('setup.v2.params.testPassed'), at }
  const failed = checks.filter((check) => check.ok === false).map(checkLabel)
  if (ev.stored && failed.length === 0) {
    return { status: 'fail', message: t('setup.v2.test.storedOnly'), at }
  }
  const connectionMessage = String(evidence(connectStep.value).connection_message ?? '')
  const reason = failed.length
    ? t('setup.v2.params.testFailed', { items: failed.join(' / ') })
    : connectionMessage === 'waiting_for_client' || !connectionMessage
      ? t('setup.v2.test.noLink')
      : t('setup.v2.params.testFailedGeneric')
  return { status: 'fail', message: reason, at }
})

/**
 * Manifest steps that need the ws URL / device token from the later connect step.
 * They are shown as a note, never as required checkboxes: requiring them here made
 * an honest user unable to finish prepare (`connect` is three steps later).
 *
 * Deliberately narrow: "回连地址" also appears in the fixed-IP step, which is a
 * pre-connect task. Only token handling and the post-connect retry qualify.
 */
const CONNECT_DEPENDENT_STEP =
  /(?:device[ _-]?token|\btoken\b|speaker_connected|重试|retry|401|1008)/i

function manifestPrepareSteps(): PrepareStep[] {
  return (deviceSide.value?.install_steps ?? [])
    .map((line) => String(line).trim())
    .filter(Boolean)
    .map((text, index) => ({
      id: `manifest:${index}:${text.slice(0, 24)}`,
      text,
      fromManifest: true,
    }))
}

/** Device-side checklist: only what can be done before connect. */
const prepareSteps = computed<PrepareStep[]>(() => {
  const manifestSteps = manifestPrepareSteps().filter(
    (item) => !CONNECT_DEPENDENT_STEP.test(item.text),
  )
  if (manifestSteps.length) return manifestSteps
  return PREPARE_FALLBACK_KEYS.map((key) => ({
    id: `fallback:${key}`,
    text: t(key),
    fromManifest: false,
  }))
})

/** Notes that depend on the connect step; they are not part of the required list. */
const prepareLaterNotes = computed<PrepareStep[]>(() =>
  manifestPrepareSteps().filter((item) => CONNECT_DEPENDENT_STEP.test(item.text)),
)
const prepareFromManifest = computed(() => prepareSteps.value.some((item) => item.fromManifest))
const prepareCheckedCount = computed(
  () => prepareSteps.value.filter((item) => prepareChecks.value[item.id]).length,
)
const prepareAllChecked = computed(
  () => prepareSteps.value.length > 0 && prepareCheckedCount.value === prepareSteps.value.length,
)

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

/** Hostname of the generated ws URL, without IPv6 brackets or port. */
function urlHostname(raw: string): string {
  if (!raw) return ''
  try {
    return new URL(raw).hostname.replace(/^\[|\]$/g, '').toLowerCase()
  } catch {
    const match = raw.match(/^[a-z]+:\/\/([^/:?#]+)/i)
    return (match?.[1] ?? '').replace(/^\[|\]$/g, '').toLowerCase()
  }
}

/**
 * The URL is built from the browser's Host header. Opening the console through
 * localhost/SSH makes a ws://127.0.0.1 address the speaker can never reach, so say so.
 */
const wsLoopback = computed(() =>
  ['127.0.0.1', 'localhost', '::1', '0.0.0.0'].includes(urlHostname(connectInfo.value?.ws_url ?? '')),
)

/** The server's own explanation for the connection_message code (exact reason). */
const connectHint = computed(() => pickLocalized(evidence(connectStep.value).hint))
const connectSkipped = computed(
  () => connectStep.value?.status === 'skipped' || manifestTransport.value === 'outbound',
)

type ConnectStatusTone = 'ok' | 'warn' | 'fail' | 'muted'

/** One human-readable status line for the read-only hook-up card. */
const connectStatus = computed<{
  tone: ConnectStatusTone
  badge: string
  text: string
}>(() => {
  if (connectSkipped.value) {
    return {
      tone: 'muted',
      badge: t('setup.v2.connect.badge.outbound'),
      text: t('setup.v2.connect.status.outbound'),
    }
  }
  if (!deviceId.value) {
    return {
      tone: 'muted',
      badge: t('setup.v2.connect.badge.noDevice'),
      text: t('setup.v2.connect.status.noDevice'),
    }
  }
  if (state.value?.plane_error) {
    return {
      tone: 'fail',
      badge: t('setup.v2.connect.badge.planeError'),
      text: t('setup.v2.connect.status.planeError', { error: state.value.plane_error }),
    }
  }
  const connectEvidence = evidence(connectStep.value)
  const connectionMessage = String(connectEvidence.connection_message ?? '')
  if (Boolean(connectEvidence.speaker_connected) || connectionMessage === 'connected') {
    return {
      tone: 'ok',
      badge: t('setup.v2.connect.badge.connected'),
      text: t('setup.v2.connect.status.connected'),
    }
  }
  if (
    connectionMessage === 'token_mismatch' &&
    connectEvidence.token_configured === false
  ) {
    return {
      tone: 'warn',
      badge: t('setup.v2.connect.badge.waiting'),
      text: t('setup.v2.connect.status.tokenMissing', {
        env: String(connectEvidence.token_env ?? '') || tokenEnv.value,
      }),
    }
  }
  if (connectionMessage === 'token_mismatch') {
    return {
      tone: 'fail',
      badge: t('setup.v2.connect.badge.tokenMismatch'),
      text: t('setup.v2.connect.status.tokenMismatch'),
    }
  }
  if (connectionMessage === 'port_blocked') {
    return {
      tone: 'fail',
      badge: t('setup.v2.connect.badge.portBlocked'),
      text: t('setup.v2.connect.status.portBlocked'),
    }
  }
  const info = connectInfo.value
  if (info && !info.configured && !info.allow_no_token) {
    return {
      tone: 'warn',
      badge: t('setup.v2.connect.badge.waiting'),
      text: t('setup.v2.connect.status.tokenMissing', {
        env: info.token_env || tokenEnv.value,
      }),
    }
  }
  return {
    tone: 'warn',
    badge: t('setup.v2.connect.badge.waiting'),
    text: t('setup.v2.connect.status.waiting'),
  }
})

function connectToneClass(tone: ConnectStatusTone): string {
  switch (tone) {
    case 'ok':
      return 'badge-ok'
    case 'fail':
      return 'badge-fail'
    case 'muted':
      return 'badge-muted'
    default:
      return 'badge-warn'
  }
}

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
  // A stored test result is not a live link: SSH/host probes pass while the speaker
  // is disconnected. Until the backend recomputes `test` from speaker_connected,
  // never paint it green on a stored result alone.
  if (step.id === 'test' && step.status === 'done' && !evidence(step).speaker_connected) {
    return 'pending'
  }
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
      return connectStatus.value.text
    case 'test':
      return [
        `${t('setup.v2.test.stored')}: ${flag(ev.stored)}`,
        ev.at ? `${t('setup.v2.test.at', { at: text(ev.at) })}` : '',
        ev.speaker_connected
          ? t('setup.v2.connect.badge.connected')
          : t('setup.v2.connect.badge.waiting'),
      ]
        .filter(Boolean)
        .join(' · ')
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
  if (check.id === 'token') {
    return [
      `${t('setup.keys.configured')}: ${flag(raw.configured)}`,
      raw.token_env ? `${t('setup.v2.connect.tokenEnv')}: ${String(raw.token_env)}` : '',
      raw.allow_no_token ? t('setup.connect.allowNoToken') : '',
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

/** LED state from either the new top-level field or the legacy nested `led`. */
function deviceLedEnabled(device: DeviceDoc): boolean {
  if (typeof device.led_enabled === 'boolean') return device.led_enabled
  if (device.led && typeof device.led.enabled === 'boolean') return device.led.enabled
  return false
}

function numericDraft(value: unknown): string {
  if (value === null || value === undefined || value === '') return ''
  const parsed = Number(value)
  return Number.isFinite(parsed) ? String(parsed) : ''
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
  // Device-level controls come from GET /devices/{id}; the server coerces the bounds.
  volumeDraft.value = numericDraft(device.volume) || '60'
  ledEnabled.value = deviceLedEnabled(device)
  listenPortDraft.value = numericDraft(device.listen_port)
  const storedGain =
    device.mic_gain_db ??
    (device.params && typeof device.params === 'object' ? device.params.mic_gain_db : null)
  micGainDraft.value = numericDraft(storedGain)
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
  const volumeText = volumeDraft.value.trim()
  const volume = Number(volumeText)
  if (!/^\d+$/.test(volumeText) || !Number.isInteger(volume) || volume < 0 || volume > 100) {
    setFieldError('volume', t('setup.v2.params.invalidVolume'))
  }
  const port = listenPortDraft.value.trim()
  if (port) {
    const parsed = Number(port)
    if (!/^\d+$/.test(port) || !Number.isInteger(parsed) || parsed < 1 || parsed > 65535) {
      setFieldError('listen_port', t('setup.v2.params.invalidPort'))
    }
  }
  const gain = micGainDraft.value.trim()
  if (gain) {
    const parsed = Number(gain)
    if (!/^\d+(?:\.\d+)?$/.test(gain) || !Number.isFinite(parsed) || parsed < 0 || parsed > 42) {
      setFieldError('mic_gain_db', t('setup.v2.params.invalidGain'))
    }
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
    // Device-level controls live at the top level of `device.upsert`, not in `params`:
    // the server owns their schema and bounds (volume/LED/listen port/mic gain).
    volume: Number.parseInt(volumeDraft.value.trim(), 10),
    led_enabled: Boolean(ledEnabled.value),
    listen_port: listenPortDraft.value.trim()
      ? Number.parseInt(listenPortDraft.value.trim(), 10)
      : null,
    mic_gain_db: micGainDraft.value.trim() ? Number(micGainDraft.value.trim()) : 0,
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
  volumeDraft.value = '60'
  ledEnabled.value = true
  listenPortDraft.value = ''
  micGainDraft.value = ''
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

function fallbackProviderName(name: string, provider: Provider): string {
  const kind = String(provider.kind ?? '').toLowerCase()
  if (kind === 'volc_asr' || kind === 'volc_tts') return t('setup.v2.keys.product.volc')
  if (kind === 'xfyun') return t('setup.v2.keys.product.xfyun')
  if (kind === 'sherpa') return t('setup.v2.keys.product.local')
  const host = String(provider.base_url ?? '').toLowerCase()
  if (host.includes('deepseek') || name.toLowerCase().includes('deepseek')) return 'DeepSeek'
  if (host.includes('siliconflow') || name.toLowerCase().includes('silicon')) {
    return t('setup.v2.keys.product.siliconflow')
  }
  return name
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

/**
 * Minimal provider rows from the legacy `GET /api/v1/config` document used only
 * when `/setup/providers` is unavailable. Presence comes from `has_key`; AppID
 * and API Secret cannot be checked from the legacy shape.
 */
function deriveProviderDoc(cfg: Config): SetupProvidersDoc {
  const providers: SetupProviderEntry[] = []
  const stages: Record<string, string[]> = {}
  for (const stage of STAGES) {
    const chain = cfg.providers?.[stage]?.chain ?? []
    stages[stage] = []
    chain.forEach((name, index) => {
      const provider = cfg.providers?.catalog?.[name]
      if (!provider) return
      const display = fallbackProviderName(name, provider)
      const fields: SetupProviderField[] = []
      const add = (env: string | undefined, role: string, label: string, configured: boolean) => {
        const value = String(env ?? '').trim()
        if (!value) return
        const hint =
          role === 'api_key'
            ? t('setup.v2.keys.hint.apiKey', {
                provider: display,
                use: t(STAGE_USE_LABELS[stage]),
              })
            : role === 'app_id'
              ? t('setup.v2.keys.hint.appId', { provider: display })
              : t('setup.v2.keys.hint.apiSecret', { provider: display })
        fields.push({ name: value, role, label, hint, configured })
      }
      add(provider.api_key_env, 'api_key', t('setup.v2.keys.field.apiKey'), provider.has_key === true)
      add(provider.app_id_env, 'app_id', t('setup.v2.keys.field.appId'), false)
      add(provider.api_secret_env, 'api_secret', t('setup.v2.keys.field.apiSecret'), false)
      const id = `${stage}:${name}`
      providers.push({
        id,
        stage,
        name,
        kind: String(provider.kind ?? ''),
        display_name: display,
        display_name_en: display,
        hint: t('setup.v2.keys.fallbackHint'),
        configured: fields.every((field) => field.configured),
        missing: fields.filter((field) => !field.configured).map((field) => field.name),
        selected: index === 0,
        chain: [...chain],
        fields,
        voices: provider.voice ? [provider.voice] : [],
        default_voice: String(provider.voice ?? ''),
      })
      stages[stage].push(id)
    })
  }
  return { providers, stages }
}

/** Push a catalog into state without discarding a still-valid user selection. */
function applyProviderDoc(doc: SetupProvidersDoc, fallback: boolean): void {
  providerDoc.value = doc
  providerFallback.value = fallback
  const selections = { ...providerSelections.value }
  const voices = { ...voiceDrafts.value }
  for (const stage of STAGES) {
    const views = stageEntries(doc, stage).map((entry, index) =>
      toProviderView(entry, doc, index),
    )
    const picked =
      views.find((view) => view.name === selections[stage]) ??
      views.find((view) => view.selected) ??
      views[0] ??
      null
    selections[stage] = picked?.name ?? ''
    const previousVoice = voices[stage]
    const stillOffered = picked?.voices.some((voice) => voice.value === previousVoice) ?? false
    voices[stage] = stillOffered
      ? previousVoice
      : picked?.defaultVoice || picked?.voices[0]?.value || ''
  }
  providerSelections.value = selections
  voiceDrafts.value = voices
  const baseline = {} as Record<Stage, ProviderBaseline>
  for (const stage of STAGES) {
    baseline[stage] = { provider: selections[stage], voice: voices[stage] }
  }
  providerBaseline.value = baseline
}

/** `GET /setup/providers`, with a legacy-config fallback and an inline hint. */
async function loadProviderCatalog(): Promise<void> {
  providerLoading.value = true
  providerError.value = ''
  try {
    applyProviderDoc(await api.setupProviders(), false)
  } catch (exc) {
    providerError.value = describeError(exc).message
    let cfg: Config | null = null
    try {
      cfg = await api.config()
    } catch {
      cfg = null
    }
    if (cfg) {
      applyProviderDoc(deriveProviderDoc(cfg), true)
    } else {
      providerDoc.value = null
      providerFallback.value = false
    }
  } finally {
    providerLoading.value = false
  }
}

async function loadInitial(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const [list] = await Promise.all([api.plugs(), loadProviderCatalog()])
    plugs.value = Array.isArray(list.plugs) ? list.plugs : []
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

function keyTestLabel(status: KeyTestStatus): string {
  if (status === 'ok') return t('setup.v2.keys.testOk')
  if (status === 'fail') return t('setup.v2.keys.testFail')
  if (status === 'neutral') return t('setup.v2.keys.noKeyNeeded')
  return t('setup.v2.keys.testError')
}

function keyTestClass(status: KeyTestStatus): string {
  if (status === 'ok') return 'badge-ok'
  if (status === 'fail') return 'badge-fail'
  if (status === 'neutral') return 'badge-muted'
  return 'badge-warn'
}

/** Switch provider without losing the drafts typed for the previous one. */
function onProviderChange(stage: Stage, event: Event): void {
  const name = (event.target as HTMLSelectElement).value
  const provider = providersByStage.value[stage].find((item) => item.name === name)
  providerSelections.value = { ...providerSelections.value, [stage]: name }
  voiceDrafts.value = {
    ...voiceDrafts.value,
    [stage]: provider?.defaultVoice || provider?.voices[0]?.value || '',
  }
  keySaveError.value = ''
  clearKeyTest(stage)
}

function onKeyInput(stage: Stage, provider: string, env: string, event: Event): void {
  keyDrafts.value = {
    ...keyDrafts.value,
    [keyDraftId(stage, provider, env)]: (event.target as HTMLInputElement).value,
  }
  keySaveError.value = ''
  clearKeyTest(stage)
}

function onVoiceChange(stage: Stage, event: Event): void {
  voiceDrafts.value = {
    ...voiceDrafts.value,
    [stage]: (event.target as HTMLSelectElement | HTMLInputElement).value,
  }
  keySaveError.value = ''
}

/**
 * `{llm:{provider,keys}, asr:{provider,keys}, tts:{provider,voice,keys}}`.
 * Only changed stages are sent, and empty key drafts are omitted so an empty
 * input never overwrites a stored secret.
 */
function buildProviderPayload(): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  for (const row of providerRows.value) {
    if (!row.selectedProvider) continue
    const keys: Record<string, string> = {}
    for (const field of row.fields) {
      const value = (keyDrafts.value[keyDraftId(row.stage, row.selectedName, field.name)] ?? '').trim()
      if (value) keys[field.name] = value
    }
    const base = providerBaseline.value[row.stage]
    const providerChanged = row.selectedName !== (base?.provider ?? '')
    const voiceChanged =
      row.stage === 'tts' && Boolean(row.voice) && row.voice !== (base?.voice ?? '')
    if (!Object.keys(keys).length && !providerChanged && !voiceChanged) continue
    const entry: Record<string, unknown> = { provider: row.selectedName }
    if (Object.keys(keys).length) entry.keys = keys
    if (row.stage === 'tts' && row.voice.trim()) entry.voice = row.voice.trim()
    payload[row.stage] = entry
  }
  return payload
}

async function saveKeys(): Promise<void> {
  if (busyStep.value || busyGlobal.value || keyTestBusy.value) return
  keySaveError.value = ''
  const values = buildProviderPayload()
  if (!Object.keys(values).length) {
    keySaveError.value = t('setup.v2.keys.nothingToSave')
    return
  }
  busyStep.value = 'keys'
  expanded.value = { ...expanded.value, keys: true }
  try {
    const response = await api.setupStep({
      plug: selectedPlug.value,
      step: 'keys',
      action: 'providers.save',
      values,
    })
    if (Array.isArray((response as unknown as Record<string, unknown>).steps)) {
      await applyState(response)
    } else {
      await refreshState({ silent: true })
    }
    keyDrafts.value = {}
    keyTestResults.value = {}
    await loadProviderCatalog()
    notice.value = t('setup.v2.keys.saved')
  } catch (exc) {
    const issue = describeError(exc)
    const notReady = ['unknown_action', 'http_400', 'http_404', 'http_405'].includes(issue.code)
    keySaveError.value = notReady
      ? t('setup.v2.keys.saveUnavailable', { code: issue.code })
      : t('setup.v2.keys.saveFailed', { message: issue.message })
  } finally {
    busyStep.value = ''
  }
}

function keyTestCheckName(check: unknown, index: number): string {
  if (!check || typeof check !== 'object') {
    return `${t('setup.v2.details')} ${index + 1}`
  }
  const obj = check as Record<string, unknown>
  const name = obj.name ?? obj.id ?? obj.key ?? obj.env
  return name ? String(name) : `${t('setup.v2.details')} ${index + 1}`
}

/**
 * Parse a key-test result without assuming one backend shape: the current API
 * returns `{ok, stage, provider, message, details}`, while older shapes wrap the
 * result in the step detail/evidence or the whole setup state.
 */
function parseKeyTestResult(response: unknown, row: ProviderRowView): KeyTestResult {
  const product = row.selectedProvider?.displayName ?? row.stage
  // Local ASR (sherpa) has no credential: "no safe online test" is its healthy state.
  if (row.selectedProvider?.keyless) {
    return { status: 'neutral', message: t('setup.v2.keys.testLocal') }
  }
  const record =
    response && typeof response === 'object'
      ? (response as Record<string, unknown>)
      : {}
  const rawSteps = record.steps
  const keysStep = Array.isArray(rawSteps)
    ? (rawSteps as SetupStepState[]).find((step) => step.id === 'keys')
    : undefined
  const detail = (keysStep?.detail ?? {}) as Record<string, unknown>
  const stepEvidence = (keysStep?.evidence ?? {}) as Record<string, unknown>
  const candidates = [
    // The backend answers keys.test with the result itself.
    record,
    detail.keys_test,
    detail.key_test,
    detail.test,
    detail.result,
    stepEvidence.keys_test,
    stepEvidence.key_test,
    stepEvidence.test,
    record.keys_test,
    record.key_test,
    record.test,
  ]
  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) continue
    const obj = candidate as Record<string, unknown>
    const rawChecks = obj.checks ?? obj.results
    const checks = Array.isArray(rawChecks) ? rawChecks : []
    const failed = checks.filter(
      (check) =>
        Boolean(check) &&
        typeof check === 'object' &&
        (check as Record<string, unknown>).ok === false,
    )
    const explicit = [obj.ok, obj.passed, obj.success].find(
      (value) => typeof value === 'boolean',
    )
    const message = pickLocalized(obj.message ?? obj.hint ?? obj.error ?? '')
    // A keyless/local provider reports ok:false with this sentence; it is not a failure.
    if (
      /no safe online test|needs no key|keyless|local provider/i.test(message) &&
      explicit !== true
    ) {
      return {
        status: 'neutral',
        message: t('setup.v2.keys.testLocal'),
        details: message || undefined,
      }
    }
    const details = failed.length
      ? t('setup.v2.keys.testChecksFailed', {
          items: failed
            .map((check, index) => keyTestCheckName(check, index))
            .join(' / '),
        })
      : undefined
    if (typeof explicit === 'boolean') {
      const ok = explicit && failed.length === 0
      return {
        status: ok ? 'ok' : 'fail',
        message: ok
          ? t('setup.v2.keys.testPass', { provider: product })
          : t('setup.v2.keys.testFailMessage', { provider: product }),
        details: details || message || undefined,
      }
    }
    if (checks.length) {
      const ok = failed.length === 0
      return {
        status: ok ? 'ok' : 'fail',
        message: ok
          ? t('setup.v2.keys.testPass', { provider: product })
          : t('setup.v2.keys.testFailMessage', { provider: product }),
        details,
      }
    }
    if (message) return { status: 'error', message }
  }
  return { status: 'error', message: t('setup.v2.keys.testNoResult') }
}

/** Credentials currently typed for one row; sent only to the test call, never stored. */
function draftKeys(row: ProviderRowView): Record<string, string> {
  const keys: Record<string, string> = {}
  for (const field of row.fields) {
    const value = (keyDrafts.value[keyDraftId(row.stage, row.selectedName, field.name)] ?? '').trim()
    if (value) keys[field.name] = value
  }
  return keys
}

function draftVoice(row: ProviderRowView): string {
  return row.stage === 'tts' ? row.voice.trim() : ''
}

function rejectsDraftValues(exc: unknown): boolean {
  const issue = describeError(exc)
  return issue.code === 'invalid_values' || issue.code === 'http_422'
}

/** One test request; drafts ride along so an unsaved provider can be tested. */
function requestKeyTest(row: ProviderRowView, includeDrafts: boolean): Promise<unknown> {
  const values: Record<string, unknown> = {
    stage: row.stage,
    provider: row.selectedName,
  }
  if (includeDrafts) {
    const keys = draftKeys(row)
    if (Object.keys(keys).length) values.keys = keys
    const voice = draftVoice(row)
    if (voice) values.voice = voice
  }
  return api.setupStep({
    plug: selectedPlug.value,
    step: 'keys',
    action: 'keys.test',
    values,
  })
}

async function testProviderRow(row: ProviderRowView): Promise<void> {
  if (!row.selectedProvider || busyStep.value || busyGlobal.value || keyTestBusy.value) return
  clearKeyTest(row.stage)
  expanded.value = { ...expanded.value, keys: true }
  // Local ASR / any keyless provider: nothing to test online, and it is not a failure.
  if (row.selectedProvider.keyless) {
    keyTestResults.value = {
      ...keyTestResults.value,
      [row.stage]: { status: 'neutral', message: t('setup.v2.keys.testLocal') },
    }
    return
  }
  keyTestBusy.value = row.stage
  const drafts = draftKeys(row)
  const hasDrafts = Object.keys(drafts).length > 0 || Boolean(draftVoice(row))
  try {
    let response: unknown
    let savedOnly = false
    try {
      response = await requestKeyTest(row, true)
    } catch (exc) {
      if (!hasDrafts || !rejectsDraftValues(exc)) throw exc
      // Older control planes only read stored secrets: retest them and say so.
      savedOnly = true
      response = await requestKeyTest(row, false)
    }
    const record =
      response && typeof response === 'object'
        ? (response as Record<string, unknown>)
        : {}
    if (Array.isArray(record.steps)) {
      await applyState(response as SetupStepResponse)
    }
    const result = parseKeyTestResult(response, row)
    if (hasDrafts && result.status === 'fail') {
      result.details = [result.details, t('setup.v2.keys.testSavedOnly')]
        .filter(Boolean)
        .join(' ')
    } else if (savedOnly && result.status === 'ok') {
      result.details = [result.details, t('setup.v2.keys.testSavedOnly')]
        .filter(Boolean)
        .join(' ')
    }
    keyTestResults.value = { ...keyTestResults.value, [row.stage]: result }
  } catch (exc) {
    const issue = describeError(exc)
    const unavailable = [
      'unknown_action',
      'http_404',
      'http_405',
      'keys_test_unavailable',
    ].includes(issue.code)
    const saveFirst = ['unknown_provider', 'unknown_stage', 'invalid_values'].includes(
      issue.code,
    )
    keyTestResults.value = {
      ...keyTestResults.value,
      [row.stage]: {
        status: 'error',
        message: saveFirst
          ? t('setup.v2.keys.testSaveFirst')
          : unavailable
            ? t('setup.v2.keys.testUnavailable', { code: issue.code })
            : issue.message,
        details: issue.hint || issue.details || undefined,
      },
    }
  } finally {
    keyTestBusy.value = ''
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
  if (!prepareAllChecked.value) return
  if (manifestClass.value === 'B' && !prepareRollbackAck.value) return
  const ok = await runStep(
    'prepare',
    'manual.confirm',
    { confirmed: true, rollback_ack: prepareRollbackAck.value },
    { retry: confirmPrepare },
  )
  if (ok) {
    prepareChecks.value = {}
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
    notice.value = t('setup.v2.connect.status.connected')
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

          <!-- keys: one provider per use; only that provider's fields are shown -->
          <template v-if="step.id === 'keys'">
            <p class="field-hint">{{ t('setup.v2.keys.providersLede') }}</p>
            <p v-if="providerLoading" class="field-hint">{{ t('setup.v2.keys.catalogLoading') }}</p>
            <p v-if="providerFallback" class="field-hint setup-provider-fallback">
              {{ t('setup.v2.keys.catalogFallback') }}
              <span v-if="providerError" class="mono"> ({{ providerError }})</span>
            </p>
            <p v-else-if="providerError && !providerDoc" class="field-hint fail">
              {{ t('setup.v2.keys.catalogFailed') }}
            </p>

            <div
              v-if="providerDoc"
              class="setup-provider-table"
              role="table"
              :aria-label="t('setup.v2.keys.providersLede')"
            >
              <div class="setup-provider-row setup-provider-head" role="row">
                <span role="columnheader">{{ t('setup.v2.keys.table.use') }}</span>
                <span role="columnheader">{{ t('setup.v2.keys.table.provider') }}</span>
                <span role="columnheader">{{ t('setup.v2.keys.table.credentials') }}</span>
                <span role="columnheader">{{ t('setup.v2.keys.table.voice') }}</span>
                <span role="columnheader">{{ t('setup.v2.keys.table.actions') }}</span>
              </div>
              <div
                v-for="row in providerRows"
                :key="row.stage"
                class="setup-provider-row"
                :class="`is-${row.stage}`"
                role="row"
              >
                <div class="setup-provider-use" role="rowheader">
                  <strong>{{ t(STAGE_USE_LABELS[row.stage]) }}</strong>
                  <span
                    v-if="row.selectedProvider"
                    class="badge"
                    :class="providerBadgeClass(row)"
                  >
                    {{ providerBadgeLabel(row) }}
                  </span>
                </div>

                <div class="setup-provider-pick" role="cell">
                  <select
                    v-if="row.providers.length"
                    :value="row.selectedName"
                    :aria-label="t('setup.v2.keys.chooseProvider')"
                    :disabled="busyStep !== '' || busyGlobal !== ''"
                    @change="onProviderChange(row.stage, $event)"
                  >
                    <option v-for="provider in row.providers" :key="provider.name" :value="provider.name">
                      {{ providerOptionLabel(provider) }}
                    </option>
                  </select>
                  <p v-else class="field-hint">{{ t('setup.v2.keys.noProvider') }}</p>
                  <template v-if="row.selectedProvider">
                    <p v-if="row.selectedProvider.hint" class="field-hint">
                      {{ row.selectedProvider.hint }}
                    </p>
                    <a
                      v-if="safeHref(row.selectedProvider.docsUrl)"
                      :href="safeHref(row.selectedProvider.docsUrl)"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="setup-provider-docs"
                    >
                      {{ t('setup.v2.keys.docs') }} <AppIcon name="external" :size="12" />
                    </a>
                  </template>
                </div>

                <div class="setup-provider-creds" role="cell">
                  <p v-if="row.selectedProvider && !row.fields.length" class="field-hint">
                    {{ t('setup.v2.keys.noFields') }}
                  </p>
                  <div v-for="field in row.fields" :key="field.name" class="setup-provider-field">
                    <div class="setup-provider-field-label">
                      <label class="field-label" :for="`setup-key-${row.stage}-${field.name}`">
                        {{ field.label || keyRoleLabel(field.role) }}
                      </label>
                      <span class="badge" :class="field.configured ? 'badge-ok' : 'badge-warn'">
                        {{ field.configured ? t('setup.keys.configured') : t('setup.keys.missing') }}
                      </span>
                    </div>
                    <p v-if="field.hint" class="field-hint">{{ field.hint }}</p>
                    <code class="setup-key-env mono">
                      {{ t('setup.v2.keys.envLabel') }}: {{ field.env }}
                    </code>
                    <input
                      :id="`setup-key-${row.stage}-${field.name}`"
                      :value="keyDrafts[keyDraftId(row.stage, row.selectedName, field.name)] ?? ''"
                      type="password"
                      autocomplete="off"
                      spellcheck="false"
                      :placeholder="t('secrets.placeholder')"
                      @input="onKeyInput(row.stage, row.selectedName, field.name, $event)"
                    />
                  </div>
                </div>

                <div class="setup-provider-voice" role="cell">
                  <template v-if="row.stage === 'tts' && row.selectedProvider">
                    <select
                      v-if="row.voices.length"
                      :value="row.voice"
                      :aria-label="t('setup.v2.keys.table.voice')"
                      :disabled="busyStep !== '' || busyGlobal !== ''"
                      @change="onVoiceChange(row.stage, $event)"
                    >
                      <option v-for="voice in row.voices" :key="voice.value" :value="voice.value">
                        {{ voice.label }}{{ voice.isDefault ? ` (${t('setup.v2.keys.voiceDefault')})` : '' }}
                      </option>
                    </select>
                    <input
                      v-else
                      :value="row.voice"
                      type="text"
                      autocomplete="off"
                      spellcheck="false"
                      :placeholder="t('setup.v2.keys.voicePlaceholder')"
                      :aria-label="t('setup.v2.keys.table.voice')"
                      :disabled="busyStep !== '' || busyGlobal !== ''"
                      @input="onVoiceChange(row.stage, $event)"
                    />
                    <p class="field-hint">{{ t('setup.v2.keys.voiceHint') }}</p>
                  </template>
                  <span v-else class="setup-provider-na">{{ t('setup.v2.keys.noVoice') }}</span>
                </div>

                <div class="setup-provider-actions" role="cell">
                  <button
                    type="button"
                    class="btn btn-sm"
                    :disabled="!row.selectedProvider || busyStep !== '' || busyGlobal !== '' || keyTestBusy !== ''"
                    @click="testProviderRow(row)"
                  >
                    {{ keyTestBusy === row.stage ? t('setup.v2.keys.testing') : t('setup.v2.keys.test') }}
                  </button>
                  <p class="field-hint">{{ t('setup.v2.keys.testUsesSaved') }}</p>
                  <div
                    v-if="keyTestResults[row.stage]"
                    class="setup-provider-test"
                    :class="`is-${keyTestResults[row.stage].status}`"
                    role="status"
                  >
                    <span class="badge" :class="keyTestClass(keyTestResults[row.stage].status)">
                      {{ keyTestLabel(keyTestResults[row.stage].status) }}
                    </span>
                    <div>
                      <strong>{{ keyTestResults[row.stage].message }}</strong>
                      <p v-if="keyTestResults[row.stage].details" class="field-hint">
                        {{ keyTestResults[row.stage].details }}
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div class="actions setup-provider-save">
              <button
                type="button"
                class="btn btn-primary"
                :disabled="busyStep !== '' || busyGlobal !== '' || keyTestBusy !== '' || !keyDirty"
                @click="saveKeys"
              >
                {{ busyStep === 'keys' ? t('common.saving') : t('setup.v2.keys.save') }}
              </button>
              <span class="field-hint">{{ t('setup.v2.keys.saveHint') }}</span>
            </div>
            <p v-if="keySaveError" class="note fail" role="alert">{{ keySaveError }}</p>
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
              <div v-if="deviceSide.rollback" class="setup-manifest-row setup-manifest-block">
                <span class="field-label">{{ t('setup.v2.prepare.rollback') }}</span>
                <p>{{ deviceSide.rollback }}</p>
              </div>
              <p v-else-if="manifestClass === 'B'" class="note fail">{{ t('setup.v2.prepare.rollbackMissing') }}</p>
              <p v-if="evidence(step).confirmed" class="ok">{{ t('setup.v2.prepare.confirmed') }}</p>
            </div>
            <p v-else-if="deviceSide" class="field-hint">{{ t('setup.v2.prepare.noArtifact') }}</p>

            <div v-if="hasAction(step, 'manual.confirm') && deviceSide && deviceSide.artifact !== 'none'" class="setup-fields">
              <div class="setup-checklist">
                <div class="setup-checklist-head">
                  <span class="field-label">{{ t('setup.v2.prepare.checklist') }}</span>
                  <span class="field-hint">
                    {{ t('setup.v2.prepare.checklistProgress', { done: prepareCheckedCount, total: prepareSteps.length }) }}
                  </span>
                </div>
                <p v-if="!prepareFromManifest" class="field-hint">
                  {{ t('setup.v2.prepare.fallbackNote') }}
                </p>
                <label
                  v-for="(item, at) in prepareSteps"
                  :key="item.id"
                  class="setup-check setup-prepare-item"
                  :class="{ checked: prepareChecks[item.id] }"
                >
                  <input
                    v-model="prepareChecks[item.id]"
                    type="checkbox"
                    :disabled="busyStep !== ''"
                  />
                  <span class="setup-prepare-num mono">{{ at + 1 }}</span>
                  <span>{{ item.text }}</span>
                </label>
              </div>
              <label v-if="manifestClass === 'B'" class="setup-check">
                <input v-model="prepareRollbackAck" type="checkbox" :disabled="busyStep !== ''" />
                <span>{{ t('setup.v2.prepare.rollbackAck') }}</span>
              </label>
              <p class="field-hint setup-prepare-token-note">
                {{ t('setup.v2.prepare.tokenNote') }}
              </p>
              <div v-if="prepareLaterNotes.length" class="setup-prepare-later">
                <span class="field-label">{{ t('setup.v2.prepare.laterTitle') }}</span>
                <ul>
                  <li v-for="item in prepareLaterNotes" :key="item.id">{{ item.text }}</li>
                </ul>
              </div>
              <p class="field-hint">{{ t('setup.v2.prepare.checkAllHint') }}</p>
              <div class="actions">
                <button
                  type="button"
                  class="btn btn-primary"
                  :disabled="busyStep !== '' || busyGlobal !== '' || !prepareAllChecked || (manifestClass === 'B' && !prepareRollbackAck)"
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
              <p class="field-hint setup-step-lede">{{ t('setup.v2.params.lede') }}</p>
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

                <div class="field-row" :class="{ invalid: fieldErrors.volume }">
                  <div class="field-meta">
                    <label class="field-label" for="setup-param-volume">{{ t('setup.v2.params.volume') }}</label>
                    <p class="field-hint">{{ t('setup.v2.params.volumeHint') }}</p>
                  </div>
                  <div class="field-control">
                    <input
                      id="setup-param-volume"
                      v-model="volumeDraft"
                      type="number"
                      inputmode="numeric"
                      min="0"
                      max="100"
                      step="1"
                    />
                    <p v-if="fieldErrors.volume" class="field-hint fail">{{ fieldErrors.volume }}</p>
                  </div>
                </div>

                <div class="field-row">
                  <div class="field-meta">
                    <label class="field-label" for="setup-param-led">{{ t('setup.v2.params.led') }}</label>
                    <p class="field-hint">{{ t('setup.v2.params.ledHint') }}</p>
                  </div>
                  <div class="field-control">
                    <input
                      id="setup-param-led"
                      v-model="ledEnabled"
                      type="checkbox"
                      class="setup-checkbox"
                    />
                  </div>
                </div>

                <div class="field-row" :class="{ invalid: fieldErrors.listen_port }">
                  <div class="field-meta">
                    <label class="field-label" for="setup-param-listen-port">{{ t('setup.v2.params.listenPort') }}</label>
                    <p class="field-hint">{{ t('setup.v2.params.listenPortHint', { port: listenPort }) }}</p>
                  </div>
                  <div class="field-control">
                    <input
                      id="setup-param-listen-port"
                      v-model="listenPortDraft"
                      type="number"
                      inputmode="numeric"
                      min="1"
                      max="65535"
                      step="1"
                      :placeholder="String(listenPort)"
                    />
                    <p v-if="fieldErrors.listen_port" class="field-hint fail">{{ fieldErrors.listen_port }}</p>
                  </div>
                </div>

                <div class="field-row" :class="{ invalid: fieldErrors.mic_gain_db }">
                  <div class="field-meta">
                    <label class="field-label" for="setup-param-mic-gain">{{ t('setup.v2.params.micGain') }}</label>
                    <p class="field-hint">{{ t('setup.v2.params.micGainHint') }}</p>
                  </div>
                  <div class="field-control">
                    <input
                      id="setup-param-mic-gain"
                      v-model="micGainDraft"
                      type="number"
                      inputmode="decimal"
                      min="0"
                      max="42"
                      step="0.5"
                      placeholder="0"
                    />
                    <p v-if="fieldErrors.mic_gain_db" class="field-hint fail">{{ fieldErrors.mic_gain_db }}</p>
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
                <button
                  type="button"
                  class="btn"
                  :disabled="busyStep !== '' || busyGlobal !== '' || !deviceId"
                  @click="runTest"
                >
                  {{ busyStep === 'test' ? t('setup.v2.params.testing') : t('setup.v2.params.test') }}
                </button>
              </div>
              <div
                v-if="testOutcome"
                class="setup-test-outcome"
                :class="testOutcome.status === 'ok' ? 'is-ok' : 'is-fail'"
                role="status"
              >
                <span class="badge" :class="testOutcome.status === 'ok' ? 'badge-ok' : 'badge-fail'">
                  {{ testOutcome.status === 'ok' ? t('setup.v2.test.ok') : t('setup.v2.test.fail') }}
                </span>
                <span>{{ testOutcome.message }}</span>
                <span v-if="testOutcome.at" class="field-hint">
                  {{ t('setup.v2.test.at', { at: testOutcome.at }) }}
                </span>
              </div>
              <p v-if="issues.test" class="note fail" role="alert">
                {{ issues.test?.message }}
                <template v-if="issues.test?.hint"> — {{ issues.test?.hint }}</template>
              </p>
              <p v-else-if="!testOutcome" class="field-hint">{{ t('setup.v2.params.testHint') }}</p>
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

          <!-- connect: read-only info card + one human status line -->
          <template v-else-if="step.id === 'connect'">
            <div class="setup-connect-status" :class="`is-${connectStatus.tone}`" role="status">
              <span class="field-label">{{ t('setup.v2.connect.statusLabel') }}</span>
              <span class="badge" :class="connectToneClass(connectStatus.tone)">
                {{ connectStatus.badge }}
              </span>
              <span>{{ connectStatus.text }}</span>
            </div>
            <p v-if="connectHint && connectStatus.tone !== 'ok'" class="field-hint setup-connect-hint">
              {{ t('setup.connect.hint') }}: {{ connectHint }}
            </p>
            <template v-if="!connectSkipped && deviceId">
              <p v-if="connectBusy" class="field-hint">{{ t('setup.connect.loading') }}</p>
              <div v-else-if="connectError" class="note fail" role="alert">
                {{ t('setup.connect.loadError', { message: connectError }) }}
                <button type="button" class="btn btn-sm" @click="loadConnect(deviceId, true)">
                  {{ t('setup.connect.retry') }}
                </button>
              </div>
              <div v-else-if="connectInfo" class="setup-connect-card">
                <div class="setup-connect-card-head">
                  <span class="field-label">{{ t('setup.v2.connect.infoTitle') }}</span>
                  <span class="field-hint">{{ t('setup.v2.connect.readonlyHint') }}</span>
                </div>
                <div class="setup-connect-row">
                  <span class="setup-connect-row-label">{{ t('setup.connect.wsUrl') }}</span>
                  <div class="copy-row">
                    <code class="setup-connect-value mono">{{ wsDisplay }}</code>
                    <button
                      type="button"
                      class="btn btn-sm"
                      :disabled="wsCopyDisabled"
                      @click="copyText(connectInfo.ws_url, 'ws')"
                    >
                      {{ copied === 'ws' ? t('setup.connect.copied') : t('setup.connect.copy') }}
                    </button>
                  </div>
                  <p class="field-hint">{{ t('setup.v2.connect.wsHint') }}</p>
                  <p v-if="wsLoopback" class="note fail setup-connect-loopback" role="alert">
                    {{ t('setup.v2.connect.loopbackHint') }}
                  </p>
                </div>
                <div class="setup-connect-row">
                  <div class="setup-connect-row-head">
                    <span class="setup-connect-row-label">{{ t('setup.connect.token') }}</span>
                    <span class="badge" :class="connectInfo.configured ? 'badge-ok' : 'badge-warn'">
                      {{ connectInfo.configured ? t('setup.keys.configured') : t('setup.keys.missing') }}
                    </span>
                  </div>
                  <div class="copy-row">
                    <code class="setup-connect-value mono">{{ tokenVisible ? connectInfo.token : maskedToken }}</code>
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
                  <p class="field-hint">
                    {{ t('setup.v2.connect.tokenHintCard') }}
                    <code class="mono">{{ connectInfo.token_env || tokenEnv }}</code>
                  </p>
                </div>
                <div class="setup-connect-row">
                  <span class="setup-connect-row-label">{{ t('setup.v2.connect.listenPort') }}</span>
                  <code class="setup-connect-value mono">{{ listenPort }}</code>
                  <p class="field-hint">{{ t('setup.v2.connect.portHint') }}</p>
                </div>
                <p class="field-hint setup-connect-token-update">
                  {{ t('setup.v2.connect.updateTokenHint') }}
                </p>
              </div>

              <div v-if="connectInfo && !connectInfo.configured" class="connect-warning">
                <strong>{{ t('setup.connect.missingTitle') }}</strong>
                <p>{{ t('setup.connect.missingBody', { env: connectInfo.token_env || tokenEnv }) }}</p>
                <pre class="connect-command mono">{{ t('setup.connect.rotate') }}</pre>
              </div>
              <div v-else-if="connectInfo?.allow_no_token" class="connect-warning">
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
            </div>
            <div
              v-if="testOutcome"
              class="setup-test-outcome"
              :class="testOutcome.status === 'ok' ? 'is-ok' : 'is-fail'"
              role="status"
            >
              <span class="badge" :class="testOutcome.status === 'ok' ? 'badge-ok' : 'badge-fail'">
                {{ testOutcome.status === 'ok' ? t('setup.v2.test.ok') : t('setup.v2.test.fail') }}
              </span>
              <span>{{ testOutcome.message }}</span>
              <span v-if="testOutcome.at" class="field-hint">
                {{ t('setup.v2.test.at', { at: testOutcome.at }) }}
              </span>
            </div>
            <p
              v-if="testOutcome?.status === 'fail' && connectHint"
              class="field-hint setup-connect-hint"
            >
              {{ t('setup.connect.hint') }}: {{ connectHint }}
            </p>
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

.setup-step-lede {
  margin-top: 0;
}

.setup-provider-table {
  margin-top: var(--s2);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  overflow: hidden;
  background: var(--surface);
}

.setup-provider-row {
  display: grid;
  grid-template-columns:
    minmax(92px, 0.8fr)
    minmax(150px, 1.1fr)
    minmax(0, 2fr)
    minmax(132px, 1fr)
    minmax(144px, 1.05fr);
  gap: var(--s3);
  align-items: start;
  padding: var(--s3);
  border-top: 1px solid var(--line);
}

.setup-provider-row:first-child {
  border-top: 0;
}

.setup-provider-head {
  padding-top: 8px;
  padding-bottom: 8px;
  background: var(--surface-2);
  color: var(--muted);
  font-size: 10.5px;
  text-transform: uppercase;
  letter-spacing: 0.07em;
}

.setup-provider-use {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 5px;
  min-width: 0;
}

.setup-provider-use strong {
  font-size: 13.5px;
}

.setup-provider-pick,
.setup-provider-voice,
.setup-provider-actions {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.setup-provider-pick select,
.setup-provider-voice select,
.setup-provider-voice input {
  width: 100%;
  max-width: 100%;
}

.setup-provider-docs {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
}

.setup-provider-creds {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  min-width: 0;
}

.setup-provider-field {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.setup-provider-field-label {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.setup-provider-field input {
  width: 100%;
}

.setup-provider-na {
  color: var(--muted);
}

.setup-provider-actions .btn {
  align-self: flex-start;
}

.setup-provider-test {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 7px 8px;
  border-radius: var(--r-xs);
  font-size: 11.5px;
  line-height: 1.45;
}

.setup-provider-test.is-ok {
  background: var(--ok-soft);
}

.setup-provider-test.is-fail {
  background: var(--fail-soft);
}

.setup-provider-test.is-error {
  background: var(--warn-soft);
}

.setup-provider-test p {
  margin-top: 2px;
}

.setup-provider-save {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  margin-top: var(--s3);
}

.setup-provider-save .field-hint {
  margin: 0;
}

.setup-provider-fallback {
  padding: 6px 8px;
  border-radius: var(--r-xs);
  background: var(--warn-soft);
}

.setup-key-env {
  display: inline-block;
  margin-top: 2px;
  color: var(--muted);
  font-size: 10.5px;
  overflow-wrap: anywhere;
}

.setup-step-lede + .note {
  margin-top: var(--s2);
}

.setup-checklist {
  margin: var(--s3) 0;
  padding: var(--s3);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-2);
}

.setup-checklist-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--s2);
  flex-wrap: wrap;
  margin-bottom: var(--s2);
}

.setup-checklist .setup-prepare-item {
  margin: 0;
  padding: 7px 0;
  border-top: 1px solid var(--line);
}

.setup-checklist .setup-prepare-item:first-of-type {
  border-top: 0;
}

.setup-prepare-item.checked {
  color: var(--muted);
}

.setup-prepare-num {
  min-width: 16px;
  color: var(--accent);
  font-size: 11px;
}

.setup-prepare-token-note {
  margin-top: var(--s2);
}

.setup-prepare-later {
  margin-top: var(--s2);
  padding: var(--s2) var(--s3);
  border-left: 2px solid var(--line-strong);
  background: var(--surface-2);
  border-radius: 0 var(--r-xs) var(--r-xs) 0;
  font-size: 12px;
  line-height: 1.55;
  color: var(--ink-2);
}

.setup-prepare-later ul {
  margin: 4px 0 0;
  padding-left: 18px;
}

.setup-prepare-later li + li {
  margin-top: 3px;
}

.setup-connect-hint {
  margin-top: 4px;
}

.setup-connect-loopback {
  margin-top: var(--s2);
}

.setup-connect-token-update {
  padding: var(--s2) var(--s3) 0;
  border-top: 1px solid var(--line);
}

.setup-test-outcome {
  display: flex;
  align-items: baseline;
  gap: var(--s2);
  flex-wrap: wrap;
  margin-top: var(--s2);
  padding: 8px 10px;
  border-radius: var(--r-xs);
  font-size: 12px;
  line-height: 1.5;
}

.setup-test-outcome.is-ok {
  background: var(--ok-soft);
}

.setup-test-outcome.is-fail {
  background: var(--fail-soft);
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

.setup-connect-status {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  margin: var(--s2) 0 var(--s3);
  padding: 8px 10px;
  border-left: 2px solid var(--line-strong);
  border-radius: 0 var(--r-sm) var(--r-sm) 0;
  background: var(--surface-2);
  font-size: 12.5px;
  line-height: 1.5;
}

.setup-connect-status.is-ok {
  border-left-color: var(--ok);
  background: var(--ok-soft);
}

.setup-connect-status.is-warn {
  border-left-color: var(--warn);
  background: var(--warn-soft);
}

.setup-connect-status.is-fail {
  border-left-color: var(--fail);
  background: var(--fail-soft);
}

.setup-connect-card {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
  margin-top: var(--s2);
  padding: var(--s3);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-2);
}

.setup-connect-card-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.setup-connect-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  padding-top: var(--s3);
  border-top: 1px solid var(--line);
}

.setup-connect-card-head + .setup-connect-row {
  padding-top: 0;
  border-top: 0;
}

.setup-connect-row-head {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.setup-connect-row-label {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ink-2);
}

.setup-connect-value {
  display: block;
  flex: 1;
  min-width: 0;
  padding: 6px 8px;
  border: 1px solid var(--line);
  border-radius: var(--r-xs);
  background: var(--surface);
  color: var(--ink-2);
  font-size: 11.5px;
  overflow-wrap: anywhere;
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

  .setup-provider-row {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--s2);
  }

  .setup-provider-head {
    display: none;
  }

  .setup-provider-use {
    flex-direction: row;
    align-items: center;
    flex-wrap: wrap;
  }

  .setup-provider-actions .btn {
    width: 100%;
    min-height: 40px;
  }

  .setup-provider-save {
    align-items: stretch;
    flex-direction: column;
  }

  .setup-connect-status {
    align-items: flex-start;
  }

  .copy-row {
    flex-wrap: wrap;
  }

  .copy-row input,
  .copy-row .setup-connect-value {
    flex: 1 1 100%;
    min-height: 44px;
  }

  .copy-row .btn {
    flex: 1 1 calc(50% - var(--s2));
    min-height: 44px;
  }
}
</style>
