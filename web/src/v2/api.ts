/**
 * v2 console API client (contract C-002/1, `docs/contracts/C-002-console-v2-api.md`).
 *
 * Hand-written from the contract on purpose: the v2 console must not import the
 * legacy client (`../api.ts` pulls `PanelSpec` types from the panel registry) nor
 * any legacy device endpoint. Only `/api/v2/*` is spoken here.
 *
 * Auth follows the console convention: localStorage `rui-token`, header
 * `X-Auth-Token`. A 401 means "ask the operator for a token", so it is surfaced
 * as a window event the shell listens to instead of a page-level exception.
 */

const TOKEN_KEY = 'rui-token'

export const token = {
  get(): string {
    try {
      return localStorage.getItem(TOKEN_KEY) ?? ''
    } catch {
      return ''
    }
  },
  set(value: string): void {
    try {
      localStorage.setItem(TOKEN_KEY, value)
    } catch {
      /* private mode */
    }
  },
}

/** Ask the shell to open the token dialog (used by inline 401 states). */
export function promptForToken(): void {
  window.dispatchEvent(new Event('v2:unauthorized'))
}

export type LocalizedCopy = { zh?: string; en?: string }

export interface MetaInfo {
  contract: string
  api_version: string
  rui_version: string
  compat: { min: string; max_exclusive: string }
  features: Record<string, boolean>
  capabilities: string[]
}

export interface HouseInfo {
  version: { rui: string; config: number }
  devices: { total: number; enabled: number; online: number; ready: number }
  multi_device: {
    enabled: boolean
    max_planes: number
    default_output: string | null
    room_priority: string[]
    planes_running: number
  }
  rooms: Array<{ room: string; device_ids: string[]; online: number; default_output: boolean }>
  warnings: WarningItem[]
  setup: { complete: boolean; verified: boolean; next_step: string }
}

export interface WarningItem {
  code: string
  message?: string
  hint?: string
  device_id?: string
  /** Present on `unknown_params_ignored`: the ignored legacy parameter keys. */
  params?: string[]
}

export interface StatusReason {
  code: string
  message: string
  hint: string
}

export type DeviceStatus =
  | 'ready'
  | 'unknown'
  | 'disconnected'
  | 'disabled'
  | 'unavailable'
  | 'error'

export type Transport = 'inbound' | 'outbound'

export interface AudioSummary {
  capture_rate: number
  playback_rate: number
  aec_who: string
  ack_echo_ignore_ms: number
}

export interface DeviceSummary {
  id: string
  room: string
  plugin_id: string
  model_id: string
  model: string
  vendor: string
  transport: Transport
  enabled: boolean
  ready: boolean
  status: DeviceStatus
  connected: boolean | null
  status_reason: StatusReason
  last_seen: number | null
  latency_ms: number | null
  listen_port: number | null
  capabilities: string[]
  unknown_capabilities: string[]
  audio_summary: AudioSummary
  warnings: WarningItem[]
}

export type CapabilityReason =
  | 'ok'
  | 'manifest_only'
  | 'probe_missing'
  | 'not_declared'
  | 'unknown_capability'
  | 'deprecated'

export interface CapabilityEntry {
  id: string
  declared: boolean
  probed: boolean | null
  effective: boolean
  known: boolean
  reason: CapabilityReason | string
}

export interface CapabilityAction {
  id: string
  args_schema?: unknown
  result_schema?: unknown
}

export interface CapabilityCatalogItem {
  id: string
  label: LocalizedCopy
  description: LocalizedCopy
  ui_hint: string
  actions: CapabilityAction[]
  degraded_hint: LocalizedCopy
}

export interface CapabilityCatalog {
  items: CapabilityCatalogItem[]
}

export type AudioSource =
  | 'device-override'
  | 'global-config'
  | 'device-manifest'
  | 'core-default'
  | 'unresolved'

export interface AudioLayer {
  effective: unknown
  source: AudioSource | string
  core: unknown
  manifest: unknown
  global: unknown
  global_explicit?: boolean
  device: unknown
}

export interface DeviceAudio {
  effective: Record<string, unknown>
  sources: Record<string, AudioSource | string>
  layers: Record<string, AudioLayer>
  overrides: string[]
  manifest_defaults: Record<string, unknown>
  plug_resolved: boolean
}

export interface DevicePlane {
  running: boolean
  speaker_connected: boolean | null
  speaker_rejected: number
  restarts: number
  pid: number | null
  error: string
  wake_candidates: number
  wake_admitted: number
  wake_rejected: Record<string, number>
  utterances: number
}

export interface DeviceSetup {
  configured: boolean
  last_test_ok: boolean
  last_test_at: string | null
}

