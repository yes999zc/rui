/**
 * Global audio defaults surface (contract C-002/1 §7.4–7.5).
 *
 * `GET /house/audio` returns the global `audio` block, the one writable ack
 * leaf, the explicit paths and the restart-effect list. This module is the UI
 * directory for that block: control kind and bounds, group order, and the
 * field-level diff helpers. It deliberately contains no model names.
 *
 * The catalog mirrors `src/rui/config/schema.py` (`CaptureConfig`,
 * `VoiceConfig`, `PlaybackConfig`, `AecConfig`, `AckConfig.echo_ignore_ms`).
 * A leaf missing from the table is still rendered from the response, but
 * read-only: the backend is the source of truth for what exists, and an
 * unknown leaf must never be silently dropped.
 *
 * M6 debt (do not grow this duplication): the 37 paths here duplicate the
 * 37-leaf directory in `audio.ts`, and `HouseAudioEditor` duplicates the draft
 * state machine in `AudioPanel`. Before M6, extract the shared leaf metadata
 * and a `useAudioDraft` composable. The two path sets are 37/37 identical; the
 * bounds were checked against `src/rui/config/schema.py` (they are not always
 * numerically equal on purpose — global `ack.echo_ignore_ms` is ≤ 3000 while
 * the per-device override is ≤ 5000). Do not add a leaf to only one table.
 */
import type { PatchHouseAudioRequest, HouseAudioInfo } from './api'
import type { MsgKey } from './i18n'

export type HouseAudioControl = 'number' | 'boolean' | 'enum' | 'text'

export interface HouseAudioEnumOption {
  value: string | number
  /** Contract literal; never translated. */
  label: string
}

export interface HouseAudioSpec {
  /** Dotted path exactly as in `explicit` / `effects.fields`. */
  path: string
  group: string
  control: HouseAudioControl
  min?: number
  max?: number
  options?: HouseAudioEnumOption[]
  /** Collapsed under the group's "advanced" disclosure. */
  advanced?: boolean
}

function leaf(
  path: string,
  group: string,
  control: HouseAudioControl,
  bounds: {
    min?: number
    max?: number
    options?: HouseAudioEnumOption[]
    advanced?: boolean
  } = {},
): HouseAudioSpec {
  return { path, group, control, ...bounds }
}

const CAPTURE: HouseAudioSpec[] = [
  leaf('audio.capture.rate', 'capture', 'number', { min: 8000, max: 96000 }),
  leaf('audio.capture.channels', 'capture', 'number', { min: 1, max: 16 }),
  leaf('audio.capture.mic_channels', 'capture', 'number', { min: 1, max: 16 }),
  leaf('audio.capture.ref_channels', 'capture', 'number', { min: 0, max: 16 }),
  leaf('audio.capture.bits', 'capture', 'enum', {
    options: [
      { value: 16, label: '16' },
      { value: 32, label: '32' },
    ],
  }),
  leaf('audio.capture.mic_shift', 'capture', 'number', { min: 0, max: 31 }),
  leaf('audio.capture.ref_shift', 'capture', 'number', { min: 0, max: 31 }),
]

const VOICE: HouseAudioSpec[] = [
  leaf('audio.voice.rate', 'voice', 'number', { min: 8000, max: 48000 }),
  leaf('audio.voice.frame_ms', 'voice', 'number', { min: 10, max: 40 }),
  leaf('audio.voice.vad_min_speech_ms', 'voice', 'number', { min: 50, max: 2000 }),
  leaf('audio.voice.vad_end_silence_ms', 'voice', 'number', { min: 100, max: 3000 }),
  leaf('audio.voice.vad_preroll_ms', 'voice', 'number', { min: 0, max: 1000 }),
  leaf('audio.voice.wake_skip_ms', 'voice', 'number', {
    min: 0,
    max: 1000,
    advanced: true,
  }),
  leaf('audio.voice.barge_in', 'voice', 'boolean'),
  leaf('audio.voice.kws_enabled', 'voice', 'boolean'),
  leaf('audio.voice.kws_model_dir', 'voice', 'text', { advanced: true }),
  leaf('audio.voice.kws_keywords_file', 'voice', 'text', { advanced: true }),
  leaf('audio.voice.kws_probe_file', 'voice', 'text', { advanced: true }),
  leaf('audio.voice.kws_chunk', 'voice', 'number', {
    min: 4,
    max: 16,
    advanced: true,
  }),
  leaf('audio.voice.kws_num_threads', 'voice', 'number', {
    min: 1,
    max: 4,
    advanced: true,
  }),
  leaf('audio.voice.vad_model_path', 'voice', 'text', { advanced: true }),
  leaf('audio.voice.wake_preroll_ms', 'voice', 'number', {
    min: 0,
    max: 1500,
    advanced: true,
  }),
  leaf('audio.voice.followup_delay_ms', 'voice', 'number', {
    min: 0,
    max: 10000,
    advanced: true,
  }),
  leaf('audio.voice.followup_replay', 'voice', 'boolean', { advanced: true }),
  leaf('audio.voice.post_playback_mute_ms', 'voice', 'number', {
    min: 0,
    max: 10000,
    advanced: true,
  }),
]

