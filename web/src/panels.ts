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

/** Backend copy can be a bare string or a bilingual `{zh, en}` pair. */
export type PanelText = string | { zh?: string; en?: string }

export interface PanelTab {
  id: string
  title: PanelText
  title_en?: string
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
  /** `component` block: a hand-written view from panelComponents.ts. */
  component?: string
  component_args?: Record<string, unknown>
  /** Tab this block belongs to when the PanelSpec declares `tabs`. */
  tab?: string
  /** Page section heading (music: 正在播放 / 音源与凭据 / 高级). */
  section_title?: PanelText
  /** `config` block: optional caution banner (hardware-following parameters). */
  warning?: PanelText
  /** `config` block: catalog groups rendered inline by ConfigCenter. */
  groups?: string[]
  /** `config` block: render only these dotted paths (logs owns log_level). */
  paths?: string[]
  /** `config` block: omit these dotted paths (system omits log_level). */
  exclude_paths?: string[]
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
  /** `credentials` block: boolean path in the panel data (`credentials_ready`). */
  status_key?: string
  /** `credentials` block: source registry endpoint (presets + credential slots). */
  sources_href?: string
  /** `credentials` block: link to the write-only credentials ledger. */
  secrets_href?: string
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

/** Navigation section; the backend's `nav.section` is authoritative. */
export type PanelSection = 'run' | 'configure'

/**
 * The backend-owned navigation manifest (docs/console-ia-audit.md §5, Phase 4).
 * The rail reads this and nothing else: `hidden` panels stay reachable by URL but
 * leave the rail (credentials), `icon` names an AppIcon, and `order` sorts the
 * section (lower first). Utility routes and section titles live in web/src/nav.ts.
 */
export interface PanelNav {
  section: PanelSection
  order: number
  hidden: boolean
  icon: string
}

export interface PanelSpec {
  id: string
  title: string
  /** English title (contract, docs/config-catalog-contract.md); falls back to `title`. */
  title_en?: string
  /** Authoritative nav placement/icon; the legacy group/order/icon fields are gone. */
  nav: PanelNav
  enabled: boolean
  enabled_reason?: string
  source: string
  extra_sources?: Record<string, string>
  refresh_s?: number
  blocks: PanelBlock[]
  /** Declarative tabs (device/audio, integrations, voice assistant). */
  tabs?: PanelTab[]
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

/*
 * Navigation is NOT declared here anymore (Phase 4): every PanelSpec carries its
 * own `nav` manifest from the backend, and the three hand-written utility routes
 * plus the section headings live in web/src/nav.ts. This module only parses specs
 * and evaluates their tiny expression language.
 */
