import { locale, t } from './i18n'

const LOCALE_TAG: Record<typeof locale.value, string> = { zh: 'zh-Hans', en: 'en' }

function tag(): string {
  return LOCALE_TAG[locale.value]
}

/** `last_seen` / activity timestamps are Unix seconds (float). */
export function relativeTime(ts: number | null | undefined, nowMs = Date.now()): string {
  if (ts == null || !Number.isFinite(ts)) return ''
  const diff = nowMs / 1000 - ts
  const abs = Math.abs(diff)
  const rtf = new Intl.RelativeTimeFormat(tag(), { numeric: 'auto', style: 'short' })
  if (abs < 60) return rtf.format(-Math.round(diff), 'second')
  if (abs < 3600) return rtf.format(-Math.round(diff / 60), 'minute')
  if (abs < 86400) return rtf.format(-Math.round(diff / 3600), 'hour')
  if (abs < 30 * 86400) return rtf.format(-Math.round(diff / 86400), 'day')
  return formatDateTime(ts)
}

/** `last_test_at` is an ISO string, not Unix seconds. */
export function formatIso(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(tag(), { dateStyle: 'medium', timeStyle: 'medium' }).format(date)
}

export function formatDateTime(ts: number | null | undefined): string {
  if (ts == null || !Number.isFinite(ts)) return ''
  return new Intl.DateTimeFormat(tag(), { dateStyle: 'medium', timeStyle: 'medium' }).format(
    new Date(ts * 1000),
  )
}

/** Render an arbitrary effective/param value without inventing copy. */
export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return t('v2.common.na')
  if (typeof value === 'boolean') return value ? t('v2.common.yes') : t('v2.common.no')
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : String(value)
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

export function jsonText(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return String(value)
  }
}

/** Dotted lookup into the effective audio object (`ack.echo_ignore_ms` is flat). */
export function dottedValue(source: Record<string, unknown> | null | undefined, path: string): unknown {
  if (!source) return undefined
  if (path === 'ack.echo_ignore_ms') return source.ack_echo_ignore_ms
  let current: unknown = source
  for (const part of path.split('.')) {
    if (!current || typeof current !== 'object') return undefined
    current = (current as Record<string, unknown>)[part]
  }
  return current
}

