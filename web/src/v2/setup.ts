/**
 * Presentation helpers for the M6 setup wizard.
 *
 * The server owns the step order, status, evidence, allowed actions and
 * `next_step` (contract §7.23/§7.24). Nothing here reorders or infers those:
 * this module only maps stable ids/codes onto copy and defensive readers.
 */
import type { SetupStep } from './api'
import { normalizeFieldPath } from './errors'
import { t, type MsgKey } from './i18n'

const STEP_LABEL_KEYS: Record<string, MsgKey> = {
  keys: 'v2.setup.step.keys',
  models: 'v2.setup.step.models',
  prepare: 'v2.setup.step.prepare',
  params: 'v2.setup.step.params',
  wake: 'v2.setup.step.wake',
  connect: 'v2.setup.step.connect',
  test: 'v2.setup.step.test',
  verify: 'v2.setup.step.verify',
}

const STEP_DESCRIPTION_KEYS: Record<string, MsgKey> = {
  keys: 'v2.setup.stepDesc.keys',
  models: 'v2.setup.stepDesc.models',
  prepare: 'v2.setup.stepDesc.prepare',
  params: 'v2.setup.stepDesc.params',
  wake: 'v2.setup.stepDesc.wake',
  connect: 'v2.setup.stepDesc.connect',
  test: 'v2.setup.stepDesc.test',
  verify: 'v2.setup.stepDesc.verify',
}

const STATUS_KEYS: Record<string, MsgKey> = {
  pending: 'v2.setup.status.pending',
  done: 'v2.setup.status.done',
  skipped: 'v2.setup.status.skipped',
  error: 'v2.setup.status.error',
}

const SCOPE_KEYS: Record<string, MsgKey> = {
  house: 'v2.setup.scope.house',
  device: 'v2.setup.scope.device',
}

export function stepLabel(id: string): string {
  const key = STEP_LABEL_KEYS[id]
  return key ? t(key) : id
}

export function stepDescription(id: string): string {
  const key = STEP_DESCRIPTION_KEYS[id]
  return key ? t(key) : ''
}

export function setupStatusLabel(status: string): string {
  const key = STATUS_KEYS[status]
  return key ? t(key) : t('v2.setup.status.other', { status })
}

export function setupScopeLabel(scope: string): string {
  const key = SCOPE_KEYS[scope]
  return key ? t(key) : scope
}

export function stepStatusClass(status: string): string {
  if (status === 'done') return 'is-done'
  if (status === 'error') return 'is-error'
  if (status === 'skipped') return 'is-skipped'
  return 'is-pending'
}

export function isStepSettled(step: SetupStep | null | undefined): boolean {
  return step?.status === 'done' || step?.status === 'skipped'
}

export function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string')
}

export function boolValue(value: unknown): boolean {
  return value === true
}

export interface SetupCheck {
  id: string
  status: string
  code: string
  hint: string
}

/**
 * One `checks[]` list (`test.evidence.checks` or a live `device.test` result).
 * Unknown rows are ignored; the server owns every status string.
 */
export function checksFrom(raw: unknown): SetupCheck[] {
  if (!Array.isArray(raw)) return []
  const rows: SetupCheck[] = []
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object') continue
    const row = entry as Record<string, unknown>
    rows.push({
      id: typeof row.id === 'string' ? row.id : '',
      status: typeof row.status === 'string' ? row.status : '',
      code: typeof row.code === 'string' ? row.code : '',
      hint: typeof row.hint === 'string' ? row.hint : '',
    })
  }
  return rows
}

/** `test.evidence.checks` is a list of check objects; unknown rows are ignored. */
export function setupChecks(step: SetupStep | null | undefined): SetupCheck[] {
  return checksFrom(step?.evidence?.checks)
}

/** A check is acceptable for the green "can continue" state. */
export function checkPasses(status: string): boolean {
  return status === 'ok' || status === 'skipped' || status === 'not_applicable'
}

/** Badge classes for a check/result status; unknown values stay neutral. */
export function checkTone(status: string): string {
  if (status === 'ok') return 'badge-ok'
  if (status === 'fail') return 'badge-fail'
  if (status === 'not_applicable' || status === 'skipped') return 'badge-muted'
  return ''
}

/** Read `{ok,stage,provider,message,details}` from a `keys.test` result. */
export function keyTestView(result: Record<string, unknown> | null): {
  ok: boolean
  provider: string
  message: string
  docsUrl: string
  missing: string[]
} | null {
  if (!result || typeof result.ok !== 'boolean') return null
  const details =
    result.details && typeof result.details === 'object'
      ? (result.details as Record<string, unknown>)
      : {}
  return {
    ok: result.ok,
    provider: typeof result.provider === 'string' ? result.provider : '',
    message: typeof result.message === 'string' ? result.message : '',
    docsUrl: typeof details.docs_url === 'string' ? details.docs_url : '',
    missing: stringList(details.missing),
  }
}

/**
 * `details.fields[]` uses stored paths (`devices[0].room`, `params.oww_model`).
 * Params inputs are addressed one level up, so the wizard normalizes them.
 */
export function setupFieldKey(path: string): string {
  const next = normalizeFieldPath(path)
  const wake = next.match(/^wake_words(?:\[(\d+)\]|\.(\d+))$/)
  if (wake) return `wake_words:${wake[1] ?? wake[2]}`
  return next
}
