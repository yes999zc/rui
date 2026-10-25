/**
 * The one frontend-owned piece of console navigation (docs/console-ia-audit.md §3/§5).
 *
 * Phase 4 made the backend PanelSpec `nav` manifest authoritative:
 * `{section, order, hidden, icon}` decides where every registry page appears, so
 * the rail is built from /api/v1/panels with no id→group maps here. This file only
 * adds what the registry cannot declare: the two section headings and the three
 * hand-written utility destinations (channels, diagnostics, first-run setup).
 * Add/remove an entry in `UTILITY_ROUTES` and the rail follows.
 */
import type { MsgKey } from './i18n'
import type { PanelSection, PanelSpec } from './panels'

/** Render order of the two sections; the backend never emits a third one. */
export const SECTION_ORDER: readonly PanelSection[] = ['run', 'configure']

const SECTION_TITLES: Record<PanelSection, MsgKey> = {
  run: 'nav.run',
  configure: 'nav.configure',
}

export function sectionTitle(section: PanelSection): MsgKey {
  return SECTION_TITLES[section]
}

/** A hand-written route that is not a backend panel. */
export interface UtilityRoute {
  path: string
  labelKey: MsgKey
  icon: string
  section: PanelSection
  /** Render directly after this backend panel id; append to the section if absent. */
  after: string
  /** First-run only: hidden once GET /api/v1/setup/step reports complete. */
  setupOnly?: boolean
}

/**
 * Hand-written destinations. `/secrets` is deliberately absent: it is a write-only
 * credential ledger linked from the pages that consume keys, not a daily destination
 * (IA audit §2.3). `/setup` disappears once the wizard passes.
 */
export const UTILITY_ROUTES: readonly UtilityRoute[] = [
  { path: '/channels', labelKey: 'nav.channels', icon: 'chat', section: 'configure', after: 'integrations' },
  { path: '/diagnostics', labelKey: 'nav.diagnostics', icon: 'activity', section: 'configure', after: 'integrations' },
  { path: '/setup', labelKey: 'nav.setup', icon: 'compass', section: 'configure', after: 'system', setupOnly: true },
]

export type NavItem =
  | { kind: 'panel'; key: string; panel: PanelSpec }
  | { kind: 'route'; key: string; path: string; labelKey: MsgKey; icon: string }

export interface NavGroup {
  id: PanelSection
  title: MsgKey
  items: NavItem[]
}

function routeItem(route: UtilityRoute): NavItem {
  return {
    kind: 'route',
    key: `route:${route.path}`,
    path: route.path,
    labelKey: route.labelKey,
    icon: route.icon,
  }
}

/**
 * Build the rail from the registry manifest.
 *
 * - `nav.hidden` panels are skipped (credentials stays reachable by URL);
 * - the section order and the per-section order come from the backend
 *   (`nav.order`, id as a deterministic tie-break);
 * - a panel this build does not know still appears: the backend already assigned
 *   it a section/order, so there is no "append to configure" guess anymore;
 * - utilities are placed after their anchor panel and appended when the anchor is
 *   absent, so a backend rename cannot silently drop them.
 */
export function buildNav(panels: PanelSpec[], setupComplete: boolean): NavGroup[] {
  const utilities = UTILITY_ROUTES.filter((route) => !route.setupOnly || !setupComplete)
  const groups: NavGroup[] = []
  for (const section of SECTION_ORDER) {
    const sectionPanels = panels
      .filter((panel) => !panel.nav.hidden && panel.nav.section === section)
      .sort((a, b) => a.nav.order - b.nav.order || a.id.localeCompare(b.id))
    const items: NavItem[] = []
    const placed = new Set<string>()
    for (const panel of sectionPanels) {
      items.push({ kind: 'panel', key: `panel:${panel.id}`, panel })
      for (const route of utilities) {
        if (route.section === section && route.after === panel.id) {
          items.push(routeItem(route))
          placed.add(route.path)
        }
      }
    }
    for (const route of utilities) {
      if (route.section === section && !placed.has(route.path)) items.push(routeItem(route))
    }
    if (items.length) groups.push({ id: section, title: SECTION_TITLES[section], items })
  }
  return groups
}
