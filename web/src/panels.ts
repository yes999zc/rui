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
}

export interface PanelSpec {
  id: string
  title: string
  icon?: string
  group: string
  order: number
  enabled: boolean
  enabled_reason?: string
  source: string
  extra_sources?: Record<string, string>
  refresh_s?: number
  blocks: PanelBlock[]
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

export const GROUP_TITLES: Record<string, string> = {
  status: '状态',
  living: '生活',
  config: '配置',
  system: '系统',
}
