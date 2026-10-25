import { shallowRef } from 'vue'
import { api, type CapabilityCatalogItem } from './api'
import { pickLocalized } from './i18n'

/**
 * Capability catalog cache. Capability labels belong to the core, not to the
 * plug and not to this frontend: when the catalog is unavailable we show the
 * raw id (plus a note) instead of inventing a Chinese label or branching on a
 * model name.
 */
export const capabilityItems = shallowRef<CapabilityCatalogItem[]>([])
export const capabilityError = shallowRef<unknown>(null)

let inflight: Promise<void> | null = null

export function loadCapabilities(force = false): Promise<void> {
  if (inflight) return inflight
  if (capabilityItems.value.length > 0 && !force) return Promise.resolve()
  inflight = (async () => {
    try {
      const catalog = await api.capabilities()
      capabilityItems.value = catalog.items ?? []
      capabilityError.value = null
    } catch (err) {
      capabilityError.value = err
    } finally {
      inflight = null
    }
  })()
  return inflight
}

export function capabilityItem(id: string): CapabilityCatalogItem | undefined {
  return capabilityItems.value.find((item) => item.id === id)
}

export function capabilityLabel(id: string): string {
  const item = capabilityItem(id)
  return (item && pickLocalized(item.label)) || id
}

export function capabilityDescription(id: string): string {
  const item = capabilityItem(id)
  if (!item) return ''
  return [pickLocalized(item.description), pickLocalized(item.degraded_hint)].filter(Boolean).join(' — ')
}
