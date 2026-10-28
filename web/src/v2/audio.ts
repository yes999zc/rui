/**
 * Device audio override surface (contract C-002/1 §7.15–7.16).
 *
 * `GET /devices/{id}/audio` reports every effective leaf plus the four layers.
 * Which leaves may be overridden per device is *not* part of that response; it
 * is the C-001 `DeviceAudioOverride` schema: capture (all fields), a few voice
 * fields, a few playback fields, AEC ownership/enabled, and the ack echo hold.
 * Keeping that small table here makes the editor honest: a leaf outside it is
 * shown read-only instead of pretending the PATCH would accept it.
 *
 * The ack field has two paths on purpose: the API's provenance key is
 * `ack.echo_ignore_ms` (flat in `effective`), while the device override path is
 * `voice.ack_echo_ignore_ms` (contract §7.15).
 *
 * Keep this file in lockstep with `DeviceAudioOverride` and the provenance leaf
 * shape: any new/renamed leaf must be added to `AUDIO_LEAVES` (and to the
 * override paths only when the C-001 schema allows writing it). A leaf that is
 * missing here is not fatal — `buildAudioRows` still renders it read-only from
 * the server's `sources`/`layers`, so an unknown leaf can never crash the table
 * or pretend to be editable.
 */
import type { AudioLayer, DeviceAudio } from './api'
import type { MsgKey } from './i18n'
import { dottedValue } from './format'

export type AudioGroup = 'capture' | 'voice' | 'playback' | 'aec' | 'ack'
export type AudioControlKind = 'number' | 'boolean' | 'enum'

export interface AudioEnumOption {
  value: string | number
  /** Contract literal (e.g. `device`, `controller`, `16`); never translated. */
  label: string
}

export interface AudioLeafSpec {
  /** Provenance path: key of `sources`, `layers`, and `effective`. */
  path: string
  group: AudioGroup
  /** Path inside `devices[].audio`; ack lives under `voice`. */
  overridePath: string
  /** `null` = this leaf has no per-device override in the contract. */
  control: AudioControlKind | null
  min?: number
  max?: number
  options?: AudioEnumOption[]
}

function leaf(
  path: string,
  group: AudioGroup,
  control: AudioControlKind | null,
  bounds: { min?: number; max?: number; options?: AudioEnumOption[] } = {},
): AudioLeafSpec {
  return {
    path,
    group,
    overridePath: path,
    control,
    ...bounds,
  }
}

/** Capture layout: every field is part of `DeviceCaptureOverride`. */
const CAPTURE: AudioLeafSpec[] = [
  leaf('capture.rate', 'capture', 'number', { min: 8000, max: 96000 }),
  leaf('capture.channels', 'capture', 'number', { min: 1, max: 16 }),
  leaf('capture.mic_channels', 'capture', 'number', { min: 1, max: 16 }),
  leaf('capture.ref_channels', 'capture', 'number', { min: 0, max: 16 }),
  leaf('capture.bits', 'capture', 'enum', {
    options: [
      { value: 16, label: '16' },
      { value: 32, label: '32' },
    ],
  }),
  leaf('capture.mic_shift', 'capture', 'number', { min: 0, max: 31 }),
  leaf('capture.ref_shift', 'capture', 'number', { min: 0, max: 31 }),
]

/**
 * Voice leaves. Only followup/post-playback fields are overridable per device.
 * `voice.ack_echo_ignore_ms` is exposed as the ack row below, not here.
 */
const VOICE: AudioLeafSpec[] = [
  leaf('voice.rate', 'voice', null),
  leaf('voice.frame_ms', 'voice', null),
  leaf('voice.vad_min_speech_ms', 'voice', null),
  leaf('voice.vad_end_silence_ms', 'voice', null),
  leaf('voice.vad_preroll_ms', 'voice', null),
  leaf('voice.wake_skip_ms', 'voice', null),
  leaf('voice.barge_in', 'voice', null),
  leaf('voice.kws_enabled', 'voice', null),
  leaf('voice.kws_model_dir', 'voice', null),
  leaf('voice.kws_keywords_file', 'voice', null),
  leaf('voice.kws_probe_file', 'voice', null),
  leaf('voice.kws_chunk', 'voice', null),
  leaf('voice.kws_num_threads', 'voice', null),
  leaf('voice.vad_model_path', 'voice', null),
  leaf('voice.wake_preroll_ms', 'voice', null),
  leaf('voice.followup_delay_ms', 'voice', 'number', { min: 0, max: 10000 }),
  leaf('voice.followup_replay', 'voice', 'boolean'),
  leaf('voice.post_playback_mute_ms', 'voice', 'number', { min: 0, max: 10000 }),
]

