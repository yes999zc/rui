/**
 * The PanelSpec escape hatch (docs/console.md §2).
 *
 * The declarative block vocabulary covers the ordinary 80%; when a panel needs
 * an interaction the data spec cannot express, the backend sets
 * `"component": "<id>"` on the PanelSpec and the renderer lazy-loads the
 * hand-written Vue view registered here. Declaring a component keeps the rest of
 * the spec data-only - it is not a second way to build an ordinary page.
 */
import type { Component } from 'vue'

export const PANEL_COMPONENTS: Record<string, () => Promise<{ default: Component }>> = {
  // The config pages share one catalog-driven editor (docs/config-catalog-contract.md).
  'config-center': () => import('./ConfigCenter.vue'),
  // P6: editing the phrase templates with slot chips, dirty tracking and per-key
  // reset is a sequence, not a block set - the escape hatch's first real user.
  'phrasebook-editor': () => import('./PhrasebookEditor.vue'),
  'device-manager': () => import('./DeviceManager.vue'),
  'mcp-manager': () => import('./McpManager.vue'),
  // The read-only registry, embedded as the 注册工具 tab of /panel/integrations.
  'tools-view': () => import('../views/ToolsView.vue'),
  // Dedicated providers page (LLM/ASR/TTS chains, voice audition, advanced collapsed).
  'providers-v2': () => import('../views/ProvidersView.vue'),
}
