/**
 * One place that turns a failed `/api/v2` call into operator-readable copy.
 *
 * Known contract codes get translated title + hint; anything else keeps the
 * backend's own English technical message (never silently swallowed). 422 field
 * paths stay structured so a future form can highlight inputs.
 */
import { V2ApiError } from './api'
import { t, type MsgKey } from './i18n'

export interface FieldIssue {
  path: string
  message: string
}

export interface ErrorCopy {
  code: string
  status: number | null
  title: string
  detail: string
  hint: string
  /** The backend's own English hint, kept alongside the translated one. */
  backendHint: string
  requestId: string | null
  retryable: boolean
  /** Seconds advertised by a 429, when the server sent one. */
  retryAfterS: number | null
  fields: FieldIssue[]
}

const CODE_KEYS: Record<string, { title: MsgKey; hint: MsgKey }> = {
  unauthorized: { title: 'v2.error.unauthorized', hint: 'v2.error.unauthorizedHint' },
  not_found: { title: 'v2.error.notFound', hint: 'v2.error.notFoundHint' },
  internal: { title: 'v2.error.internal', hint: 'v2.error.internalHint' },
  rate_limited: { title: 'v2.error.rateLimited', hint: 'v2.error.rateLimitedHint' },
  invalid_values: { title: 'v2.error.invalidValues', hint: 'v2.error.invalidValuesHint' },
  bad_request: { title: 'v2.error.badRequest', hint: 'v2.error.badRequestHint' },
  capability_unsupported: {
    title: 'v2.error.capabilityUnsupported',
    hint: 'v2.error.capabilityUnsupportedHint',
  },
  device_disabled: { title: 'v2.error.deviceDisabled', hint: 'v2.error.deviceDisabledHint' },
  device_not_connected: {
    title: 'v2.error.deviceNotConnected',
    hint: 'v2.error.deviceNotConnectedHint',
  },
  token_managed_by_adapter: {
    title: 'v2.error.tokenManagedByAdapter',
    hint: 'v2.error.tokenManagedByAdapterHint',
  },
  token_from_environment: {
    title: 'v2.error.tokenFromEnvironment',
    hint: 'v2.error.tokenFromEnvironmentHint',
  },
  token_missing: { title: 'v2.error.tokenMissing', hint: 'v2.error.tokenMissingHint' },
  path_not_allowed: {
    title: 'v2.error.pathNotAllowed',
    hint: 'v2.error.pathNotAllowedHint',
  },
  unknown_action: { title: 'v2.error.unknownAction', hint: 'v2.error.unknownActionHint' },
  action_failed: { title: 'v2.error.actionFailed', hint: 'v2.error.actionFailedHint' },
  secret_rejected: { title: 'v2.error.secretRejected', hint: 'v2.error.secretRejectedHint' },
  unavailable: { title: 'v2.error.unavailable', hint: 'v2.error.unavailableHint' },
  conflict: { title: 'v2.error.conflict', hint: 'v2.error.conflictHint' },
  device_exists: { title: 'v2.error.deviceExists', hint: 'v2.error.deviceExistsHint' },
  confirm_required: {
    title: 'v2.error.confirmRequired',
    hint: 'v2.error.confirmRequiredHint',
  },
  plug_unavailable: {
    title: 'v2.error.plugUnavailable',
    hint: 'v2.error.plugUnavailableHint',
  },
  plug_mismatch: { title: 'v2.error.plugMismatch', hint: 'v2.error.plugMismatchHint' },
  model_required: { title: 'v2.error.modelRequired', hint: 'v2.error.modelRequiredHint' },
  immutable_field: {
    title: 'v2.error.immutableField',
    hint: 'v2.error.immutableFieldHint',
  },
  port_conflict: { title: 'v2.error.portConflict', hint: 'v2.error.portConflictHint' },
  no_config: { title: 'v2.error.noConfig', hint: 'v2.error.noConfigHint' },
}

