/**
 * Shared helpers for the M9 config pages.
 *
 * The catalog is the only source of truth for shape: group order, field order,
 * labels, ranges, options and `effect` all come from the server. Nothing here
 * sorts by path or invents a group, so changing the catalog changes the page.
 */
import type {
  ConfigCatalog,
  ConfigFieldSpec,
  ConfigGroupSpec,
  CopyText,
} from './api'

/** A stable DOM id for a dotted field path (used by labels and error anchors). */
export function fieldInputId(path: string): string {
  return `v2-config-${path.replace(/[^a-zA-Z0-9_-]+/g, '-')}`
}

/**
 * Kinds the console knows how to edit. A catalog that grows a new kind must not
 * silently fall back to a free-text input: an unknown kind is rendered read-only
 * (see `ConfigField.vue`) so a value that cannot be edited is never mis-saved.
 */
export const CONFIG_FIELD_KINDS = ['bool', 'list', 'number', 'select', 'text', 'textarea'] as const

export function isKnownFieldKind(kind: string | null | undefined): boolean {
  return Boolean(kind) && (CONFIG_FIELD_KINDS as readonly string[]).includes(kind as string)
}

export function cloneValue<T>(value: T): T {
  if (Array.isArray(value)) return [...value] as T
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      out[key] = cloneValue(item)
    }
    return out as T
  }
  return value
}

/** Value equality for catalog leaves: arrays element-wise, numbers as numbers. */
export function valuesEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (a === null || a === undefined || b === null || b === undefined) {
    return (a ?? null) === (b ?? null)
  }
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false
    return a.every((item, index) => valuesEqual(item, b[index]))
  }
  if (typeof a === 'number' || typeof b === 'number') {
    // `Number('') === 0` would make a cleared number look unchanged. An empty
    // string is only equal to another empty string (handled by `a === b` above),
    // so the dirty count and the save diff agree on "cleared".
    if (typeof a === 'string' && a.trim() === '') return false
    if (typeof b === 'string' && b.trim() === '') return false
    return Number(a) === Number(b)
  }
  return false
}

/**
 * A non-nullable number cannot be represented by the empty control value.
 * Callers surface this as a field error instead of dropping the edit from the
 * request body (which used to read as "nothing to save").
 */
export function isBlankNumber(field: ConfigFieldSpec, value: unknown): boolean {
  if (field.kind !== 'number' || field.nullable) return false
  if (value === null || value === undefined) return true
  if (typeof value === 'number') return !Number.isFinite(value)
  return typeof value === 'string' && value.trim() === ''
}

/** `min`/`max`/`options` are rendered from the catalog entry verbatim. */
export interface FieldOption {
  value: string | number
  label: CopyText
}

export function fieldOptions(field: ConfigFieldSpec): FieldOption[] {
  if (!Array.isArray(field.options)) return []
  return field.options.map((option) => {
    // The catalog's `options` is `list[Any]`; today it mixes strings, numbers
    // (LED bit depth) and `{value,label}` objects.
    if (typeof option === 'string' || typeof option === 'number') {
      const text = String(option)
      return { value: option, label: { zh: text, en: text } }
    }
    const value = option.value
    const text = String(value)
    const label = option.label
    if (!label?.zh && !label?.en) {
      return { value, label: { zh: text, en: text } }
    }
    return { value, label }
  })
}

/**
 * Parse the textarea representation of a `list` field. One item per line is
 * the canonical form; a single line with commas is also accepted so an
 * operator can paste `a, b, c`.
 */
export function parseListInput(raw: string): string[] {
  const text = raw.trim()
  if (!text) return []
  const lines = text.includes('\n') ? text.split(/\r?\n/) : text.split(',')
  return lines.map((item) => item.trim()).filter(Boolean)
}

export function formatListInput(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).join('\n')
  return value === null || value === undefined ? '' : String(value)
}

/**
 * The value a draft control should start with.
 *
 * A nullable field with no value starts empty (not the string `"null"`), and a
 * missing value is normalized to `null` so the diff sees "unchanged".
 */
export function initialDraftValue(field: ConfigFieldSpec, value: unknown): unknown {
  if (value === undefined) return field.kind === 'list' ? [] : field.nullable ? null : ''
  return cloneValue(value)
}

/** Read a dotted path out of the redacted `/config` document (fallback only). */
export function readPath(data: unknown, path: string): unknown {
  let node: unknown = data
  for (const part of path.split('.')) {
    if (Array.isArray(node)) {
      const index = Number(part)
      if (!Number.isInteger(index) || index < 0 || index >= node.length) return undefined
      node = node[index]
    } else if (node && typeof node === 'object') {
      node = (node as Record<string, unknown>)[part]
    } else {
      return undefined
    }
  }
  return node
}

/** Human-readable representation for a read-only row. */
export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (Array.isArray(value)) return value.map((item) => String(item)).join(', ') || '—'
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

/**
 * Build the dotted-path diff for `PUT /config/values`.
 *
 * Only fields whose draft differs from the original are sent, so the request
 * body is exactly "what the operator changed" — not the whole page. `fields`
 * is walked in catalog order (deterministic request bodies).
 */
export function buildConfigDiff(
  fields: ConfigFieldSpec[],
  original: Record<string, unknown>,
  draft: Record<string, unknown>,
): { values: Record<string, unknown>; changed: ConfigFieldSpec[]; restartFields: string[] } {
  const values: Record<string, unknown> = {}
  const changed: ConfigFieldSpec[] = []
  for (const field of fields) {
    if (!field.writable) continue
    const before = original[field.path]
    const after = draft[field.path]
    if (valuesEqual(before, after)) continue
    values[field.path] = after
    changed.push(field)
  }
  return {
    values,
    changed,
    restartFields: changed
      .filter((field) => field.effect === 'restart')
      .map((field) => field.path),
  }
}

/** Catalog values first, `/config` as the fallback for a partial catalog. */
export function valueFor(
  catalog: ConfigCatalog,
  config: Record<string, unknown> | null,
  path: string,
): unknown {
  if (Object.prototype.hasOwnProperty.call(catalog.values, path)) {
    return catalog.values[path]
  }
  return config ? readPath(config, path) : undefined
}

/** Groups in server order, keeping only the ids the caller wants to show. */
export function orderedGroups(
  catalog: ConfigCatalog,
  include: (group: ConfigGroupSpec) => boolean,
): Array<{ group: ConfigGroupSpec; fields: ConfigFieldSpec[] }> {
  return catalog.groups
    .filter(include)
    .map((group) => ({
      group,
      // Server order is preserved inside a group (never re-sorted by path).
      fields: catalog.fields.filter((field) => field.group === group.id),
    }))
    .filter((entry) => entry.fields.length > 0)
}

export function isAbnormal(status: string): boolean {
  return status === 'warn' || status === 'fail' || status === 'error' || status === 'degraded'
}

/** `fail` before `warn` before `ok`; unknown statuses sort last. */
export function severityRank(status: string): number {
  if (status === 'fail' || status === 'error' || status === 'unsupported') return 0
  if (status === 'warn' || status === 'degraded') return 1
  if (status === 'ok' || status === 'healthy') return 3
  return 2
}

export function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—'
  const total = Math.floor(seconds)
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  if (days > 0) return `${days}d ${hours}h ${minutes}m`
  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m ${secs}s`
  return `${secs}s`
}