/** Playback transport: rate/channels/prebuffer are the per-device knobs. */
const PLAYBACK: AudioLeafSpec[] = [
  leaf('playback.rate', 'playback', 'number', { min: 8000, max: 96000 }),
  leaf('playback.channels', 'playback', 'number', { min: 1, max: 8 }),
  leaf('playback.queue_ms', 'playback', null),
  leaf('playback.fade_out_ms', 'playback', null),
  leaf('playback.credit_window', 'playback', null),
  leaf('playback.prebuffer_ms', 'playback', 'number', { min: 0, max: 1000 }),
  leaf('playback.target_silence_ms', 'playback', null),
]

/** AEC ownership only; the DSP shape (`filter_len`, `cancel`) stays global. */
const AEC: AudioLeafSpec[] = [
  leaf('aec.enabled', 'aec', 'boolean'),
  leaf('aec.who', 'aec', 'enum', {
    options: [
      { value: 'device', label: 'device' },
      { value: 'controller', label: 'controller' },
      { value: 'both', label: 'both' },
      { value: 'none', label: 'none' },
    ],
  }),
  leaf('aec.filter_len', 'aec', null),
  leaf('aec.cancel', 'aec', null),
]

/** The ack hold is stored under `voice` but shown as its own group. */
const ACK: AudioLeafSpec = {
  path: 'ack.echo_ignore_ms',
  group: 'ack',
  overridePath: 'voice.ack_echo_ignore_ms',
  control: 'number',
  min: 0,
  max: 5000,
}

export const AUDIO_LEAVES: readonly AudioLeafSpec[] = [
  ...CAPTURE,
  ...VOICE,
  ...PLAYBACK,
  ...AEC,
  ACK,
]

export const AUDIO_GROUPS: ReadonlyArray<{ id: AudioGroup; labelKey: MsgKey }> = [
  { id: 'capture', labelKey: 'v2.audio.group.capture' },
  { id: 'voice', labelKey: 'v2.audio.group.voice' },
  { id: 'playback', labelKey: 'v2.audio.group.playback' },
  { id: 'aec', labelKey: 'v2.audio.group.aec' },
  { id: 'ack', labelKey: 'v2.audio.group.ack' },
]

const SPEC_BY_PATH = new Map(AUDIO_LEAVES.map((spec) => [spec.path, spec]))
const GROUP_INDEX = new Map<string, number>(
  AUDIO_GROUPS.map((group, index) => [group.id, index]),
)
const LEAF_INDEX = new Map<string, number>(
  AUDIO_LEAVES.map((spec, index) => [spec.path, index]),
)

export function audioSpec(path: string): AudioLeafSpec | null {
  return SPEC_BY_PATH.get(path) ?? null
}

/** Override path for a provenance path (`ack.echo_ignore_ms` → `voice.…`). */
export function overridePathFor(path: string): string {
  return SPEC_BY_PATH.get(path)?.overridePath ?? path
}

/** Provenance path for an override path (the reverse mapping). */
export function displayPathForOverride(path: string): string {
  return path === 'voice.ack_echo_ignore_ms' ? 'ack.echo_ignore_ms' : path
}

export function audioGroupFor(path: string): string {
  if (path === 'ack.echo_ignore_ms' || path.startsWith('ack.')) return 'ack'
  const group = path.split('.')[0]
  return group || 'other'
}

export function audioGroupLabelKey(group: string): MsgKey | null {
  return AUDIO_GROUPS.find((entry) => entry.id === group)?.labelKey ?? null
}

export interface AudioRow {
  path: string
  group: string
  spec: AudioLeafSpec | null
  effective: unknown
  source: string
  layer: AudioLayer | null
  overridden: boolean
  editable: boolean
}

/**
 * Build the table rows.
 *
 * The contract promises `sources`/`layers` for every leaf, so their union is
 * the authoritative row set. When a response carries no provenance at all
 * (the degraded `plug_resolved=false` case) the canonical leaf list is rendered
 * with `unresolved`, so the table never disappears and never invents a source.
 */