const STATUS_KEYS: Record<number, { title: MsgKey; hint: MsgKey }> = {
  400: CODE_KEYS.bad_request,
  401: CODE_KEYS.unauthorized,
  403: CODE_KEYS.path_not_allowed,
  404: CODE_KEYS.not_found,
  409: CODE_KEYS.conflict,
  422: CODE_KEYS.invalid_values,
  429: CODE_KEYS.rate_limited,
  500: CODE_KEYS.internal,
  502: CODE_KEYS.action_failed,
  503: CODE_KEYS.unavailable,
}

function generic(message: string): ErrorCopy {
  return {
    code: 'generic',
    status: null,
    title: t('v2.error.generic'),
    detail: message,
    hint: t('v2.error.genericHint'),
    backendHint: '',
    requestId: null,
    retryable: false,
    retryAfterS: null,
    fields: [],
  }
}

export function describeError(err: unknown): ErrorCopy {
  if (err instanceof V2ApiError) {
    const keys = CODE_KEYS[err.code] ?? STATUS_KEYS[err.status]
    return {
      code: err.code,
      status: err.status,
      title: keys ? t(keys.title) : t('v2.error.generic'),
      detail: err.message,
      hint: keys ? t(keys.hint) : err.hint || t('v2.error.genericHint'),
      backendHint: err.hint,
      requestId: err.requestId,
      retryable: err.retryable || err.status >= 500,
      retryAfterS: apiRetryAfterSeconds(err),
      fields: err.fields.map((field) => ({
        path: field.path,
        message: field.message ?? field.code ?? '',
      })),
    }
  }
  const message = err instanceof Error ? err.message : err ? String(err) : ''
  // `fetch` rejects with a TypeError when the network/route is unreachable.
  if (err instanceof TypeError || !message) {
    return {
      code: 'network',
      status: null,
      title: t('v2.error.network'),
      detail: message,
      hint: t('v2.error.networkHint'),
      backendHint: '',
      requestId: null,
      retryable: true,
      retryAfterS: null,
      fields: [],
    }
  }
  return generic(message)
}

export function isNotFound(err: unknown): boolean {
  return err instanceof V2ApiError && err.status === 404
}

/** Stable contract code of a failed call (`''` for network/unknown errors). */
export function apiErrorCode(err: unknown): string {
  return err instanceof V2ApiError ? err.code : ''
}

/** One key of `error.details`, when the server supplied an object. */
export function apiErrorDetail(err: unknown, key: string): unknown {
  if (!(err instanceof V2ApiError)) return undefined
  if (!err.details || typeof err.details !== 'object') return undefined
  return (err.details as Record<string, unknown>)[key]
}

/**
 * Map a contract 422 field path onto a config input id.
 *
 * The backend reports the stored shape (`devices[0].room`, occasionally
 * `devices[0].wake_words[1]`); the M3 form edits promoted fields one level up.
 * Unknown paths are returned unchanged so the generic field list still shows
 * them instead of losing the server's explanation.
 */
export function normalizeFieldPath(path: string): string {
  let next = path.trim()
  next = next.replace(/^devices\[\d+\]\./, '')
  next = next.replace(/^devices\.\d+\./, '')
  next = next.replace(/^device\./, '')
  if (next === 'port' || next === 'params.port') return 'listen_port'
  return next
}

/**
 * Cooldown advertised by a 429: `details.retry_after_s` wins over the
 * `Retry-After` header, so a proxy cannot shorten/lengthen the server's value.
 * Returns `null` when neither is usable (callers pick their own default).
 */
export function apiRetryAfterSeconds(err: unknown): number | null {
  const detail = apiErrorDetail(err, 'retry_after_s')
  const fromDetail =
    typeof detail === 'number'
      ? detail
      : typeof detail === 'string' && detail.trim()
        ? Number(detail)
        : NaN
  if (Number.isFinite(fromDetail) && fromDetail > 0) return Math.ceil(fromDetail)
  if (err instanceof V2ApiError && err.retryAfterS !== null && err.retryAfterS > 0) {
    return Math.ceil(err.retryAfterS)
  }
  return null
}
