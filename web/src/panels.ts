/**
 * The panel registry client (docs/console.md): the backend declares what pages
 * exist, this file evaluates the tiny expression language, the blocks render.
 *
 * Expressions are deliberately NOT a language: `$.a.b` looks a value up, `map`
 * translates enums, `enabled_when: "$.x=v"` is one equality. Anything richer
 * belongs in a hand-written component via PanelSpec's `component` escape hatch.
 */

export interface PanelCard {
  label: string
  value: string
  unit?: string
  map?: Record<string, string>
  tone?: Record<string, string>
}

export interface PanelButton {
  id: string
  label: string
  method: string
  href: string
  body?: unknown
  confirm?: string
  danger?: boolean
  refresh?: boolean
  enabled_when?: string
}

export interface PanelField {
  key: string
  label: string
  kind?: 'text' | 'number' | 'textarea' | 'select' | 'bool' | 'password'
  min?: number
  max?: number
  unit?: string
  hint?: string
  placeholder?: string
  options?: string[]
}

export interface PanelBlock {
  type: string
  title?: string
  sub?: string
  empty?: string
  max?: number
  source?: string
  cards?: PanelCard[]
  buttons?: PanelButton[]
  items?: string
  item_title?: string
  item_sub?: string
  section?: string
  apply?: string
  /** `config` block: catalog groups rendered inline by ConfigCenter. */
  groups?: string[]
  tier?: number
  fields?: PanelField[]
  columns?: PanelColumn[]
  follow?: boolean
  filter?: boolean
  /** Log lines containing this text are hidden until the reader unticks the box. */
  hide?: string
  hide_label?: string
  /** `say` block: where a typed turn is posted. */
  href?: string
  placeholder?: string
  hint?: string
  /** `volume` block: label, device floor, and the sentence under the slider. */
  label?: string
  min?: number
  note?: string
  /** `credentials` block: boolean path in the panel data (`credentials_present`). */
  status_key?: string
  /** `credentials` block: write endpoint (PUT) and connection-test endpoint (POST). */
  save?: { href: string; method?: string }
  test?: { href: string; method?: string }
}

export interface PanelColumn {
  key: string
  label: string
  width?: string
  truncate?: number
  join?: boolean
}

export interface PanelSpec {
  id: string
  title: string
  /** English title (contract, docs/config-catalog-contract.md); falls back to `title`. */
  title_en?: string
  icon?: string
  group: string
  order: number
  enabled: boolean
  enabled_reason?: string
  source: string
  extra_sources?: Record<string, string>
  refresh_s?: number
  blocks: PanelBlock[]
  /** Escape hatch: a hand-written component id replaces the blocks (panelComponents.ts). */
  component?: string
  /** Props handed to the escape-hatch component (e.g. `groups` for config-center). */
  component_args?: Record<string, unknown>
}

export interface PanelsDoc {
  revision: string
  panels: PanelSpec[]
}

/** `$.a.b` -> data.a.b; a missing middle renders as '' (a half-built page is not an error). */
export function lookup(expr: unknown, data: unknown): unknown {
  if (typeof expr !== 'string' || !expr.startsWith('$')) return expr
  const path = expr.slice(1).replace(/^\./, '')
  let cur: unknown = data
  for (const part of path.split('.').filter(Boolean)) {
    if (cur == null || typeof cur !== 'object') return undefined
    cur = (cur as Record<string, unknown>)[part]
  }
  return cur
}

/**
 * A label that mixes literals and lookups: `"$.room · $.id"` -> `living room · living`.
 * The spec examples in docs/console.md use this form for list subtitles, so the
 * renderer has to evaluate every `$.path` in the string, not just a bare path.
 */
export function format(expr: unknown, data: unknown): string {
  if (typeof expr !== 'string') return expr == null ? '' : String(expr)
  if (!expr.includes('$.')) return expr
  return expr.replace(/\$(\.[A-Za-z0-9_.]*)?/g, (_m, path: string | undefined) => {
    const value = lookup(`$${path ?? ''}`, data)
    return value == null ? '' : String(value)
  })
}

