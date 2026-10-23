/**
 * The registry singleton: fetch /api/v1/panels once, share the nav with the
 * sidebar, re-fetch on window focus so a redeployed backend's new panels appear
 * without a manual reload. `ready` and `error` let the shell distinguish "still
 * loading" from "this panel really does not exist".
 */
import { ref } from 'vue'
import { api } from '../api'
import type { PanelSpec } from '../panels'

const panels = ref<PanelSpec[]>([])
const revision = ref('')
const ready = ref(false)
const error = ref('')
let loading: Promise<void> | null = null

async function load(): Promise<void> {
  try {
    const doc = await api.panels()
    panels.value = doc.panels as PanelSpec[]
    revision.value = doc.revision
    error.value = ''
  } catch (exc) {
    // Nav falls back to the hand-written pages; panels show their own error.
    error.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    ready.value = true
  }
}

export function usePanels() {
  if (!loading) {
    loading = load()
    window.addEventListener('focus', () => { loading = load() })
  }
  return { panels, revision, ready, error, reload: load }
}