/**
 * Lead errata (2026-10-25): the backend does not expose `secret_params` or
 * `requires_socket`; capabilities and the params schema are the only sources of
 * device shape. Do not re-add those fields from the contract text.
 */
export interface DeviceDetail extends Omit<DeviceSummary, 'capabilities'> {
  host: string
  wake_words: string[]
  volume_target: number
  params: Record<string, unknown>
  /** Keys kept in config that the manifest does not declare (read-only). */
  unknown_params: string[]
  capabilities: CapabilityEntry[]
  effective_capabilities: string[]
  missing_for_barge_in: string[]
  audio: DeviceAudio
  plane: DevicePlane
  setup: DeviceSetup
}

export interface DeviceList {
  items: DeviceSummary[]
  total: number
}

// — device writes (M3) ————————————————————————————————————————
// Contract §7.11–7.14. `params` / `audio` stay typed as opaque maps here: the
// config tab only edits the promoted public fields and leaves audio to M4.

export interface CreateDeviceRequest {
  id: string
  room?: string
  plugin_id: string
  model_id?: string
  host: string
  listen_port?: number | null
  wake_words?: string[]
  volume_target?: number
  enabled?: boolean
  params?: Record<string, unknown>
}

/** `PATCH /devices/{id}` — every field optional, one atomic write. */
export interface PatchDeviceRequest {
  room?: string
  host?: string
  enabled?: boolean
  wake_words?: string[]
  volume_target?: number
  listen_port?: number | null
  params?: Record<string, unknown> | null
  audio?: Record<string, unknown> | null
}

export interface Restart {
  scope: 'fleet' | 'service' | 'audio_plane' | string
  required: boolean
  auto: boolean
  status: 'ok' | 'failed' | 'stopped' | 'pending' | string
  affected_devices: string[]
  duration_ms: number | null
  error: string
  restart_fields?: string[]
}

/** `POST /devices` — 201 `{device, restart}`. */
export interface CreateDeviceResponse {
  device: DeviceDetail
  restart: Restart
}

/** `PATCH /devices/{id}` — 200 `{device, restart}`. */
export interface PatchDeviceResponse {
  device: DeviceDetail
  restart: Restart
}

/** `PATCH /devices/{id}/audio` — 200 `{audio, device, restart}`. */
export interface PatchDeviceAudioResponse {
  audio: DeviceAudio
  device: DeviceDetail
  restart: Restart
}

/** `DELETE /devices/{id}?confirm=...` — 200 `{removed, device_id}`. */
export interface DeleteDeviceResponse {
  removed: boolean
  device_id: string
}

/** `PATCH /house` — `{house, restart}`. */
export interface HousePatchResponse {
  house: HouseInfo
  restart: Restart
}

/** `PATCH /house/audio` — audio view plus the `restart` envelope. */
export interface HouseAudioPatchResponse {
  audio: Record<string, unknown>
  ack: Record<string, unknown>
  explicit: string[]
  effects: { restart: string; fields: string[] }
  restart: Restart
}

/** `POST /house/audio/restart` — 200 `{restart}`. */
export interface RestartResponse {
  restart: Restart
}

export interface ActivityItem {
  id: string
  ts: number
  type: 'turn' | 'log' | string
  level: string
  title: string
  detail?: Record<string, unknown>
  matched_by: 'device_id' | 'literal' | string
}

export interface ActivityPage {
  items: ActivityItem[]
  total: number
  truncated: boolean
}

export interface ConnectionInfo {
  device_id: string
  transport: Transport
  configured: boolean
  token_configured: boolean
  token_managed_by_adapter: boolean
  token_env: string | null
  allow_no_token: boolean
  listen_port: number | null
  ws_url_masked: string | null
  host_source: 'request' | 'server' | string
  hint: LocalizedCopy | string | null
  host_hint: LocalizedCopy | null
}

/** `POST /devices/{id}/connection/reveal` — the only response carrying a token. */
export interface RevealedConnection {
  device_id: string
  ws_url: string
  token: string
  token_env: string | null
  configured: boolean
  allow_no_token: boolean
  host_source: 'request' | 'server' | string
  hint: LocalizedCopy | string | null
}

/** `POST /devices/{id}/token/rotate` — shown once, never cached by the UI. */
export interface RotatedToken {
  token: string
  ws_url: string
  token_env: string
}

export type TestCheckStatus = 'ok' | 'fail' | 'not_applicable' | 'skipped'

export interface TestCheck {
  id: string
  status: TestCheckStatus | string
  code: string
  detail?: Record<string, unknown>
  hint: string
}

export interface TestFailure {
  code: string
  message: string
  hint: string
}

/** `POST /devices/{id}/test` — evidence only; never plays or changes state. */
export interface DeviceTestResult {
  ok: boolean
  ready: boolean
  waited_ms: number
  checked_at: number
  transport: Transport | string
  checks: TestCheck[]
  errors: TestFailure[]
}

