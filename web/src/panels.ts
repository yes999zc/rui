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
  kind?: 'text' | 'number' | 'textarea' | 'select' | 'bool'
  min?: number
  max?: number
  unit?: string
  hint?: string
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
 * The four navigation groups (docs/console-redesign.md §3). Panel groups come
 * from the backend; legacy names map onto the new ones so an older control plane
 * still lands in the right place. Unknown groups are appended, never dropped.
 */
export const GROUP_ORDER = ['run', 'life', 'config', 'maintain'] as const

const LEGACY_GROUPS: Record<string, string> = {
  status: 'run',
  living: 'life',
  system: 'maintain',
  config: 'config',
  run: 'run',
  life: 'life',
  maintain: 'maintain',
}

export function navGroup(group: string | undefined): string {
  if (!group) return 'run'
  return LEGACY_GROUPS[group] ?? group
}
