const TOKEN_KEY = 'house-ear-token'

export const token = {
  get: () => localStorage.getItem(TOKEN_KEY) ?? '',
  set: (v: string) => localStorage.setItem(TOKEN_KEY, v),
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const t = token.get()
  if (t) headers.set('X-Auth-Token', t)
  if (init.body) headers.set('Content-Type', 'application/json')
  const res = await fetch(path, { ...init, headers })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
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
  rtf: number
  cpu_share: number
  device_capacity: number
  measured_at: string
  checks: Check[]
}

export interface Device {
  id: string
  adapter: string
  model: string
  room: string
  host: string
  capabilities: string[]
  missing_for_barge_in: string[]
}

export interface Provider {
  kind: string
  base_url: string
  model: string
  api_key_env: string
  timeout_s: number
  fallback: string
  has_key?: boolean
}

export interface Config {
  version: number
  web: { host: string; port: number; auth_token: string }
  devices: Device[]
  audio: {
    sample_rate: number
    frame_ms: number
    kws_threshold: number
    vad_min_speech_ms: number
    vad_end_silence_ms: number
    barge_in: boolean
    aec: { enabled: boolean; filter_len: number; channels: number; mic_shift: number; ref_shift: number }
  }
  providers: { asr: Provider; tts: Provider; llm: Provider }
  tools: { expose: string; whitelist: string[]; mcp_servers: { name: string; transport: string; url: string }[] }
}

export const api = {
  health: () => request<{ status: string; version: string; uptime_s: number }>('/health'),
  doctor: () => request<DoctorReport>('/api/v1/doctor'),
  config: () => request<Config>('/api/v1/config'),
  devices: () => request<Device[]>('/api/v1/devices'),
  tools: () => request<{ count: number; names: string[] }>('/api/v1/tools'),
}