export type DeviceActionId =
  | 'volume.read'
  | 'volume.set'
  | 'led.set'
  | 'audio.say'
  | 'audio.play'
  | 'audio.stop'

export interface ActionDeviceStatus {
  ready: boolean
  connected: boolean
}

/** `POST /devices/{id}/actions` — generic capability-gated action. */
export interface DeviceActionResponse {
  action: DeviceActionId | string
  ok: boolean
  code: string
  result: Record<string, unknown>
  device_status: ActionDeviceStatus
  turn?: { turn_id: string; device_id: string }
}

export type ActivityKind = 'all' | 'turn' | 'log'

export interface ActivityQuery {
  limit?: number
  type?: ActivityKind
}

export interface ParamSpec {
  key: string
  label: string
  type: string
  required: boolean
  default?: unknown
  max?: number
  min?: number
}

export interface PluginModel {
  model_id: string
  model: string
  name: string
  vendor: string
  class: string
  transport: Transport
  capabilities: string[]
  params: ParamSpec[]
  audio_defaults?: Record<string, unknown>
}

export interface PluginInfo {
  id: string
  version: string
  name: string
  vendor: string
  class: string
  trust: string
  transport: Transport
  capabilities: string[]
  available: boolean
  error: string | null
  devices: PluginModel[]
}

export interface PluginList {
  items: PluginInfo[]
  total: number
}

export interface ErrorField {
  path: string
  code?: string
  message?: string
}

/**
 * One failed `/api/v2` call. `code` is the contract's stable string, `hint` the
 * operator-facing technical suggestion; both are kept verbatim so the UI can
 * translate known codes without inventing copy.
 */
export class V2ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly hint: string
  readonly details: unknown
  readonly retryable: boolean
  readonly requestId: string | null
  /** Parsed `Retry-After` response header, in seconds (429 cooling off). */
  readonly retryAfterS: number | null

  constructor(init: {
    status: number
    code: string
    message: string
    hint?: string
    details?: unknown
    retryable?: boolean
    requestId?: string | null
    retryAfterS?: number | null
  }) {
    super(init.message)
    this.name = 'V2ApiError'
    this.status = init.status
    this.code = init.code
    this.hint = init.hint ?? ''
    this.details = init.details
    this.retryable = init.retryable ?? false
    this.requestId = init.requestId ?? null
    this.retryAfterS = init.retryAfterS ?? null
  }

  get fields(): ErrorField[] {
    const details = this.details
    if (!details || typeof details !== 'object') return []
    const raw = (details as { fields?: unknown }).fields
    if (!Array.isArray(raw)) return []
    const out: ErrorField[] = []
    for (const entry of raw) {
      if (!entry || typeof entry !== 'object') continue
      const field = entry as { path?: unknown; code?: unknown; message?: unknown }
      const path = typeof field.path === 'string' ? field.path : ''
      if (!path) continue
      out.push({
        path,
        code: typeof field.code === 'string' ? field.code : undefined,
        message: typeof field.message === 'string' ? field.message : undefined,
      })
    }
    return out
  }
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function statusCode(status: number): string {
  switch (status) {
    case 400:
      return 'bad_request'
    case 401:
      return 'unauthorized'
    case 403:
      return 'path_not_allowed'
    case 404:
      return 'not_found'
    case 409:
      return 'conflict'
    case 422:
      return 'invalid_values'
    case 429:
      return 'rate_limited'
    case 502:
      return 'action_failed'
    case 503:
      return 'unavailable'
    default:
      return status >= 500 ? 'internal' : 'http_error'
  }
}

function summarizeBody(body: unknown): string {
  if (typeof body === 'string') return body
  if (body && typeof body === 'object') {
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (!item || typeof item !== 'object') return String(item)
          const row = item as { loc?: unknown; msg?: unknown }
          const loc = Array.isArray(row.loc) ? row.loc.map(String).slice(1).join('.') : ''
          const msg = asString(row.msg)
          return loc ? `${loc}: ${msg}` : msg
        })
        .filter(Boolean)
        .join('; ')
    }
    try {
      return JSON.stringify(body)
    } catch {
      return ''
    }
  }
  return ''
}

/** `Retry-After` is either delta-seconds or an HTTP date. */
function parseRetryAfterS(value: string | null): number | null {
  if (!value) return null
  const raw = value.trim()
  if (!raw) return null
  const seconds = Number(raw)
  if (Number.isFinite(seconds)) return Math.max(0, Math.ceil(seconds))
  const at = Date.parse(raw)
  if (Number.isNaN(at)) return null
  return Math.max(0, Math.ceil((at - Date.now()) / 1000))
}