export function buildAudioRows(data: DeviceAudio): AudioRow[] {
  const sources = data.sources ?? {}
  const layers = data.layers ?? {}
  const overrides = new Set(data.overrides ?? [])
  const hasProvenance =
    Object.keys(sources).length > 0 ||
    Object.keys(layers).length > 0 ||
    overrides.size > 0
  const effectiveEmpty = Object.keys(data.effective ?? {}).length === 0

  const paths: string[] = []
  const seen = new Set<string>()
  const push = (path: string): void => {
    if (!path || seen.has(path)) return
    seen.add(path)
    paths.push(path)
  }

  if (hasProvenance) {
    for (const spec of AUDIO_LEAVES) {
      const effective = dottedValue(data.effective, spec.path)
      if (
        spec.path in sources ||
        spec.path in layers ||
        effective !== undefined ||
        overrides.has(spec.overridePath)
      ) {
        push(spec.path)
      }
    }
    for (const path of Object.keys(sources)) push(displayPathForOverride(path))
    for (const path of Object.keys(layers)) push(displayPathForOverride(path))
    for (const path of overrides) push(displayPathForOverride(path))
  } else if (data.plug_resolved === false || !effectiveEmpty) {
    // Degraded/unresolved payload: keep the canonical table visible. A truly
    // empty `effective` with a resolved plug has no rows (neutral empty state).
    for (const spec of AUDIO_LEAVES) push(spec.path)
  }

  const rank = (path: string): number =>
    GROUP_INDEX.get(audioGroupFor(path)) ?? AUDIO_GROUPS.length

  paths.sort((a, b) => {
    const byGroup = rank(a) - rank(b)
    if (byGroup !== 0) return byGroup
    const byLeaf = (LEAF_INDEX.get(a) ?? Number.MAX_SAFE_INTEGER) -
      (LEAF_INDEX.get(b) ?? Number.MAX_SAFE_INTEGER)
    if (byLeaf !== 0) return byLeaf
    return a.localeCompare(b)
  })

  return paths.map((path) => {
    const spec = audioSpec(path)
    const layer = layers[path] ?? null
    const deviceValue = layer?.device
    const overridden =
      overrides.has(spec?.overridePath ?? path) ||
      (deviceValue !== null && deviceValue !== undefined)
    return {
      path,
      group: audioGroupFor(path),
      spec,
      // The layer's `effective` is the server's value for that path; fall back
      // to the top-level map only when a layer was omitted.
      effective: layer?.effective ?? dottedValue(data.effective, path),
      source: String(sources[path] ?? layer?.source ?? 'unresolved'),
      layer,
      overridden,
      editable: Boolean(spec && spec.control),
    }
  })
}

/** The only audio leaf the global layer may legitimately override (task-26). */
export const NEGOTIATED_AUDIO_PATH = 'capture.rate'

/**
 * `capture.rate` is the one negotiated leaf: an explicit global value is the wire
 * rate the client is asked for, and the client resamples to the model's hardware
 * rate. It may therefore differ from the manifest without being a stale override.
 *
 * Every other manifest leaf is a hardware fact and wins over global config. The
 * path argument is required on purpose: an `AudioLayer` alone cannot tell capture
 * from playback, and `playback.rate`/`playback.channels` with the same
 * `global_explicit`/manifest shape must NOT be reported as negotiated — those
 * disagreements are the server's `conflicts` list, shown with the winner.
 */
export function isNegotiatedCaptureRate(
  path: string,
  layer: AudioLayer | null,
): boolean {
  if (path !== NEGOTIATED_AUDIO_PATH || !layer) return false
  if (layer.global_explicit !== true) return false
  if (layer.manifest === null || layer.manifest === undefined) return false
  if (layer.global === null || layer.global === undefined) return false
  return layer.global !== layer.manifest
}

/**
 * `true` when `source` is one of the three inherited layers (post-restore check).
 * `unresolved` is deliberately excluded: the override may be gone while the
 * plug still cannot resolve the manifest, so callers report it as a neutral
 * "cleared but unconfirmed" outcome instead of success or failure.
 */
export function isInheritedSource(source: string): boolean {
  return (
    source === 'device-manifest' ||
    source === 'global-config' ||
    source === 'core-default'
  )
}