const PLAYBACK: HouseAudioSpec[] = [
  leaf('audio.playback.rate', 'playback', 'number', { min: 8000, max: 96000 }),
  leaf('audio.playback.channels', 'playback', 'number', { min: 1, max: 8 }),
  leaf('audio.playback.queue_ms', 'playback', 'number', { min: 20, max: 1000 }),
  leaf('audio.playback.fade_out_ms', 'playback', 'number', { min: 0, max: 500 }),
  leaf('audio.playback.credit_window', 'playback', 'number', {
    min: 0,
    max: 50,
    advanced: true,
  }),
  leaf('audio.playback.prebuffer_ms', 'playback', 'number', { min: 0, max: 1000 }),
  leaf('audio.playback.target_silence_ms', 'playback', 'number', {
    min: 50,
    max: 1500,
    advanced: true,
  }),
]

const AEC: HouseAudioSpec[] = [
  leaf('audio.aec.enabled', 'aec', 'boolean'),
  leaf('audio.aec.filter_len', 'aec', 'number', { min: 256, max: 16384 }),
  leaf('audio.aec.cancel', 'aec', 'enum', {
    options: [
      { value: 'mix', label: 'mix' },
      { value: 'per_channel', label: 'per_channel' },
    ],
  }),
  leaf('audio.aec.who', 'aec', 'enum', {
    options: [
      { value: 'device', label: 'device' },
      { value: 'controller', label: 'controller' },
      { value: 'both', label: 'both' },
      { value: 'none', label: 'none' },
    ],
  }),
]

const ACK: HouseAudioSpec[] = [
  leaf('ack.echo_ignore_ms', 'ack', 'number', { min: 0, max: 3000 }),
]

export const HOUSE_AUDIO_SPECS: readonly HouseAudioSpec[] = [
  ...CAPTURE,
  ...VOICE,
  ...PLAYBACK,
  ...AEC,
  ...ACK,
]

export const HOUSE_AUDIO_GROUPS: ReadonlyArray<{ id: string; labelKey: MsgKey }> = [
  { id: 'capture', labelKey: 'v2.houseAudio.group.capture' },
  { id: 'voice', labelKey: 'v2.houseAudio.group.voice' },
  { id: 'playback', labelKey: 'v2.houseAudio.group.playback' },
  { id: 'aec', labelKey: 'v2.houseAudio.group.aec' },
  { id: 'ack', labelKey: 'v2.houseAudio.group.ack' },
  { id: 'other', labelKey: 'v2.houseAudio.group.other' },
]

const SPEC_BY_PATH = new Map(HOUSE_AUDIO_SPECS.map((spec) => [spec.path, spec]))
const GROUP_INDEX = new Map(HOUSE_AUDIO_GROUPS.map((group, index) => [group.id, index]))
const SPEC_INDEX = new Map(HOUSE_AUDIO_SPECS.map((spec, index) => [spec.path, index]))

export function houseAudioSpec(path: string): HouseAudioSpec | null {
  return SPEC_BY_PATH.get(path) ?? null
}

export function houseAudioGroupOrder(group: string): number {
  if (group === 'other') return HOUSE_AUDIO_GROUPS.length
  return GROUP_INDEX.get(group) ?? HOUSE_AUDIO_GROUPS.length
}

export function houseAudioGroupLabelKey(group: string): MsgKey | null {
  return HOUSE_AUDIO_GROUPS.find((entry) => entry.id === group)?.labelKey ?? null
}

