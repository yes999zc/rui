/**
 * "Needs attention" derivation for the run overview (M7).
 *
 * The backend already answers every question this list asks, so nothing is
 * invented here: per-device `status` + `status_reason` (error / offline /
 * disabled), each device's own `warnings`, the house-level `warnings`
 * (`duplicate_room`, fleet failures, plane limits), and `setup.complete` /
 * `setup.verified`. The list only ranks and links them.
 */
import type { DeviceSummary, HouseInfo, WarningItem } from './api'
import { statusReasonText, t, warningTitle } from './i18n'

export type AttentionKind = 'device' | 'warning' | 'config'
export type AttentionTone = 'fail' | 'warn'

export interface AttentionItem {
  key: string
  kind: AttentionKind
  /** Stable reason code from the backend (`device_disabled`, `duplicate_room`, ...). */
  code: string
  title: string
  detail: string
  /** In-app destination: a device detail, the rooms page, or the setup wizard. */
  to: string
  tone: AttentionTone
  deviceId?: string
  room?: string
}

function devicePath(id: string): string {
  return `/devices/${encodeURIComponent(id)}`
}

function deviceLabel(device: DeviceSummary): string {
  return device.room ? `${device.id} · ${device.room}` : device.id
}

function attentionScore(item: AttentionItem): number {
  if (item.tone === 'fail') return 0
  if (item.kind === 'device') return 1
  if (item.kind === 'warning') return 2
  return 3
}

function warningDetail(warning: WarningItem): string {
  return warning.hint || warning.message || ''
}

/**
 * Build the attention list. Device rows win over house warnings for the same
 * condition (the device row has the live status), and the duplicate-room
 * warning links to the rooms page rather than to one of the two devices.
 */
export function buildAttentionItems(
  info: HouseInfo | null,
  devices: DeviceSummary[],
): AttentionItem[] {
  const items: AttentionItem[] = []

  for (const device of devices) {
    const reason = statusReasonText(device.status_reason)
    if (!device.enabled) {
      items.push({
        key: `device:${device.id}:disabled`,
        kind: 'device',
        code: device.status_reason?.code || 'device_disabled',
        title: `${t('v2.run.attention.deviceDisabled')} · ${deviceLabel(device)}`,
        detail: reason,
        to: devicePath(device.id),
        tone: 'warn',
        deviceId: device.id,
        room: device.room,
      })
    } else if (!device.ready) {
      const severe = device.status === 'error' || device.status === 'unavailable'
      items.push({
        key: `device:${device.id}:${device.status}`,
        kind: 'device',
        code: device.status_reason?.code || device.status,
        title: `${t(severe ? 'v2.run.attention.deviceError' : 'v2.run.attention.deviceOffline')} · ${deviceLabel(device)}`,
        detail: reason,
        to: devicePath(device.id),
        tone: severe ? 'fail' : 'warn',
        deviceId: device.id,
        room: device.room,
      })
    }

    for (const [index, warning] of (device.warnings ?? []).entries()) {
      // `duplicate_room` is a room-level statement and is handled below, where
      // it can link to the rooms page instead of one of the two devices.
      if (warning.code === 'duplicate_room') continue
      // `device_warnings_for()` copies house-level warnings (fleet failures,
      // plane limits) into every device list. They have no `device_id`, and
      // rendering them here would repeat one house-wide condition per device
      // and link it to the wrong device; the house branch below renders them once.
      if (!warning.device_id) continue
      items.push({
        key: `warning:${device.id}:${warning.code}:${index}`,
        kind: 'warning',
        code: warning.code,
        title: warningTitle(warning.code, warning.message),
        detail: warningDetail(warning),
        to: devicePath(device.id),
        tone: 'warn',
        deviceId: device.id,
        room: device.room,
      })
    }
  }

  const devicesKnown = devices.length > 0
  const deviceById = new Map(devices.map((device) => [device.id, device]))
  for (const [index, warning] of (info?.warnings ?? []).entries()) {
    if (warning.code === 'duplicate_room') {
      // Each warning names the first device in its room. Map that id through the
      // device catalog instead of reusing the first duplicate room for all of
      // them, and keep the backend's room-specific message as the detail.
      const owner = warning.device_id ? deviceById.get(warning.device_id) : undefined
      items.push({
        key: `house:duplicate_room:${index}`,
        kind: 'warning',
        code: warning.code,
        title: warningTitle(warning.code, warning.message),
        detail: warning.message || warning.hint || t('v2.run.attention.openRooms'),
        to: '/rooms',
        tone: 'warn',
        deviceId: warning.device_id,
        room: owner?.room || undefined,
      })
      continue
    }
    if (warning.device_id && warning.device_id !== '') {
      // The device row above already carries its own warnings with live status.
      if (devicesKnown) continue
      items.push({
        key: `house:${warning.code}:${warning.device_id}:${index}`,
        kind: 'warning',
        code: warning.code,
        title: warningTitle(warning.code, warning.message),
        detail: warningDetail(warning),
        to: devicePath(warning.device_id),
        tone: 'warn',
        deviceId: warning.device_id,
      })
      continue
    }
    items.push({
      key: `house:${warning.code}:${index}`,
      kind: 'warning',
      code: warning.code,
      title: warningTitle(warning.code, warning.message),
      detail: warningDetail(warning),
      to: '/rooms',
      tone: 'warn',
    })
  }

  if (info) {
    const first = devices[0]
    if (!info.setup.complete) {
      items.push({
        key: 'config:setup_incomplete',
        kind: 'config',
        code: 'setup_incomplete',
        title: t('v2.run.attention.config'),
        detail: t('v2.run.attention.configHint'),
        to: devicesKnown ? devicePath(first.id) : '/setup',
        tone: 'warn',
        deviceId: first?.id,
      })
    } else if (!info.setup.verified) {
      items.push({
        key: 'config:setup_unverified',
        kind: 'config',
        code: 'setup_unverified',
        title: t('v2.run.attention.unverified'),
        detail: t('v2.run.attention.unverifiedHint'),
        to: first ? `${devicePath(first.id)}?tab=test` : '/rooms',
        tone: 'warn',
        deviceId: first?.id,
      })
    }
  }

  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => attentionScore(a.item) - attentionScore(b.item) || a.index - b.index)
    .map((entry) => entry.item)
}
