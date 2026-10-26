/**
 * Shared health-page helpers. Kept outside the view so the audio table and the
 * recovery card can be maintained (and reviewed) as their own components.
 */
import type { HealthAudioDevice, HealthWarning } from './api'
import { t } from './i18n'

/** `fail` for a stopped/erroring plane, `warn` for restarts or a rejected speaker. */
export function audioSeverity(row: HealthAudioDevice): 'fail' | 'warn' | null {
  if (!row.enabled) return null
  if (row.error) return 'fail'
  if (!row.running) return 'fail'
  if (row.speaker_connected === false) return 'fail'
  if (row.restarts > 0 || row.speaker_rejected > 0) return 'warn'
  return null
}

export function speakerText(value: boolean | null): string {
  if (value === null) return t('v2.common.unknown')
  return value ? t('v2.common.yes') : t('v2.common.no')
}

function detailText(value: unknown): string {
  if (typeof value === 'string') return value.trim()
  if (typeof value === 'number') return String(value)
  return ''
}

/**
 * The device a warning belongs to. `plane_not_running` (and friends) carry the
 * id in `details.device`, not at the top level, so the top-level field alone
 * would collapse two different planes into one attention row.
 */
export function warningDevice(warning: HealthWarning): string {
  return warning.device_id || detailText(warning.details?.device)
}

/**
 * A stable identity for de-duplicating warnings. Two `plane_not_running`
 * warnings stay separate when they mention different devices/rooms (or, as a
 * last resort, different messages).
 */
export function warningIdentity(warning: HealthWarning): string {
  const device = warningDevice(warning)
  const room = detailText(warning.details?.room)
  return device || room || warning.message?.trim() || ''
}
