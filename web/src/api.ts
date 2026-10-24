import type { PanelSpec } from './panels'

const TOKEN_KEY = 'rui-token'

export const token = {
  get: () => localStorage.getItem(TOKEN_KEY) ?? '',
  set: (v: string) => localStorage.setItem(TOKEN_KEY, v),
}

/** One entry of a FastAPI 422 body: `loc` is the path to the offending field. */
export interface ValidationIssue {
  loc?: (string | number)[]
  msg: string
  type?: string
}

function summarizeDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((raw) => {
        const issue = raw as ValidationIssue
        // Drop the leading section name so the message names the field people see.
        const path = (issue.loc ?? []).slice(1).join('.')
        return path ? `${path}: ${issue.msg}` : issue.msg
      })
      .join('; ')
  }
  if (detail && typeof detail === 'object') return JSON.stringify(detail)
  return ''
}

/**
 * A failed request, carrying the backend's own words.
 *
 * The console used to throw `500 Internal Server Error` and nothing else, which for this
 * API is the least useful half of the answer: a 400 says which key was rejected, a 409
 * says the config file is missing from disk, a 422 lists the field and the bound it broke.
 * `detail` is kept raw so a page can map a 422 onto individual inputs.
 */
export class ApiError extends Error {
  readonly status: number
  readonly statusText: string
  readonly detail: unknown

  constructor(status: number, statusText: string, detail: unknown) {
    const said = summarizeDetail(detail)
    super(`${status} ${said || statusText}`)
    this.name = 'ApiError'
    this.status = status
    this.statusText = statusText
    this.detail = detail
  }