export function display(card: PanelCard, data: unknown): string {
  const raw = lookup(card.value, data)
  const text = raw == null ? '—' : String(raw)
  const mapped = card.map?.[text]
  return mapped != null ? mapped : text
}

export function toneOf(card: PanelCard, data: unknown): string {
  const raw = lookup(card.value, data)
  const tone = raw == null ? undefined : card.tone?.[String(raw)]
  return tone ?? ''
}

/** One equality: `$.state=playing`. Anything else is not ours to interpret. */
export function enabledWhen(expr: string | undefined, data: unknown): boolean {
  if (!expr) return true
  const eq = expr.split('=')
  if (eq.length !== 2) return true
  return String(lookup(eq[0], data) ?? '') === eq[1]
}

/**
 * The two navigation groups (docs/console-ia-audit.md §2.6/§3). The console moved
 * from four task-less buckets to "what is happening now" vs "what I configure or
 * maintain". Backend groups are still the fallback for panels that predate this
 * manifest; legacy names map onto the two groups so an older control plane lands
 * in the right place.
 */
const LEGACY_GROUPS: Record<string, string> = {
  status: 'run',
  living: 'run',
  life: 'run',
  system: 'run',
  config: 'configure',
  maintain: 'configure',
  run: 'run',
  configure: 'configure',
}

export function navGroup(group: string | undefined): string {
  if (!group) return 'run'
  return LEGACY_GROUPS[group] ?? 'configure'
}

/**
 * Panel id → designed nav group. A few panels arrive in a historical group
 * (`devices` in `status`, `mcp`/`devices-admin` in `config`); the console cares
 * about where the operator looks for them, so the placement is explicit here and
 * shared by the rail and the page kicker (they used to disagree for Logs).
 */
export const PANEL_NAV_GROUPS: Record<string, string> = {
  overview: 'run',
  turns: 'run',
  memory: 'run',
  logs: 'run',
  'assistant-session': 'configure',
  devices: 'configure',
  music: 'configure',
  providers: 'configure',
  tools: 'configure',
  foundation: 'configure',
  // Legacy ids stay reachable by URL (one release of compatibility) but are not
  // separate destinations: their task is already represented by a combined entry.
  speaker: 'configure',
  'devices-admin': 'configure',
  phrases: 'configure',
  mcp: 'configure',
}

export function panelNavGroup(panel: { id: string; group: string }): string {
  return PANEL_NAV_GROUPS[panel.id] ?? navGroup(panel.group)
}

/**
 * Panels that stay routes but leave the rail: their job is now part of a
 * combined destination. Old links keep working (PanelHost aliases + router);
 * this is the P0 "hide, do not delete" contract from the IA audit.
 */
export const HIDDEN_NAV_PANELS = new Set([
  'speaker',          // 设备与音频 → /panel/devices
  'devices-admin',    // 设备与音频 → /panel/devices (full merge is Phase 2)
  'phrases',          // 语音助手 → /panel/assistant-session
  'mcp',              // 工具与集成 → /panel/tools
])

/** Which destination lights up while a legacy panel is open. */
export const NAV_ACTIVE_ALIASES: Record<string, string[]> = {
  'assistant-session': ['assistant-session', 'phrases'],
  devices: ['devices', 'speaker', 'devices-admin'],
  tools: ['tools', 'mcp'],
}

/**
 * The rail order. Only panels listed here are primary destinations; anything a
 * newer backend adds and this build does not know yet is appended to the
 * configure group rather than silently dropped.
 */
export const NAV_GROUPS: { id: 'run' | 'configure'; panelIds: string[] }[] = [
  { id: 'run', panelIds: ['overview', 'turns', 'memory', 'logs'] },
  {
    id: 'configure',
    panelIds: ['assistant-session', 'devices', 'music', 'providers', 'tools', 'foundation'],
  },
]
