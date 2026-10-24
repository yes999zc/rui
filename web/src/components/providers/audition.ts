/**
 * Audition transport shared by the providers page and the legacy config center.
 *
 * Speaker playback is the default: the backend synthesises the line and sends it
 * to the audio plane, returning JSON. If that path is unavailable (old backend
 * returning WAV, a 4xx/5xx, or a network error), the caller still gets a browser
 * WAV fallback and a localized explanation.
 */
import { ApiError, token } from '../../api'
import { t } from '../../i18n'

export type AuditionOutput = 'speaker' | 'browser'

export interface AuditionRequest {
  stage: string
  provider: string
  body: Record<string, unknown>
}

export type AuditionOutcome =
  | { played: 'speaker'; duration_ms: number; provider: string; voice: string }
  | { played: 'browser'; blob: Blob; fallback?: string }

const STORAGE_KEY = 'rui-audition-output'

export function readAuditionOutput(): AuditionOutput {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'browser' ? 'browser' : 'speaker'
  } catch {
    return 'speaker'
  }
}

export function writeAuditionOutput(output: AuditionOutput): void {
  try {
    localStorage.setItem(STORAGE_KEY, output)
  } catch {
    /* private mode: the in-memory choice still works for this page */
  }
}

function headers(): Headers {
  const h = new Headers({ 'Content-Type': 'application/json' })
  const authToken = token.get()
  if (authToken) h.set('X-Auth-Token', authToken)
  return h
}

async function detailOf(response: Response): Promise<string> {
  let detail = response.statusText
  try {
    const payload = (await response.json()) as { detail?: unknown }
    detail = typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail)
  } catch {
    /* not JSON: keep the status text */
  }
  return detail || `HTTP ${response.status}`
}

async function browserWav(request: AuditionRequest): Promise<Blob> {
  const response = await fetch(
    `/api/v1/providers/${request.stage}/${encodeURIComponent(request.provider)}/audition?output=browser`,
    { method: 'POST', headers: headers(), body: JSON.stringify(request.body) },
  )
  if (!response.ok) throw new ApiError(response.status, response.statusText, await detailOf(response))
  return response.blob()
}

export async function requestAudition(request: AuditionRequest, output: AuditionOutput): Promise<AuditionOutcome> {
  if (output === 'browser') return { played: 'browser', blob: await browserWav(request) }

  const url = `/api/v1/providers/${request.stage}/${encodeURIComponent(request.provider)}/audition?output=speaker`
  let response: Response | null = null
  let failure = ''
  try {
    response = await fetch(url, { method: 'POST', headers: headers(), body: JSON.stringify(request.body) })
  } catch (exc) {
    failure = exc instanceof Error ? exc.message : String(exc)
  }

  if (response?.ok) {
    const type = response.headers.get('content-type') ?? ''
    if (type.includes('application/json')) {
      const payload = (await response.json()) as {
        played?: string
        duration_ms?: number
        provider?: string
        voice?: string
      }
      if (payload.played === 'speaker') {
        return {
          played: 'speaker',
          duration_ms: Number(payload.duration_ms ?? 0),
          provider: String(payload.provider ?? request.provider),
          voice: String(payload.voice ?? ''),
        }
      }
      failure = payload.played || 'unexpected audition response'
    } else {
      // An old backend ignored `output` and answered with the WAV directly.
      return {
        played: 'browser',
        blob: await response.blob(),
        fallback: t('providers.auditionFallbackUnsupported'),
      }
    }
  } else if (response) {
    failure = await detailOf(response)
    // Speaker path explicitly asks the client to retry with the browser; keep the
    // explanation short and actionable instead of surfacing a 503 stack message.
    if (response.status === 503) failure = ''
  }

  try {
    const blob = await browserWav(request)
    return {
      played: 'browser',
      blob,
      fallback: failure
        ? t('providers.auditionFallback', { message: failure })
        : t('providers.auditionFallbackUnavailable'),
    }
  } catch (fallbackError) {
    if (response && !response.ok) {
      throw new ApiError(response.status, response.statusText, failure)
    }
    throw fallbackError
  }
}
