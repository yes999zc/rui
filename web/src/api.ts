import type { PanelSpec } from './panels'

const TOKEN_KEY = 'house-ear-token'

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
    window.dispatchEvent(new Event('house-ear-401'))
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

export interface Device {
  id: string
  adapter: string
  token_env?: string
  model: string
  room: string
  host: string
  transport: string
  status: 'ready' | 'unavailable'
  error?: string
  capabilities: string[]
  missing_for_barge_in: string[]
  has_device_token?: boolean
}

export interface Provider {
  kind: string
  base_url: string
  model: string
  api_key_env: string
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

export interface SetupStep {
  id: string
  title: string
  done: boolean
  detail: Record<string, unknown>
}

/** The install wizard's state: computed on the server from files and keys. */
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
  [key: string]: unknown
}

export interface DeviceDetail {
  device: DeviceDoc
  live: { model?: string; transport?: string; status?: string; capabilities?: string[] }
}

/** HA over MCP: the endpoint, whether it answers, and how much it lists. */
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
  servers: { name: string; transport: string; url: string; enabled: boolean }[]
  generic_wired: boolean
}

export interface McpProbe {
  ok: boolean
  connected: boolean
  latency_ms: number | null
  tools: number
  entities: number
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
}

export interface CatalogDoc {
  tiers: CatalogTier[]
  groups: CatalogGroup[]
  fields: CatalogField[]
  /** Dotted path → current value. Secrets never appear here. */
  values: Record<string, unknown>
  /** Secret slots the catalog knows about; `has_value` is the only fact exposed. */
  secrets?: Record<string, { has_value: boolean }>
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
  /** P5: device admin (edit/delete use the id; probe is the wizard's SSH check). */
  deviceDetail: (id: string) => request<DeviceDetail>(`/api/v1/devices/${encodeURIComponent(id)}`),
  updateDevice: (id: string, patch: Record<string, unknown>) =>
    request<DeviceDetail>(`/api/v1/devices/${encodeURIComponent(id)}`, {
      method: 'PUT', body: JSON.stringify(patch),
    }),
  deleteDevice: (id: string) =>
    request<{ removed: string; devices: string[] }>(`/api/v1/devices/${encodeURIComponent(id)}`,
      { method: 'DELETE' }),
  probeDevice: (host: string) =>
    request<{ host: string; usable: boolean; hint: string; ssh: { port_open: boolean; banner: boolean; ms?: number; error?: string } }>(
      '/api/v1/devices/probe', { method: 'POST', body: JSON.stringify({ host }) }),
  /** P5: HA over MCP (write endpoint/token, test a throwaway connection). */
  mcp: () => request<McpState>('/api/v1/mcp'),
  saveMcp: (payload: { ha?: { base_url?: string; token?: string }; servers?: unknown[] }) =>
    request<McpState>('/api/v1/mcp', { method: 'PUT', body: JSON.stringify(payload) }),
  testMcp: (base_url: string) =>
    request<McpProbe>('/api/v1/mcp/test', { method: 'POST', body: JSON.stringify({ base_url }) }),
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
  setDeviceVolume: (value: number) =>
    request<{ volume: number }>('/api/v1/device/volume', { method: 'PUT', body: JSON.stringify({ value }) }),

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
}
