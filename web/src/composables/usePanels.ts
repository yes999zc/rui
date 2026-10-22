/**
 * The registry singleton: fetch /api/v1/panels once, share the nav with the
 * sidebar, re-fetch on window focus so a redeployed backend's new panels appear
 * without a manual reload.
 */
import { ref } from 'vue'
import { api } from '../api'
import type { PanelSpec } from '../panels'

const panels = ref<PanelSpec[]>([])
const revision = ref('')
let loading: Promise<void> | null = null

async function load(): Promise<void> {
  try {
    const doc = await api.panels()
    panels.value = doc.panels as PanelSpec[]
    revision.value = doc.revision
  } catch {
    // Nav falls back to the legacy views; panels show their own error.
  }
}

export function usePanels() {
  if (!loading) {
    loading = load()
    window.addEventListener('focus', () => { loading = load() })
  }
  return { panels, revision, reload: load }
}
