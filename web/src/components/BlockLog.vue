<script setup lang="ts">
/**
 * The service log, tailed and filtered (docs/console.md P3).
 *
 * Follows the bottom only while the reader is already there: scrolling up to read
 * an error must not be yanked back by the next poll (the old build's atBottom rule).
 *
 * Levels are parsed from the standard log line (`... INFO module: message`) so the
 * default view can show only WARN/ERROR. Third-party INFO (httpx per request) used
 * to be 95% of the tail; reading the log should start with the things that broke.
 */
import { computed, nextTick, ref, watch } from 'vue'
import type { PanelBlock } from '../panels'
import { localizeBackend, t } from '../i18n'

const props = defineProps<{ block: PanelBlock; data: unknown; hideTitle?: boolean }>()

type Level = 'debug' | 'info' | 'warn' | 'error' | 'other'
type LevelMode = 'problems' | 'all'

const LEVEL_RE = /\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\b/g
const LEVEL_RANK: Record<Level, number> = { other: 0, debug: 0, info: 1, warn: 2, error: 3 }

/** The whole line counts: nested plane logs put a WARNING after their outer INFO. */
function levelOf(line: string): Level {
  let worst: Level = 'other'
  for (const match of line.matchAll(LEVEL_RE)) {
    let level: Level
    switch (match[1]) {
      case 'DEBUG':
        level = 'debug'
        break
      case 'INFO':
        level = 'info'
        break
      case 'WARNING':
      case 'WARN':
        level = 'warn'
        break
      case 'ERROR':
      case 'CRITICAL':
      case 'FATAL':
        level = 'error'
        break
      default:
        level = 'other'
    }
    if (LEVEL_RANK[level] > LEVEL_RANK[worst]) worst = level
  }
  return worst
}

const filter = ref('')
const follow = ref(true)
const levelMode = ref<LevelMode>('problems')
const box = ref<HTMLElement | null>(null)
/** Line noise the spec asks to hide by default (the plane's level line every 2 s). */
const hideNoise = ref(props.block.hide != null)

const all = computed<string[]>(() => {
  const raw = (props.data as { lines?: unknown } | undefined)?.lines
  return Array.isArray(raw) ? raw.map((line) => String(line).replace(/\s+$/, '')) : []
})
const hiddenCount = computed(() => {
  const pattern = props.block.hide
  if (!pattern) return 0
  return all.value.filter((line) => line.includes(pattern)).length
})

/** Everything after the spec's noise filter; levels and search build on this. */
const tagged = computed(() => {
  const pattern = props.block.hide
  const visible = hideNoise.value && pattern
    ? all.value.filter((line) => !line.includes(pattern))
    : all.value
  return visible.map((line) => ({ line, level: levelOf(line) }))
})

const levelCounts = computed(() => {
  const counts = { debug: 0, info: 0, warn: 0, error: 0, other: 0 }
  for (const item of tagged.value) counts[item.level] += 1
  return counts
})

const problemCount = computed(() => levelCounts.value.warn + levelCounts.value.error)
const baseCount = computed(() => tagged.value.length)

const lines = computed<string[]>(() => {
  const needle = filter.value.trim().toLowerCase()
  const selected = levelMode.value === 'problems'
    ? tagged.value.filter((item) => item.level === 'warn' || item.level === 'error')
    : tagged.value
  return (needle ? selected.filter((item) => item.line.toLowerCase().includes(needle)) : selected)
    .map((item) => item.line)
})

function atBottom(): boolean {
  const el = box.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < 24
}

watch(lines, async () => {
  if (!follow.value) return
  await nextTick()
  const el = box.value
  if (el) el.scrollTop = el.scrollHeight
})

function onScroll() {
  follow.value = atBottom()
}
</script>