function errorFromResponse(
  status: number,
  body: unknown,
  requestId: string | null,
  retryAfterHeader: string | null = null,
): V2ApiError {
  const envelope =
    body && typeof body === 'object' && (body as { error?: unknown }).error
      ? ((body as { error: Record<string, unknown> }).error as Record<string, unknown>)
      : null
  const code = asString(envelope?.code) || statusCode(status)
  const message = asString(envelope?.message) || summarizeBody(body) || `HTTP ${status}`
  return new V2ApiError({
    status,
    code,
    message,
    hint: asString(envelope?.hint),
    details: envelope?.details,
    retryable: Boolean(envelope?.retryable),
    requestId: asString(body && typeof body === 'object' ? (body as { request_id?: unknown }).request_id : '') || requestId,
    retryAfterS: parseRetryAfterS(retryAfterHeader),
  })
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const current = token.get()
  if (current) headers.set('X-Auth-Token', current)
  if (init.body) headers.set('Content-Type', 'application/json')

  const res = await fetch(path, { ...init, headers })
  if (res.status === 401) {
    // The shell owns the token dialog; pages only render their own error state.
    window.dispatchEvent(new Event('v2:unauthorized'))
  }

  const raw = await res.text()
  let body: unknown = null
  if (raw) {
    try {
      body = JSON.parse(raw)
    } catch {
      body = raw
    }
  }
  if (!res.ok) {
    throw errorFromResponse(
      res.status,
      body,
      res.headers.get('X-Request-ID'),
      res.headers.get('Retry-After'),
    )
  }
  return body as T
}

function encodeId(id: string): string {
  return encodeURIComponent(id)
}

export const api = {
  meta: (): Promise<MetaInfo> => request<MetaInfo>('/api/v2/meta'),
  house: (): Promise<HouseInfo> => request<HouseInfo>('/api/v2/house'),
  plugins: (): Promise<PluginList> => request<PluginList>('/api/v2/plugins'),
  capabilities: (): Promise<CapabilityCatalog> =>
    request<CapabilityCatalog>('/api/v2/capabilities'),
  devices: (): Promise<DeviceList> => request<DeviceList>('/api/v2/devices'),
  device: (id: string): Promise<DeviceDetail> =>
    request<DeviceDetail>(`/api/v2/devices/${encodeId(id)}`),
  /** `POST /devices` — 201 `{device, restart}`; 409 `device_exists` is final. */
  createDevice: (body: CreateDeviceRequest): Promise<CreateDeviceResponse> =>
    request<CreateDeviceResponse>('/api/v2/devices', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  /** `PATCH /devices/{id}` — 200 `{device, restart}`; never carries a token. */
  patchDevice: (id: string, body: PatchDeviceRequest): Promise<PatchDeviceResponse> =>
    request<PatchDeviceResponse>(`/api/v2/devices/${encodeId(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  /** `DELETE /devices/{id}?confirm=<id>` — 200 `{removed, device_id}`. */
  deleteDevice: (id: string, confirm: string): Promise<DeleteDeviceResponse> =>
    request<DeleteDeviceResponse>(
      `/api/v2/devices/${encodeId(id)}?confirm=${encodeURIComponent(confirm)}`,
      { method: 'DELETE' },
    ),
  deviceAudio: (id: string): Promise<DeviceAudio> =>
    request<DeviceAudio>(`/api/v2/devices/${encodeId(id)}/audio`),
  deviceActivity: (id: string, query: ActivityQuery = {}): Promise<ActivityPage> => {
    const params = new URLSearchParams()
    params.set('limit', String(query.limit ?? 50))
    if (query.type) params.set('type', query.type)
    return request<ActivityPage>(
      `/api/v2/devices/${encodeId(id)}/activity?${params.toString()}`,
    )
  },
  deviceConnection: (id: string): Promise<ConnectionInfo> =>
    request<ConnectionInfo>(`/api/v2/devices/${encodeId(id)}/connection`),
  deviceTest: (id: string): Promise<DeviceTestResult> =>
    request<DeviceTestResult>(`/api/v2/devices/${encodeId(id)}/test`, { method: 'POST' }),
  deviceAction: (
    id: string,
    action: DeviceActionId,
    args: Record<string, unknown> = {},
  ): Promise<DeviceActionResponse> =>
    request<DeviceActionResponse>(`/api/v2/devices/${encodeId(id)}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action, args }),
    }),
  revealConnection: (id: string): Promise<RevealedConnection> =>
    request<RevealedConnection>(`/api/v2/devices/${encodeId(id)}/connection/reveal`, {
      method: 'POST',
    }),
  rotateToken: (id: string): Promise<RotatedToken> =>
    request<RotatedToken>(`/api/v2/devices/${encodeId(id)}/token/rotate`, { method: 'POST' }),
}
