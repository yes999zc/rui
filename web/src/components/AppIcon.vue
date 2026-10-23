<script setup lang="ts">
/**
 * One coherent icon language (docs/console-redesign.md §4.4): 24px grid,
 * 1.6 stroke, round caps. The backend's PanelSpec sends a decorative unicode
 * glyph; the shell maps it to one of these instead of rendering emoji-ish text.
 */
import { computed } from 'vue'

const props = withDefaults(defineProps<{ name: string; size?: number; strokeWidth?: number }>(), {
  size: 18,
  strokeWidth: 1.6,
})

const ICONS: Record<string, string[]> = {
  menu: ['M4 7h16', 'M4 12h16', 'M4 17h16'],
  close: ['M6 6l12 12', 'M18 6L6 18'],
  sun: ['M12 4v2', 'M12 18v2', 'M4 12h2', 'M18 12h2', 'M6.3 6.3l1.4 1.4', 'M16.3 16.3l1.4 1.4', 'M17.7 6.3l-1.4 1.4', 'M7.7 16.3l-1.4 1.4', 'M12 8a4 4 0 100 8 4 4 0 000-8z'],
  moon: ['M20 14.5A8.5 8.5 0 116.5 4a7 7 0 0013.5 10.5z'],
  monitor: ['M4 5h16v11H4z', 'M9 20h6', 'M12 16v4'],
  globe: ['M12 3a9 9 0 100 18 9 9 0 000-18z', 'M3 12h18', 'M12 3c2.5 2.4 4 5.6 4 9s-1.5 6.6-4 9c-2.5-2.4-4-5.6-4-9s1.5-6.6 4-9z'],
  key: ['M15 3a6 6 0 00-5.2 9L3 18.8V21h2.2l1.2-1.2h2l1.4-1.4h1.6L15 15a6 6 0 100-12z', 'M17 7h.01'],
  activity: ['M3 12h4l3-8 4 16 3-8h4'],
  compass: ['M12 3a9 9 0 100 18 9 9 0 000-18z', 'M15.5 8.5l-2 5-5 2 2-5 5-2z'],
  speaker: ['M4 9h3l4-3v12l-4-3H4z', 'M15 9a4 4 0 010 6', 'M17.5 6.5a8 8 0 010 11'],
  gauge: ['M12 14l4-4', 'M4.5 19a9 9 0 1115 0', 'M12 19h.01'],
  chat: ['M4 5h16v11H9l-5 4z'],
  list: ['M8 6h12', 'M8 12h12', 'M8 18h12', 'M4 6h.01', 'M4 12h.01', 'M4 18h.01'],
  note: ['M9 18V5l11-2v13', 'M9 18a3 3 0 11-6 0 3 3 0 016 0z', 'M20 16a3 3 0 11-6 0 3 3 0 016 0z'],
  bookmark: ['M6 4h12v17l-6-4-6 4z'],
  sliders: ['M4 8h10', 'M18 8h2', 'M4 16h4', 'M12 16h8', 'M14 6v4', 'M8 14v4'],
  plug: ['M9 3v6', 'M15 3v6', 'M6 9h12v4a6 6 0 01-12 0z', 'M12 19v3'],
  wrench: ['M14.5 6.5a4 4 0 105 5L11 20l-3-3 8.5-8.5z', 'M4 4l3 3'],
  layers: ['M12 3l9 5-9 5-9-5z', 'M3 13l9 5 9-5'],
  link: ['M9 15l6-6', 'M10 6l1.5-1.5a4 4 0 016 6L16 12', 'M14 18l-1.5 1.5a4 4 0 01-6-6L8 12'],
  quote: ['M7 7h4v4c0 3-2 5-4 5', 'M15 7h4v4c0 3-2 5-4 5'],
  gear: ['M12 9a3 3 0 100 6 3 3 0 000-6z', 'M12 2l1.2 3.2L16 4l1.6 3.3 3.4.6-1.4 3.1 1.4 3.1-3.4.6L16 18l-2.8-1.2L12 20l-1.2-3.2L8 18l-1.6-3.3-3.4-.6 1.4-3.1-1.4-3.1 3.4-.6L8 4l2.8 1.2z'],
  refresh: ['M20 11a8 8 0 10-2.3 5.7', 'M20 4v7h-7'],
  copy: ['M9 9h11v11H9z', 'M5 15V4h11'],
  check: ['M4 12l5 5L20 6'],
  alert: ['M12 3l9 16H3z', 'M12 9v4', 'M12 16h.01'],
  lock: ['M6 11h12v9H6z', 'M9 11V8a3 3 0 016 0v3'],
  arrowRight: ['M4 12h16', 'M14 6l6 6-6 6'],
  play: ['M8 5l11 7-11 7z'],
  pause: ['M8 5v14', 'M16 5v14'],
  skipBack: ['M6 5v14', 'M19 5l-9 7 9 7z'],
  skipForward: ['M18 5v14', 'M5 5l9 7-9 7z'],
  stop: ['M6 6h12v12H6z'],
  volume: ['M4 9h3l4-3v12l-4-3H4z', 'M15 9a4 4 0 010 6'],
  external: ['M14 4h6v6', 'M20 4l-9 9', 'M18 14v6H4V6h6'],
  plus: ['M12 5v14', 'M5 12h14'],
  trash: ['M4 7h16', 'M9 7V4h6v3', 'M6 7l1 14h10l1-14', 'M10 11v6', 'M14 11v6'],
  dot: ['M12 12h.01'],
  chevronDown: ['M6 9l6 6 6-6'],
  terminal: ['M4 5h16v14H4z', 'M8 10l2 2-2 2', 'M13 14h3'],
  clock: ['M12 3a9 9 0 100 18 9 9 0 000-18z', 'M12 7v5l3 2'],
  download: ['M12 4v11', 'M7 11l5 5 5-5', 'M4 20h16'],
}

/** PanelSpec icon glyphs (backend copy) → icon names. `.` fallback. */
const GLYPHS: Record<string, string> = {
  '◎': 'gauge',
  '⌸': 'speaker',
  '☰': 'chat',
  '⚙': 'sliders',
  '≡': 'list',
  '◇': 'bookmark',
  '♪': 'note',
  '⌗': 'link',
  Aa: 'quote',
}

const d = computed(() => ICONS[props.name] ?? GLYPHS[props.name] ?? ICONS.dot)
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="strokeWidth"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path v-for="(path, i) in d" :key="i" :d="path" />
  </svg>
</template>
