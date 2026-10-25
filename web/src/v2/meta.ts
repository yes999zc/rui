import { ref, shallowRef } from 'vue'
import { api, type MetaInfo } from './api'

/**
 * `/api/v2/meta` is the startup gate (contract §7.1): the shell reads it first,
 * refuses to issue page requests on a contract mismatch, and keeps `features`
 * as the only switch for hiding pages the backend has not enabled yet.
 */
export const meta = shallowRef<MetaInfo | null>(null)
export const metaError = shallowRef<unknown>(null)
/**
 * The gate starts closed: until the first `/meta` response (success **or**
 * failure) the shell must not render `RouterView`, otherwise the page mounts,
 * fires its own `/api/v2` requests and is unmounted again when the gate closes.
 */
export const metaLoading = ref(true)
export const metaSettled = ref(false)

let inflight: Promise<void> | null = null

export function loadMeta(): Promise<void> {
  if (inflight) return inflight
  metaLoading.value = true
  metaError.value = null
  inflight = (async () => {
    try {
      meta.value = await api.meta()
    } catch (err) {
      meta.value = null
      metaError.value = err
    } finally {
      metaLoading.value = false
      metaSettled.value = true
      inflight = null
    }
  })()
  return inflight
}

/**
 * Only an explicit `false` disables a feature. A missing key is treated as
 * available so a partially deployed backend (M1 read-only endpoints) still
 * renders, while the contract's "hide when false" rule is honoured.
 */
export function metaFeature(name: string): boolean {
  return meta.value?.features?.[name] !== false
}

export const EXPECTED_CONTRACT_FAMILY = 'C-002'

export function contractCompatible(info: MetaInfo | null): boolean {
  if (!info) return true
  return info.contract.split('/')[0] === EXPECTED_CONTRACT_FAMILY
}