<template>
  <section class="block">
    <div v-if="!hideTitle" class="block-head">
      <h2>
        {{ localizeBackend(block.title) || t('block.log') }}
        <span v-if="!block.follow" class="badge">{{ t('block.logNoFollow') }}</span>
      </h2>
      <span class="meta">{{ t('block.lines', { n: lines.length }) }}</span>
    </div>
    <div class="log-bar">
      <input
        v-if="block.filter !== false"
        v-model="filter"
        class="log-filter"
        type="search"
        :placeholder="t('block.filterPlaceholder')"
        :aria-label="t('block.filterAria')"
      />
      <div class="log-levels" role="group" :aria-label="t('block.logLevelFilter')">
        <button
          type="button"
          :class="{ on: levelMode === 'problems' }"
          :aria-pressed="levelMode === 'problems'"
          @click="levelMode = 'problems'"
        >
          {{ t('block.logProblems') }}
          <span class="log-count" :class="{ 'has-problems': problemCount > 0 }">{{ problemCount }}</span>
        </button>
        <button type="button" :class="{ on: levelMode === 'all' }" :aria-pressed="levelMode === 'all'" @click="levelMode = 'all'">
          {{ t('block.logAll') }}
          <span class="log-count">{{ baseCount }}</span>
        </button>
      </div>
      <span class="log-meta meta">{{ t('block.logCounts', { warn: levelCounts.warn, error: levelCounts.error }) }}</span>
      <label class="log-check">
        <input v-model="follow" type="checkbox" />
        {{ t('block.follow') }}
      </label>
      <label v-if="block.hide" class="log-check">
        <input v-model="hideNoise" type="checkbox" />
        {{ localizeBackend(block.hide_label) || t('block.hideNoise') }}<template v-if="hiddenCount">（{{ hiddenCount }}）</template>
      </label>
    </div>
    <p v-if="!lines.length" class="log-empty">
      {{ levelMode === 'problems' ? t('block.logNoProblems', { n: baseCount }) : t('block.emptyList') }}
      <button v-if="levelMode === 'problems' && baseCount" type="button" class="btn btn-sm" @click="levelMode = 'all'">
        {{ t('block.logShowAll') }}
      </button>
    </p>
    <pre v-else ref="box" class="terminal" @scroll="onScroll">{{ lines.join('\n') }}</pre>
  </section>
</template>

<style scoped>
.log-bar {
  display: flex;
  gap: var(--s3);
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: var(--s2);
}

.log-filter {
  flex: 1;
  min-width: 160px;
  max-width: 340px;
  min-height: 32px;
  padding: 5px 9px;
  font-size: 12px;
}

.log-levels {
  display: inline-flex;
  padding: 2px;
  gap: 2px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface);
}

.log-levels button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 0;
  background: transparent;
  color: var(--muted);
  padding: 4px 8px;
  border-radius: var(--r-xs);
  font-size: 11.5px;
  cursor: pointer;
}

.log-levels button:hover {
  color: var(--ink);
}

.log-levels button.on {
  background: var(--ink);
  color: var(--bg);
}

.log-count {
  font-family: var(--font-mono);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  opacity: 0.8;
}

.log-count.has-problems {
  color: var(--fail);
}

.log-levels button.on .log-count.has-problems {
  color: var(--bg);
  opacity: 0.9;
}

.log-meta {
  font-size: 10.5px;
  white-space: nowrap;
}

.log-check {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--ink-2);
  cursor: pointer;
}

.log-check input {
  width: 14px;
  height: 14px;
  min-height: 0;
  padding: 0;
}

.log-empty {
  display: flex;
  align-items: center;
  gap: var(--s3);
  flex-wrap: wrap;
  margin: 0;
  padding: var(--s4) 0;
  color: var(--muted);
  font-size: 12.5px;
}

@media (max-width: 720px) {
  .log-filter {
    flex: 1 1 100%;
    max-width: none;
  }

  .log-levels button {
    min-height: 44px;
    padding-inline: 10px;
  }

  .log-check {
    min-height: 44px;
  }

  /* Logs keep their alignment on a phone: scroll sideways, don't shred URLs. */
  .terminal {
    white-space: pre;
    word-break: normal;
    overflow-wrap: normal;
    overflow-x: auto;
  }
}
</style>