  /** The 422 list, when this really was a validation failure. */
  get issues(): ValidationIssue[] {
    return Array.isArray(this.detail) ? (this.detail as ValidationIssue[]) : []
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const t = token.get()
  if (t) headers.set('X-Auth-Token', t)
  if (init.body) headers.set('Content-Type', 'application/json')
  const res = await fetch(path, { ...init, headers })
  if (res.status === 401) {
    // The console has nowhere else to ask: highlight the token field (App.vue).
    window.dispatchEvent(new Event('rui-401'))
  }
  if (!res.ok) {
    let detail: unknown = null
    const raw = await res.text()
    if (raw) {
      try {
        detail = (JSON.parse(raw) as { detail?: unknown }).detail ?? raw
      } catch {
        detail = raw
      }
    }
    throw new ApiError(res.status, res.statusText, detail)
  }
  return (await res.json()) as T
}

export interface Check {
  name: string
  status: 'ok' | 'warn' | 'fail'
  value: string
  hint: string
}

export interface DoctorReport {
  verdict: 'ok' | 'degraded' | 'unsupported'
  aec_rtf: number
  cpu_share: number
  device_capacity: number
  capacity_basis: string
  measured_at: string
  checks: Check[]
}

export type DeviceStatus = 'ready' | 'disconnected' | 'unknown' | 'unavailable'

export interface Device {
  id: string
  adapter: string
  token_env?: string
  model: string
  room: string
  host: string
  transport: string
  /** Evidence-based link state: a null `connected` means no plane is running. */
  status: DeviceStatus | string
  connected?: boolean | null
  last_seen?: number | null
  latency_ms?: number | null
  error?: string
  /** Resolved plug id (`params.plug_id`, or the adapter's manifest). */
  plug_id?: string | null
  /** Non-secret, plug-declared values; secret params are filtered server-side. */
  params?: Record<string, unknown>
  capabilities: string[]
  missing_for_barge_in: string[]
  has_device_token?: boolean
}

export interface Provider {
  kind: string
  base_url: string
  model: string
  api_key_env: string
  app_id_env?: string
  api_secret_env?: string
  timeout_s: number
  voice: string
  has_key?: boolean
}

export interface AudioMetrics {
  type: string
  uptime_s: number
  aec_enabled: boolean
  aec_erle_db: number | null
  aec_channel: number
  ref_active: boolean
  speaker_connected: boolean
  /** False means the wake model never loaded: `kws_error` says why, wake_count is 0. */
  kws_enabled: boolean
  kws_error: string
  /** Segments handed to the control plane. A wake is not yet a segment. */
  utterances: number
  /** Wakes that ended with no segment: nobody spoke, or the speech was too short. */
  gate_discarded: number
  /** Cues cancelled because a new wake arrived before the previous one finished. */
  ack_skipped: number
  /**
   * How far ahead of real time the speaker's buffer is being fed. The speak loop
   * targets 300 ms and caps at 1000 ms; 0 while idle, near-zero while talking = underrun.
   */
  playback_lead_ms: number
  /** Who holds the speaker right now: '' / 'cue' / 'dialogue' / 'reminder' / 'music'. */
  output_owner: string
  playback_s: number
  frames_in: number
  frames_out: number
  frames_dropped: number
  no_consumer: number
  jitter_p50_ms: number
  jitter_p99_ms: number
  cpu_s: number
  audio_s: number
  rtf: number
}

export interface AudioStatus {
  running: boolean
  error?: string
  pid?: number | null
  device_id?: string
  capture?: { rate: number; channels: number; mic_channels: number; reference_channels: number }
  metrics?: AudioMetrics | null
  voice_frames_received?: number
  voice_bytes_received?: number
}

export interface Capture {
  rate: number
  channels: number
  mic_channels: number
  ref_channels: number
  bits: number
  mic_shift: number
  ref_shift: number
}

export interface Voice {
  rate: number
  frame_ms: number
  kws_enabled: boolean
  kws_model_dir: string
  /** Empty means `<kws_model_dir>/keywords.txt`. The real threshold is inside the file. */
  kws_keywords_file: string
  vad_min_speech_ms: number
  vad_end_silence_ms: number
  barge_in: boolean
}

/** What the keywords file says: the wake model's real gate (`#0.15`, boost `:2.00`). */
export interface KeywordMeta {
  file: string
  exists: boolean
  threshold: number | null
  boost: number | null
  keywords: number
}

export interface Playback {
  rate: number
  channels: number
  queue_ms: number
  fade_out_ms: number
  target_silence_ms: number
}

export type StageKey = 'asr' | 'tts' | 'llm'

/** `assistant.*` — identity and how long it may talk. Bounds mirror the pydantic schema. */
export interface AssistantConfig {
  name: string
  persona: string
  system_prompt: string
  reply_max_chars: number
  reply_max_chars_search: number
  reply_max_chars_long: number
}

/** `session.*` — conversation-level behaviour. */
export interface SessionConfig {
  vendor_quiet_s: number
  history_rounds: number
}

/**
 * What `PUT /api/v1/config` accepts: any subset of these two sections, nothing else.
 *
 * Values stay loose on purpose — the server coerces and validates them (a bad number must
 * come back as a 422 that names the field, not as a client-side guess about the bound).
 */
export type ConfigPatch = {
  assistant?: Record<string, string | number | boolean | null>
  session?: Record<string, string | number | boolean | null>
}

/**
 * `tools.search` — the one key slot a person actually types in.
 *
 * `has_key` is computed server-side from the environment *or* `secrets.yaml`; it is the only
 * thing the console ever learns about a key. No endpoint returns a value.
 */
export interface SearchConfig {
  api_key_env: string
  has_key?: boolean
  [k: string]: unknown
}

/** What `GET /api/v1/secrets` says: names that hold a value on the server's disk. */
export interface SecretNames {
  names: string[]
}

/** What `POST /api/v1/secrets` answers. Echoes the name, never the value. */
export interface SecretPutResult {
  name: string
  has_key: boolean
}

export interface Config {
  version: number
  assistant: AssistantConfig
  session: SessionConfig
  web: { host: string; port: number; auth_required: boolean }
  devices: Device[]
  audio: {
    capture: Capture
    voice: Voice
    playback: Playback
    aec: { enabled: boolean; filter_len: number }
  }
  providers: {
    catalog: Record<string, Provider>
    asr: { chain: string[] }
    tts: { chain: string[] }
    llm: { chain: string[] }
  }
  tools: {
    expose: string
    whitelist: string[]
    mcp_servers: { name: string; transport: string; url: string }[]
    search?: SearchConfig
    ha?: { base_url?: string; token_env?: string; has_token?: boolean }
  }
}

/** Legacy five-step shape; kept for `GET /api/v1/setup` and its existing callers. */
export interface SetupStep {
  id: string
  title: string
  done: boolean
  detail: Record<string, unknown>
}

/** The legacy install-wizard state: computed on the server from files and keys. */
export interface SetupState {
  next_step: string
  complete: boolean
  steps: SetupStep[]
  config_path: string
  config_exists: boolean
  audio_plane: boolean
  device: { id: string; host: string; room: string; wake_words: string[] } | null
  keywords_file: string
  keywords_exists: boolean
  models_command: string
}

/* — plugs + eight-step wizard (POST/GET /api/v1/setup/step) ————————————— */

export type SetupStepStatus = 'pending' | 'running' | 'done' | 'error' | 'skipped'
export type SetupParamType = 'string' | 'int' | 'bool' | 'list' | 'secret' | 'choice'

/** One row of `GET /api/v1/plugs` (`class` is the list summary's own key). */
export interface SetupPlugSummary {
  /** Null only for a broken user manifest, which is listed as data, not a 500. */
  id: string | null
  name: string | null
  version: string | null
  trust: string | null
  class: string | null
  adapter: string | null
  transport: string | null
  source: string
  available: boolean
  error: string | null
  devices: string[]
}

/** A manifest form field (`rui-device/v0` `params[]`). */
export interface SetupParamSpec {
  key: string
  label: string
  type: SetupParamType | string
  required: boolean
  default?: string | number | boolean | string[] | null
  placeholder: string
  help: string
  max?: number | null
  options: string[]
}

export interface SetupDeviceSide {
  artifact: string
  name: string
  source: string
  version_min: string
  install_steps: string[]
  rollback: string
}

export interface SetupRuntimeSpec {
  mode?: string | null
  entrypoint?: string
  image?: string
  listen_port?: number
  token_env?: string
}

export interface SetupDeviceManifest {
  schema?: string
  id: string
  name: string
  vendor: string
  /** Pydantic dumps the aliased `class` field as `device_class` on `/plugs/{id}`. */
  class?: string
  device_class?: string
  transport: string
  protocol?: { kind?: string; version?: string }
  capabilities: string[]
  device_side?: SetupDeviceSide | null
  runtime?: SetupRuntimeSpec | null
  params: SetupParamSpec[]
  compatibility?: {
    rui_min?: string
    tested_with?: string[]
    notes?: string
  } | null
  acceptance?: {
    verified?: boolean
    last_verified?: string
    evidence?: string
  } | null
}

export interface SetupResolvedDevice {
  id: string
  adapter: string
  transport: string
  runtime_mode: string | null
  listen_port: number | null
  token_env: string
}

export interface SetupPlugDetail {
  manifest: {
    id: string
    name: string
    version: string
    trust: string
    devices: SetupDeviceManifest[]
    [key: string]: unknown
  }
  devices: SetupDeviceManifest[]
  available: boolean
  error: string | null
  resolved: { source: string; devices: SetupResolvedDevice[] }
}

/** One of the eight server-driven steps; `actions` is the server's allowlist. */
export interface SetupStepState {
  id: string
  status: SetupStepStatus
  evidence: Record<string, unknown>
  detail: Record<string, unknown>
  actions: string[]
}

export interface SetupStepResponse {
  plug: SetupPlugSummary
  steps: SetupStepState[]
  next_step: string | null
  complete: boolean
  verified: boolean
  blocked: { code: string; message: string } | null
  plane_error: string
}

/** The request body `POST /api/v1/setup/step` accepts. */
export interface SetupStepPayload {
  plug?: string
  step: string
  action: string
  values?: Record<string, unknown>
}

/* — keys step provider catalog (GET /api/v1/setup/providers) ————————————— */

/** One write-only credential slot; `name` is the env var, never its value. */
export interface SetupProviderField {
  name: string
  role: string
  label: string
  label_i18n?: LocalizedText | string
  hint?: string
  hint_i18n?: LocalizedText | string
  docs_url?: string
  secret?: boolean
  required?: boolean
  configured?: boolean
}

/** A selectable TTS voice; plain strings and `{id,name,name_i18n}` are both accepted. */
export interface SetupProviderVoice {
  value?: string
  id?: string
  name?: string
  label?: LocalizedText | string
  name_i18n?: LocalizedText | string
  language?: string
  default?: boolean
}

/** One provider in one stage's chain; carries presence facts, never values. */
export interface SetupProviderEntry {
  id?: string
  name: string
  stage: string
  kind?: string
  display_name: string
  display_name_en?: string
  docs_url?: string
  hint?: string
  hint_i18n?: LocalizedText | string
  configured?: boolean
  missing?: string[]
  /** The active chain entry: a boolean, or the entry's name/id. */
  selected?: boolean | string
  /** Whether this provider kind can actually build this stage. */
  available?: boolean
  required?: boolean
  /** Chain order for this stage; the first entry is the default. */
  chain?: string[]
  fields?: SetupProviderField[]
  voices?: (string | SetupProviderVoice)[]
  default_voice?: string
}

/** Per-use selection facts (`stage_state`) when the backend provides them. */
export interface SetupStageState {
  selected?: string
  chain?: string[]
  providers?: string[]
  required?: boolean
  satisfied?: boolean
  missing?: string[]
}

/** The keys step's render model: providers grouped by use, values never included. */
export interface SetupProvidersDoc {
  providers: SetupProviderEntry[]
  /** Stage → ordered provider ids (`llm:deepseek_llm`) from the active chain. */
  stages?: Record<string, string[]>
  /** Optional stage-level selection map when `providers[].selected` is omitted. */
  selected?: Record<string, string> | string
  /** Current primary, full chain and offered providers per use. */
  stage_state?: Record<string, SetupStageState>
}

/** A new-style setup/plug error: `detail` is `{code, message, details}`. */
export interface ApiErrorObject {
  code: string
  message: string
  details: Record<string, unknown>
}

/** Read the object shape new setup endpoints put in FastAPI's `detail`. */
export function apiErrorObject(exc: unknown): ApiErrorObject | null {
  if (!(exc instanceof ApiError)) return null
  const detail = exc.detail
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return null
  const raw = detail as Record<string, unknown>
  if (typeof raw.code !== 'string') return null
  const details =
    raw.details && typeof raw.details === 'object' && !Array.isArray(raw.details)
      ? (raw.details as Record<string, unknown>)
      : {}
  return {
    code: raw.code,
    message: typeof raw.message === 'string' ? raw.message : '',
    details,
  }
}

export interface PhraseKey {
  key: string
  default: string
  text: string
  example: string
  slots: string[]
  overridden: boolean
  group: string
  advanced: boolean
}

/** The console's view of the phrasebook: live templates + the override file state. */
export interface PhraseDoc {
  effect: string
  overrides: Record<string, string>
  keys: PhraseKey[]
  last_errors: { key?: string; missing?: string[] }[]
  file: string
  file_exists: boolean
  file_error: string
  file_overrides: Record<string, string>
  errors: Record<string, string[]>
  unused: string[]
}

/** One device as the console edits it (the fields the form owns, plus the rest). */
export interface DeviceDoc {
  id: string
  adapter: string
  host: string
  room: string
  enabled: boolean
  wake_words: string[]
  volume: number
  listen_port: number | null
  /** Non-secret, plug-declared values (never a `secret` param). */
  params?: Record<string, unknown>
  [key: string]: unknown
}

export interface DeviceDetail {
  device: DeviceDoc
  live: {
    model?: string
    transport?: string
    status?: string
    connected?: boolean | null
    last_seen?: number | null
    latency_ms?: number | null
    capabilities?: string[]
  }
  /** Resolved plug id; may be absent against an older control plane. */
  plug_id?: string | null
  /** Public params (secrets removed); mirrors `device.params`. */
  params?: Record<string, unknown>
  /** Last audio-plane restart outcome; empty when it came back. */
  plane_error?: string
}

/** One row of `POST /api/v1/devices/{id}/test` (evidence only, no config write). */
export interface DeviceTestCheck {
  name: string
  ok: boolean
  detail: Record<string, unknown>
}

export interface DeviceTestResult {
  ok: boolean
  checks: DeviceTestCheck[]
  error: string
}

/** GET /api/v1/devices/{id}/connect: what the open-xiaoai client needs. */
export interface DeviceConnectInfo {
  device_id: string
  room: string
  host: string
  /** Full client URL (may already carry ?token=...). */
  ws_url: string
  /** Sensitive; write-only in the sense that this page never stores it. */
  token: string
  token_env: string
  configured: boolean
  allow_no_token: boolean
  hint: string
}

/** HA over MCP: the endpoint, whether it answers, and how much it lists. */
export interface McpServerStatus {
  name: string
  transport: 'http' | 'stdio' | string
  url: string
  command: string[]
  /** Present once the backend schema carries per-server env (stdio only). */
  env?: Record<string, string>
  enabled: boolean
  connected: boolean
  tools: number
  tool_names?: string[]
  latency_ms: number | null
  error: string
  last_seen: string | null
}

export interface McpGenericStatus {
  enabled: number
  connected: number
  tools: number
  stdio_enabled: boolean
}

export interface McpState {
  ha: {
    base_url: string
    token_env: string
    has_token: boolean
    wired: boolean
    connected: boolean
    latency_ms: number | null
    tools: number
    entities: number
    error: string
  }
  /** Configured generic servers, in config order, merged with live status. */
  servers: McpServerStatus[]
  generic_wired: boolean
  /** Local command execution gate (`tools.mcp_allow_stdio`), off by default. */
  stdio_enabled?: boolean
  generic?: McpGenericStatus
}

/** What `PUT /api/v1/mcp` accepts for one generic server. */
export interface McpServerPayload {
  name: string
  transport: 'http' | 'stdio'
  url?: string
  command?: string[]
  env?: Record<string, string>
  enabled: boolean
}

/** What `POST /api/v1/mcp/test` accepts: HA `{base_url}` or one generic server. */
export type McpTestPayload =
  | { base_url: string }
  | { name: string; transport: 'http' | 'stdio'; url?: string; command?: string[]; enabled?: boolean }

export interface McpProbe {
  ok: boolean
  /** False when the stdio gate refused to even try (no process was spawned). */
  attempted?: boolean
  connected?: boolean
  transport?: string
  name?: string
  latency_ms: number | null
  tools: number
  tool_names?: string[]
  /** HA probes report entities; generic probes do not. */
  entities?: number
  error: string
}

/* — config catalog (docs/config-catalog-contract.md) ————————————————————— */

/** Catalog copy is bilingual on the wire: `{zh, en}` (or a plain string). */
export interface LocalizedText {
  zh?: string
  en?: string
}

export type CatalogKind = 'text' | 'textarea' | 'number' | 'bool' | 'select' | 'list' | 'secret'
export type CatalogEffect = 'save' | 'restart'

export interface CatalogTier {
  id: number | string
  title: LocalizedText | string
  hint?: LocalizedText | string
}

export interface CatalogGroup {
  id: string
  title: LocalizedText | string
  tier: number
  order: number
  effect?: CatalogEffect
  hint?: LocalizedText | string
}

export interface CatalogOption {
  value: string | number
  label?: LocalizedText | string
}

export interface CatalogField {
  path: string
  group: string
  tier: number
  kind: CatalogKind
  label: LocalizedText | string
  /** What changing this achieves / when to use it — never a restatement of the name. */
  hint?: LocalizedText | string
  effect: CatalogEffect
  unit?: LocalizedText | string | null
  min?: number | null
  max?: number | null
  /** Options may be plain strings, numbers (bit depth) or `{value,label}` objects. */
  options?: (string | number | CatalogOption)[] | null
  advanced?: boolean
  writable?: boolean
  /** True means an empty value is allowed (sent as null). */
  nullable?: boolean
  /** Present on TTS voice fields: the pair the audition endpoint needs. */
  audition?: { stage: string; provider: string }
}

/** Telegram + WeChat channel gateway (GET/PUT /api/v1/channels). */
export type ChannelReplyMode = 'text' | 'voice' | 'both'

export interface TelegramChannelState {
  enabled: boolean
  allowed_chat_ids: string[]
  reply_mode: ChannelReplyMode
  /** Computed from RUI_TELEGRAM_TOKEN; the token itself is never returned. */
  token_configured: boolean
}

export interface WeixinChannelState {
  enabled: boolean
  allowed_user_ids: string[]
  reply_mode: ChannelReplyMode
  data_dir: string
  logged_in: boolean
  account_id: string
  user_id: string
}

export interface ChannelGatewayStatus {
  running: boolean
  pid: number | null
  started_at: number | null
  uptime_s: number
  last_error: string
  log_path: string
  managed: boolean
  enabled: boolean
}

export interface ConfigRecovery {
  active: boolean
  mode: '' | 'last-good' | 'defaults' | string
  bad_path: string
  last_good_path: string
  last_good_exists: boolean
  at: number
  note: string
  config_path: string
}

export interface ChannelsGateway {
  command: string
  config_note: string
  config_path?: string
  status?: ChannelGatewayStatus | null
}

/** Result of the write-only Telegram token check (getMe); the token never comes back. */
export interface TelegramTokenResult {
  ok: boolean
  username: string
  token_configured: boolean
}

export type WeixinLoginStatus = 'idle' | 'starting' | 'waiting' | 'confirmed' | 'error'

/** QR login state; qr_svg is a data: URI and may be empty when segno is unavailable. */
export interface WeixinLoginState {
  status: WeixinLoginStatus
  qr_url: string
  qr_svg: string
  error: string
  account_id: string
}

export interface ChannelsState {
  telegram: TelegramChannelState
  weixin: WeixinChannelState
  gateway: ChannelsGateway
}

/** What PUT /api/v1/channels accepts. Omitted channels keep their stored values. */
export interface ChannelsPatch {
  telegram?: {
    enabled: boolean
    allowed_chat_ids: string[]
    reply_mode: ChannelReplyMode
  }
  weixin?: {
    enabled: boolean
    allowed_user_ids: string[]
    reply_mode: ChannelReplyMode
  }
}

/** Volcano account/usage summary for the diagnostics page. */
export interface BillingQuota {
  available_balance: string | null
  cash_balance?: string | null
  credit_limit?: string | null
  currency: string
  tts_chars_today: number | null
  tts_chars_7d: number | null
  asr_seconds_today?: number | null
  asr_seconds_7d?: number | null
  packs: { name: string; remaining?: unknown }[]
  updated_at: string
  errors: string[]
}

export interface CatalogDoc {
  tiers: CatalogTier[]
  groups: CatalogGroup[]
  fields: CatalogField[]
  /** Dotted path → current value. Secrets never appear here. */
  values: Record<string, unknown>
  /** Secret slots the catalog knows about; `has_value` is the only fact exposed. */
  secrets?: Record<string, { has_value: boolean; used_by?: string[]; hint?: LocalizedText | string }>
  restart_command?: string
  writable_root?: string[]
}

export interface SaveValuesResult {
  config?: unknown
  applied?: string[]
  restart_required?: boolean
  restart_fields?: string[]
  /** Non-fatal notes from the server (e.g. automatic path updates). */
  warnings?: string[]
}

/* — music credentials (write-only; the panel never receives the values) ————— */

export interface MusicCredentialsPatch {
  url?: string
  user?: string
  password?: string
}

/** Presence only: the backend never returns url/user/password values. */
export interface MusicCredentialsState {
  has_credentials: boolean
  has_url: boolean
  has_user: boolean
  has_password: boolean
}

export interface MusicTestResult {
  ok: boolean
  latency_ms: number | null
  error: string
}

export const api = {
  health: () =>
    request<{
      status: string
      version: string
      config_version: number
      uptime_s: number
      devices: number
      device_errors: number
    }>('/health'),
  /** Any GET by path - panel blocks know their sources, the client need not. */
  json: <T = unknown>(path: string) => request<T>(path),
  /** A panel button press: the spec carries the method, href and body. */
  /** A POST by path - the memory panel's small CRUD. */
  post: (path: string, body: unknown) =>
    request<unknown>(path, { method: 'POST', body: JSON.stringify(body) }),
  action: (method: string, path: string, body: unknown) =>
    request<unknown>(path, { method, body: JSON.stringify(body) }),
  panels: () => request<{ revision: string; panels: PanelSpec[] }>('/api/v1/panels'),
  doctor: () => request<DoctorReport>('/api/v1/doctor'),
  audio: () => request<AudioStatus>('/api/v1/audio'),
  config: () => request<Config>('/api/v1/config'),
  /** Merge a patch into `assistant` / `session`; resolves with the full redacted config. */
  saveConfig: (patch: ConfigPatch) =>
    request<Config>('/api/v1/config', { method: 'PUT', body: JSON.stringify(patch) }),
  devices: () => request<Device[]>('/api/v1/devices'),
  tools: () => request<{ count: number; names: string[] }>('/api/v1/tools'),
  /** Names that hold a value on the server. There is no read-back endpoint, by design. */
  secrets: () => request<SecretNames>('/api/v1/secrets'),
  /**
   * Store one key, or delete it by sending an empty value.
   *
   * The value only ever travels one way. Callers must clear their own input afterwards and
   * must not keep it in storage, a store, or a log line.
   */
  putSecret: (name: string, value: string) =>
    request<SecretPutResult>('/api/v1/secrets', { method: 'POST', body: JSON.stringify({ name, value }) }),
  traces: () => request<{ limit: number; turns: unknown[] }>('/api/v1/traces'),
  /** The gate the wake model really uses, parsed from the keywords file. */
  threshold: () => request<KeywordMeta>('/api/v1/voice/threshold'),
  /** Run one text turn as if it had been spoken (the console's try-it box). */
  say: (text: string) =>
    request<{ reply: string; error: string; tools: unknown[]; stages: Record<string, unknown>; audio_bytes: number }>(
      '/api/v1/debug/say',
      { method: 'POST', body: JSON.stringify({ text }) },
    ),
  stopAudio: () => request<{ playing: string | null }>('/api/v1/debug/play', { method: 'POST', body: JSON.stringify({ stop: true }) }),
  deviceVolume: () => request<{ volume: number; range: string }>('/api/v1/device/volume'),
  /**
   * Write music credentials. Absent keys keep the stored value; an empty string
   * deletes that field. Values are write-only and never read back.
   */
  saveMusicCredentials: (patch: MusicCredentialsPatch, href = '/api/v1/music/credentials') =>
    request<MusicCredentialsState>(href, { method: 'PUT', body: JSON.stringify(patch) }),
  /** Ping the music source; with drafts it tests those unsaved values first. */
  testMusic: (patch: MusicCredentialsPatch = {}, href = '/api/v1/music/test') =>
    request<MusicTestResult>(href, { method: 'POST', body: JSON.stringify(patch) }),
  /** P5: device admin (edit/delete use the id; probe is the wizard's SSH check). */
  deviceDetail: (id: string) => request<DeviceDetail>(`/api/v1/devices/${encodeURIComponent(id)}`),
  /** Connection parameters for the speaker client; the token is sensitive. */
  deviceConnect: (id: string) =>
    request<DeviceConnectInfo>(`/api/v1/devices/${encodeURIComponent(id)}/connect`),
  updateDevice: (id: string, patch: Record<string, unknown>) =>
    request<DeviceDetail>(`/api/v1/devices/${encodeURIComponent(id)}`, {
      method: 'PUT', body: JSON.stringify(patch),
    }),
  /** Evidence-only connection test; never writes config or returns the token. */
  deviceTest: (id: string) =>
    request<DeviceTestResult>(`/api/v1/devices/${encodeURIComponent(id)}/test`, {
      method: 'POST',
    }),
  deleteDevice: (id: string) =>
    request<{ removed: string; devices: string[]; plane_error?: string }>(
      `/api/v1/devices/${encodeURIComponent(id)}`, { method: 'DELETE' },
    ),
  probeDevice: (host: string) =>
    request<{ host: string; usable: boolean; hint: string; ssh: { port_open: boolean; banner: boolean; ms?: number; error?: string } }>(
      '/api/v1/devices/probe', { method: 'POST', body: JSON.stringify({ host }) }),
  /** P5: HA over MCP (write endpoint/token, test a throwaway connection). */
  mcp: () => request<McpState>('/api/v1/mcp'),
  saveMcp: (payload: { ha?: { base_url?: string; token?: string }; servers?: McpServerPayload[] }) =>
    request<McpState>('/api/v1/mcp', { method: 'PUT', body: JSON.stringify(payload) }),
  testMcp: (payload: McpTestPayload) =>
    request<McpProbe>('/api/v1/mcp/test', { method: 'POST', body: JSON.stringify(payload) }),
  /** P6: every phrase template, plus what the override file currently holds. */
  phrases: () => request<PhraseDoc>('/api/v1/phrases'),
  savePhrases: (overrides: Record<string, string>) =>
    request<PhraseDoc>('/api/v1/phrases', { method: 'PUT', body: JSON.stringify({ overrides }) }),
  resetPhrases: (keys: string[] | null) =>
    request<PhraseDoc>('/api/v1/phrases/reset', {
      method: 'POST', body: JSON.stringify(keys === null ? {} : { keys }),
    }),
  /** Install wizard (C-UI P4): what is missing, then one step at a time. */
  setup: () => request<SetupState>('/api/v1/setup'),
  setupKeys: (values: Record<string, string>) =>
    request<SetupState>('/api/v1/setup/keys', { method: 'POST', body: JSON.stringify({ values }) }),
  setupDevice: (device: { host: string; room?: string; wake_words: string[] }) =>
    request<SetupState>('/api/v1/setup/device', { method: 'POST', body: JSON.stringify(device) }),
  setupWake: (wake_words: string[]) =>
    request<SetupState>('/api/v1/setup/wake', { method: 'POST', body: JSON.stringify({ wake_words }) }),
  setupProbe: (host: string) =>
    request<{
      host: string
      usable: boolean
      hint: string
      ssh: { port_open: boolean; banner: boolean; ms?: number; error?: string }
    }>('/api/v1/setup/probe', { method: 'POST', body: JSON.stringify({ host }) }),

  /* — plug picker + eight-step wizard (server-computed evidence) ————————— */

  /** Built-in plus user plugs; a broken manifest is data with `available=false`. */
  plugs: () => request<{ plugs: SetupPlugSummary[] }>('/api/v1/plugs'),
  /** Manifest detail for the selected plug: device_side, params[], capabilities. */
  plug: (id: string) =>
    request<SetupPlugDetail>(`/api/v1/plugs/${encodeURIComponent(id)}`),
  /** `GET /api/v1/setup/step`; `plug` selects the manifest that supplies the slots. */
  setupStepState: (plug?: string) =>
    request<SetupStepResponse>(
      `/api/v1/setup/step${plug ? `?plug=${encodeURIComponent(plug)}` : ''}`,
    ),
  /**
   * Provider metadata for the keys step: one row per (stage, provider) with
   * display name, credential fields, optional TTS voices and presence badges.
   * A credential value is never part of this response.
   */
  setupProviders: () => request<SetupProvidersDoc>('/api/v1/setup/providers'),
  /**
   * Run one allowlisted step action. The body field is `values` (the backend also
   * accepts the legacy `payload` alias); `action` must come from `step.actions`.
   */
  setupStep: (payload: SetupStepPayload) =>
    request<SetupStepResponse>('/api/v1/setup/step', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  /**
   * Backend reality: there is no `/setup/test-connection`. The test step's
   * allowlisted `device.test` action returns the recomputed setup state, whose
   * `detail.checks` carries the result.
   */
  setupTestConnection: (plug: string, deviceId?: string) =>
    request<SetupStepResponse>('/api/v1/setup/step', {
      method: 'POST',
      body: JSON.stringify({
        plug,
        step: 'test',
        action: 'device.test',
        values: deviceId ? { device_id: deviceId } : {},
      }),
    }),
  /**
   * Backend reality: there is no `/setup/try`. `try.run` returns the setup state
   * (audio_bytes evidence); the reply is read from `GET /api/v1/turns?limit=1`.
   */
  setupTry: (plug: string, text: string) =>
    request<SetupStepResponse>('/api/v1/setup/step', {
      method: 'POST',
      body: JSON.stringify({ plug, step: 'verify', action: 'try.run', values: { text } }),
    }),
  /** Persist the `prepare` / `verify` manual confirmation in `data/setup.json`. */
  setupConfirm: (
    plug: string,
    step: 'prepare' | 'verify',
    confirmed: boolean,
    rollbackAck = false,
  ) =>
    request<SetupStepResponse>('/api/v1/setup/step', {
      method: 'POST',
      body: JSON.stringify({
        plug,
        step,
        action: 'manual.confirm',
        values: { confirmed, rollback_ack: rollbackAck },
      }),
    }),
  /** Read-only `connect.inspect`: returns the state with live plane evidence. */
  setupInspect: (plug: string) =>
    request<SetupStepResponse>('/api/v1/setup/step', {
      method: 'POST',
      body: JSON.stringify({ plug, step: 'connect', action: 'connect.inspect', values: {} }),
    }),
  setDeviceVolume: (value: number) =>
    request<{ volume: number }>('/api/v1/device/volume', { method: 'PUT', body: JSON.stringify({ value }) }),

  /* — config recovery (last-good / defaults) ————————————————— */

  configRecovery: () => request<ConfigRecovery>('/api/v1/config/recovery'),
  restoreConfigRecovery: (mode: 'last-good' | 'defaults') =>
    request<{ ok: boolean; message: string; restart_required: boolean }>(
      '/api/v1/config/recovery/restore', { method: 'POST', body: JSON.stringify({ mode }) },
    ),
  recoveryBadConfig: async (): Promise<Blob> => {
    const headers = new Headers()
    const t = token.get()
    if (t) headers.set('X-Auth-Token', t)
    const res = await fetch('/api/v1/config/recovery/bad', { headers })
    if (!res.ok) throw new ApiError(res.status, res.statusText, null)
    return res.blob()
  },

  /* — channel gateway (Telegram + WeChat) ————————————————— */

  /** Channel enablement, allowlists and reply modes; token values are never returned. */
  channels: () => request<ChannelsState>('/api/v1/channels'),
  saveChannels: (patch: ChannelsPatch) =>
    request<ChannelsState>('/api/v1/channels', { method: 'PUT', body: JSON.stringify(patch) }),
  /** Managed channel gateway process (supervisor); each action returns the fresh status. */
  startChannelGateway: () =>
    request<ChannelGatewayStatus>('/api/v1/channels/gateway/start', { method: 'POST' }),
  stopChannelGateway: () =>
    request<ChannelGatewayStatus>('/api/v1/channels/gateway/stop', { method: 'POST' }),
  restartChannelGateway: () =>
    request<ChannelGatewayStatus>('/api/v1/channels/gateway/restart', { method: 'POST' }),
  /** Validate and store the bot token server-side; empty clears it. */
  setTelegramToken: (token: string) =>
    request<TelegramTokenResult>('/api/v1/channels/telegram/token', {
      method: 'PUT',
      body: JSON.stringify({ token }),
    }),
  startWeixinLogin: () =>
    request<WeixinLoginState>('/api/v1/channels/weixin/login', { method: 'POST' }),
  weixinLogin: () => request<WeixinLoginState>('/api/v1/channels/weixin/login'),

  /* — config catalog (console-api, docs/config-catalog-contract.md) ————— */

  /** The UI's field directory: tiers, groups, fields, current values, restart rules. */
  catalog: () => request<CatalogDoc>('/api/v1/config/catalog'),
  /** Write dotted-path values. 422s carry the offending path in `detail[].loc`. */
  saveConfigValues: (values: Record<string, unknown>) =>
    request<SaveValuesResult>('/api/v1/config/values', {
      method: 'PUT', body: JSON.stringify({ values }),
    }),
  /** Restart only the audio plane — for audio.* fields marked `restart`. */
  restartAudio: () =>
    request<{ restarted: boolean; error: string }>('/api/v1/system/audio/restart', {
      method: 'POST', body: JSON.stringify({}),
    }),

  /* — voice audition + Volcano quota (console-voice) ————————————— */

  /** Voices a TTS provider offers, for the select and the audition button. */
  voices: () => request<{ tts: Record<string, { value: string; label: LocalizedText }[]>; asr: Record<string, unknown[]> }>('/api/v1/providers/voices'),

  /** Synthesise one line with a provider + voice and return the wav bytes.
   *  `prosody` carries unsaved speech_rate/emotion drafts so preview matches the form. */
  audition: async (
    stage: string,
    provider: string,
    voice: string,
    text?: string,
    prosody?: Record<string, unknown>,
  ): Promise<Blob> => {
    const headers = new Headers({ 'Content-Type': 'application/json' })
    const authToken = token.get()
    if (authToken) headers.set('X-Auth-Token', authToken)
    const res = await fetch(`/api/v1/providers/${stage}/${encodeURIComponent(provider)}/audition`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ voice, ...(text ? { text } : {}), ...(prosody ?? {}) }),
    })
    if (res.status === 401) window.dispatchEvent(new Event('rui-401'))
    if (!res.ok) {
      let detail: unknown = null
      const raw = await res.text()
      if (raw) {
        try {
          detail = (JSON.parse(raw) as { detail?: unknown }).detail ?? raw
        } catch {
          detail = raw
        }
      }
      throw new ApiError(res.status, res.statusText, detail)
    }
    return res.blob()
  },

  /** Volcano balance + TTS character usage + resource packs (server caches 300 s). */
  billingQuota: (force = false) =>
    request<BillingQuota>(`/api/v1/billing/quota${force ? '?force=true' : ''}`),
}
