/**
 * Appearance (docs/console-redesign.md §4.1): system / light / dark, remembered
 * per browser. The inline bootstrap in index.html applies the same value before
 * first paint so the console never flashes the wrong theme.
 */
import { ref } from 'vue'

export type ThemePref = 'auto' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'house-ear-theme'

function readPreference(): ThemePref {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'light' || saved === 'dark' || saved === 'auto') return saved
  } catch {
    /* private mode */
  }
  return 'auto'
}

export const themePref = ref<ThemePref>(readPreference())
export const resolvedTheme = ref<ResolvedTheme>('light')

const query = typeof window !== 'undefined' && window.matchMedia
  ? window.matchMedia('(prefers-color-scheme: dark)')
  : null

function resolve(): ResolvedTheme {
  if (themePref.value === 'light' || themePref.value === 'dark') return themePref.value
  return query?.matches ? 'dark' : 'light'
}

export function applyTheme(): void {
  resolvedTheme.value = resolve()
  document.documentElement.dataset.theme = resolvedTheme.value
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', resolvedTheme.value === 'dark' ? '#131312' : '#f4f1ea')
}

export function setThemePref(next: ThemePref): void {
  themePref.value = next
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    /* private mode */
  }
  applyTheme()
}

export function initTheme(): void {
  applyTheme()
  query?.addEventListener('change', () => {
    if (themePref.value === 'auto') applyTheme()
  })
}

export function cycleTheme(): void {
  setThemePref(themePref.value === 'auto' ? 'light' : themePref.value === 'light' ? 'dark' : 'auto')
}
