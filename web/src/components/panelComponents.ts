/**
 * The PanelSpec escape hatch (docs/console.md §2).
 *
 * The declarative block vocabulary covers the ordinary 80%; when a panel needs
 * an interaction the data spec cannot express, the backend sets
 * `"component": "<id>"` on the PanelSpec and the renderer lazy-loads the
 * hand-written Vue view registered here. Declaring a component keeps the rest of
 * the spec data-only - it is not a second way to build an ordinary page.
 *
 * Nothing is registered yet: every current panel is covered by the blocks, and
 * the registry exists so the escape hatch is real when the first one needs it.
 */
import type { Component } from 'vue'

export const PANEL_COMPONENTS: Record<string, () => Promise<{ default: Component }>> = {
  // P6: editing 49 templates with slot chips, dirty tracking and per-key reset is a
  // sequence, not a block set - the escape hatch's first real user.
  'phrasebook-editor': () => import('./PhrasebookEditor.vue'),
  'device-manager': () => import('./DeviceManager.vue'),
  'mcp-manager': () => import('./McpManager.vue'),
}