/** Read one leaf from the `GET /house/audio` aggregate. */
export function houseAudioValue(data: HouseAudioInfo | null, path: string): unknown {
  if (!data) return undefined
  if (path === 'ack.echo_ignore_ms') return data.ack?.echo_ignore_ms
  if (!path.startsWith('audio.')) return undefined
  let current: unknown = data.audio
  for (const part of path.slice('audio.'.length).split('.')) {
    if (!current || typeof current !== 'object') return undefined
    current = (current as Record<string, unknown>)[part]
  }
  return current
}

export interface HouseAudioRow {
  path: string
  group: string
  spec: HouseAudioSpec | null
  value: unknown
  explicit: boolean
  restart: boolean
  editable: boolean
}

function collectAudioLeaves(
  source: unknown,
  prefix: string,
  known: ReadonlySet<string>,
  explicit: ReadonlySet<string>,
  restartFields: ReadonlySet<string>,
  seen: Set<string>,
  out: HouseAudioRow[],
): void {
  if (source === null || source === undefined) return
  if (typeof source !== 'object' || Array.isArray(source)) {
    if (!known.has(prefix) && !seen.has(prefix)) {
      seen.add(prefix)
      out.push({
        path: prefix,
        group: 'other',
        spec: null,
        value: source,
        explicit: explicit.has(prefix),
        restart: restartFields.has(prefix),
        editable: false,
      })
    }
    return
  }
  for (const [key, value] of Object.entries(source as Record<string, unknown>)) {
    collectAudioLeaves(
      value,
      `${prefix}.${key}`,
      known,
      explicit,
      restartFields,
      seen,
      out,
    )
  }
}

/**
 * Build the editor rows.
 *
 * Known leaves keep their canonical order; unknown leaves found in the
 * response (or only in `explicit`) are appended read-only, so a newer backend
 * field is visible but cannot be written through this UI by accident.
 */
export function buildHouseAudioRows(data: HouseAudioInfo | null): HouseAudioRow[] {
  if (!data) return []
  const explicit = new Set(data.explicit ?? [])
  const restartFields = new Set(data.effects?.fields ?? [])
  const known = new Set(HOUSE_AUDIO_SPECS.map((spec) => spec.path))

  const rows: HouseAudioRow[] = HOUSE_AUDIO_SPECS.map((spec) => ({
    path: spec.path,
    group: spec.group,
    spec,
    value: houseAudioValue(data, spec.path),
    explicit: explicit.has(spec.path),
    restart: restartFields.has(spec.path),
    editable: true,
  }))

  const extras: HouseAudioRow[] = []
  const seen = new Set<string>()
  collectAudioLeaves(data.audio, 'audio', known, explicit, restartFields, seen, extras)
  for (const path of explicit) {
    if (known.has(path) || seen.has(path)) continue
    seen.add(path)
    extras.push({
      path,
      group: 'other',
      spec: null,
      value: houseAudioValue(data, path),
      explicit: true,
      restart: restartFields.has(path),
      editable: false,
    })
  }
  extras.sort((a, b) => a.path.localeCompare(b.path))
  return [...rows, ...extras]
}

export function sortHouseAudioRows(rows: HouseAudioRow[]): HouseAudioRow[] {
  return [...rows].sort((a, b) => {
    const byGroup = houseAudioGroupOrder(a.group) - houseAudioGroupOrder(b.group)
    if (byGroup !== 0) return byGroup
    const bySpec =
      (SPEC_INDEX.get(a.path) ?? Number.MAX_SAFE_INTEGER) -
      (SPEC_INDEX.get(b.path) ?? Number.MAX_SAFE_INTEGER)
    return bySpec !== 0 ? bySpec : a.path.localeCompare(b.path)
  })
}

function assignNested(target: Record<string, unknown>, path: string, value: unknown): void {
  const parts = path.split('.')
  let current = target
  for (const part of parts.slice(0, -1)) {
    const next = current[part]
    if (!next || typeof next !== 'object' || Array.isArray(next)) current[part] = {}
    current = current[part] as Record<string, unknown>
  }
  current[parts[parts.length - 1]] = value
}

/** Add exactly one leaf (or its `null` clear) to a PATCH body. */
export function setHouseAudioLeaf(
  body: PatchHouseAudioRequest,
  path: string,
  value: unknown,
): void {
  if (path === 'ack.echo_ignore_ms') {
    body.ack = { ...(body.ack ?? {}), echo_ignore_ms: value }
    return
  }
  if (!path.startsWith('audio.')) return
  const audio = (body.audio ??= {})
  assignNested(audio, path.slice('audio.'.length), value)
}
