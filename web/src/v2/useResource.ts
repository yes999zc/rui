import { ref, type Ref } from 'vue'

export interface Resource<T> {
  data: Ref<T | null>
  error: Ref<unknown>
  loading: Ref<boolean>
  load: () => Promise<void>
  /** Drop the current value/error and invalidate any in-flight request. */
  reset: () => void
}

/**
 * Minimal async resource for the read-only M1 pages: latest request wins, so a
 * fast refresh after a slow one cannot overwrite fresher data. Retries are the
 * caller's job (`load` is exposed unchanged).
 */
export function useResource<T>(loader: () => Promise<T>): Resource<T> {
  const data = ref(null) as Ref<T | null>
  const error = ref<unknown>(null)
  const loading = ref(false)
  let sequence = 0

  async function load(): Promise<void> {
    const mine = ++sequence
    loading.value = true
    error.value = null
    try {
      const value = await loader()
      if (mine !== sequence) return
      data.value = value
    } catch (err) {
      if (mine !== sequence) return
      error.value = err
    } finally {
      if (mine === sequence) loading.value = false
    }
  }

  function reset(): void {
    sequence += 1
    data.value = null
    error.value = null
    loading.value = false
  }

  return { data, error, loading, load, reset }
}
