/**
 * `/v2/tools` section model.
 *
 * The catalog is the only source of truth for groups, field order, labels,
 * kinds, ranges and `effect`; this module only decides *which* fields belong to
 * the tools page and how they are split into the four sections 9哥 asked for:
 *
 *   weather / news / Home Assistant (its own bridge) / MCP servers / other tools
 *
 * The split is derived from the catalog's group id (preferred) or, when the
 * catalog ships flat `tools` fields, from the field's own second path segment.
 * Group titles/hints always come from the server; a synthesized group (only
 * needed for a flat/partial catalog) uses the path prefix as its title, so no
 * business field name is hard-coded in the page.
 */
import type { ConfigCatalog, ConfigFieldSpec, ConfigGroupSpec } from './api'

export type ToolsSectionKey = 'weather' | 'news' | 'mcp' | 'other'

export interface ToolsGroup {
  group: ConfigGroupSpec
  fields: ConfigFieldSpec[]
}

export interface ToolsSection {
  key: ToolsSectionKey
  groups: ToolsGroup[]
}

/** Fixed presentation order; empty sections are omitted. */
export const TOOLS_SECTION_ORDER: ToolsSectionKey[] = ['weather', 'news', 'mcp', 'other']

export function isToolsGroupId(id: string): boolean {
  return id === 'tools' || id.startsWith('tools.')
}

/** Catalog-scoped: the field's group or its dotted path marks it as a tool. */
export function isToolsField(field: ConfigFieldSpec): boolean {
  return isToolsGroupId(field.group) || field.path.startsWith('tools.')
}

/**
 * Catalog fields this page must not render, matched by exact path.
 *
 * `tools.mcp_servers` is an array of server objects (`{name, transport, url,
 * command, env, headers, token_env, enabled}`), so the generic scalar renderer
 * would stringify each row to `[object Object]`; its catalog hint also points
 * at the disabled `PUT /api/v1/mcp`. C-002 §1.2 only allows the *read-only*
 * `GET /api/v1/mcp` on this page, and the `tools-mcp-status` card already shows
 * every server's name/kind/state, so the raw rows are deliberately dropped
 * instead of being dumped as JSON (they carry `env`/`headers`).
 */
const EXCLUDED_TOOL_PATHS = new Set<string>(['tools.mcp_servers'])

/**
 * Second segment of a tool path (`tools.weather.city` -> `weather`), or the
 * group suffix when called with `tools.weather`.
 */
function segment(value: string): string {
  const parts = value.split('.')
  if (parts[0] === 'tools' && parts.length > 1) return parts[1]
  return parts[parts.length - 1] ?? ''
}

export function toolsSectionKey(value: string): ToolsSectionKey {
  const segmentValue = segment(value).toLowerCase()
  if (segmentValue === 'weather') return 'weather'
  if (segmentValue === 'news') return 'news'
  // `tools.mcp`, `tools.ha`, `tools.ha.base_url` all land here. (`tools.mcp_servers`
  // is dropped earlier by EXCLUDED_TOOL_PATHS, so it never reaches a section.)
  if (segmentValue === 'mcp' || segmentValue === 'ha' || segmentValue.startsWith('mcp_')) {
    return 'mcp'
  }
  return 'other'
}

/**
 * Group id a field is rendered under. A real sub-group (`tools.weather`) wins;
 * a flat `tools` group (or a field missing from `catalog.groups`) is split by
 * its path so the MCP/HA fields do not collapse into one card. `mcp_allow_stdio`
 * and other `mcp_*` leaves share the `mcp` prefix, so a flat catalog still shows
 * one MCP card instead of one card per leaf path.
 */
function groupIdFor(field: ConfigFieldSpec, known: Set<string>): string {
  if (field.group !== 'tools' && isToolsGroupId(field.group) && known.has(field.group)) {
    return field.group
  }
  const prefix = segment(field.path)
  if (!prefix) return 'tools'
  return `tools.${prefix === 'mcp' || prefix.startsWith('mcp_') ? 'mcp' : prefix}`
}

function syntheticGroup(id: string, field: ConfigFieldSpec, index: number): ConfigGroupSpec {
  const label = segment(id) || id
  return {
    id,
    title: { zh: label, en: label },
    tier: field.tier,
    order: index,
    effect: 'save',
    hint: { zh: '', en: '' },
  }
}

export function toolsFields(catalog: ConfigCatalog): ConfigFieldSpec[] {
  return catalog.fields.filter(
    (field) => isToolsField(field) && !EXCLUDED_TOOL_PATHS.has(field.path),
  )
}

/**
 * Build the four sections from catalog order. Fields keep the server's order
 * inside a group; groups keep the server's group order (`catalog.groups`).
 */
export function toolsSections(catalog: ConfigCatalog): ToolsSection[] {
  const fields = toolsFields(catalog)
  if (!fields.length) return []

  const serverGroups = new Map<string, ConfigGroupSpec>()
  const serverOrder = new Map<string, number>()
  catalog.groups.forEach((group, index) => {
    if (!isToolsGroupId(group.id)) return
    serverGroups.set(group.id, group)
    serverOrder.set(group.id, index)
  })

  const byGroup = new Map<string, ToolsGroup>()
  const appearance: string[] = []
  const known = new Set(serverGroups.keys())

  fields.forEach((field, index) => {
    const id = groupIdFor(field, known)
    let entry = byGroup.get(id)
    if (!entry) {
      const group = serverGroups.get(id) ?? syntheticGroup(id, field, index)
      entry = { group, fields: [] }
      byGroup.set(id, entry)
      appearance.push(id)
    }
    entry.fields.push(field)
  })

  const orderOf = (id: string): number => serverOrder.get(id) ?? 1000 + appearance.indexOf(id)
  // Entries only enter `byGroup` on their first field, so excluding a path
  // (e.g. `tools.mcp_servers`) can never leave an empty group card behind.
  const ordered = [...byGroup.values()].sort((a, b) => orderOf(a.group.id) - orderOf(b.group.id))

  const sections: ToolsSection[] = []
  for (const key of TOOLS_SECTION_ORDER) {
    const groups = ordered.filter((entry) => toolsSectionKey(entry.group.id) === key)
    if (groups.length) sections.push({ key, groups })
  }
  return sections
}
