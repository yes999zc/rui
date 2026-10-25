<script setup lang="ts">
/**
 * `/v2/logs` (M8) — readable tail of the service log.
 *
 * `GET /api/v1/logs` is a v1 *non-device* transitional endpoint (C-002 §1.2):
 * it returns the last N raw lines and no cursor, so the page never renders the
 * whole fetch at once — it keeps a bounded render window (default 300 lines,
 * "show more" +300) and says how many of how many are on screen. Level
 * filtering runs on the fetched tail; continuation lines (tracebacks) inherit
 * the level of the last timestamped line.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppIcon from '../../components/AppIcon.vue'
import StateBlock from '../components/StateBlock.vue'
import LogLine from '../components/run/LogLine.vue'
import { api, promptForToken } from '../api'
import { copyText } from '../clipboard'
import { describeError } from '../errors'
import { t, type MsgKey } from '../i18n'
import { useResource } from '../useResource'

type LevelFilter = 'all' | 'INFO' | 'WARN' | 'ERROR'

interface ParsedLine {
  raw: string
  level: string
}

const LIMITS = [200, 300, 500, 1000, 2000]
const WINDOW = 300
const POLL_MS = 3000

const FILTERS: Array<{ value: LevelFilter; labelKey: MsgKey }> = [
  { value: 'all', labelKey: 'v2.logs.level.all' },
  { value: 'INFO', labelKey: 'v2.logs.level.info' },
  { value: 'WARN', labelKey: 'v2.logs.level.warn' },
  { value: 'ERROR', labelKey: 'v2.logs.level.error' },
]

const limit = ref(300)
const filter = ref<LevelFilter>('all')
const autoRefresh = ref(true)
const autoScroll = ref(true)
const visibleCount = ref(WINDOW)
const manualBusy = ref(false)
const copiedKey = ref('')
const copyFailedKey = ref('')
const scrollRef = ref<HTMLElement | null>(null)

const logsResource = useResource(() => api.logs(limit.value))
const { data: logPage, error, loading } = logsResource
const errorCopy = computed(() => (error.value ? describeError(error.value) : null))

const rawLines = computed<string[]>(() => logPage.value?.lines ?? [])
const missing = computed(() => Boolean(logPage.value) && logPage.value?.exists === false)
const staleError = computed(() => Boolean(errorCopy.value) && rawLines.value.length > 0)

const TIMESTAMPED = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}[,.]\d+\s+(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL)\b/
const BARE_LEVEL = /^\s*\[?(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL)\]?:?\s/

function normalizeLevel(level: string): string {
  if (level === 'WARNING') return 'WARN'
  if (level === 'CRITICAL') return 'ERROR'
  return level
}

const parsedLines = computed<ParsedLine[]>(() => {
  let current = ''
  const out: ParsedLine[] = []
  for (const source of rawLines.value) {
    // `tail_lines()` uses `readlines()`, so every line keeps its trailing
    // newline. Strip it once here: render, copy-all and copy-line all use `raw`.
    const raw = source.replace(/\r?\n$/, '')
    const stamped = raw.match(TIMESTAMPED)
    const bare = stamped ? null : raw.match(BARE_LEVEL)
    if (stamped) current = normalizeLevel(stamped[1])
    else if (bare) current = normalizeLevel(bare[1])
    out.push({ raw, level: current })
  }
  return out
})

const filteredLines = computed<ParsedLine[]>(() => {
  if (filter.value === 'all') return parsedLines.value
  // ERROR includes CRITICAL and WARN includes WARNING via `normalizeLevel`.
  return parsedLines.value.filter((line) => line.level === filter.value)
})

const visibleLines = computed<ParsedLine[]>(() => {
  const lines = filteredLines.value
  const start = Math.max(0, lines.length - visibleCount.value)
  return lines.slice(start)
})

const hiddenCount = computed(() => Math.max(0, filteredLines.value.length - visibleLines.value.length))
// `tail_lines()` returns at most `limit` lines, so equality is ambiguous (the
// file may be exactly `limit` lines); only a real overflow proves truncation.
// The copy stays conservative ("older lines may not be loaded") either way.
const truncated = computed(() => rawLines.value.length > limit.value)
const emptyFiltered = computed(() => rawLines.value.length > 0 && filteredLines.value.length === 0)
const isEmpty = computed(() => rawLines.value.length === 0)

const pausedNotice = computed(() => !autoScroll.value && visibleLines.value.length > 0)

let timer: number | null = null
let copiedTimer: number | null = null
let restoringScroll = false

function stopTimer(): void {
  if (timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
}

function syncTimer(): void {
  stopTimer()
  if (!autoRefresh.value) return
  timer = window.setInterval(() => {
    if (document.hidden || loading.value) return
    void logsResource.load()
  }, POLL_MS)
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  const element = scrollRef.value
  if (element) element.scrollTop = element.scrollHeight
}

async function load(): Promise<void> {
  await logsResource.load()
  if (autoScroll.value) await scrollToBottom()
}

async function refresh(): Promise<void> {
  manualBusy.value = true
  try {
    await load()
  } finally {
    manualBusy.value = false
  }
}

function setFilter(next: LevelFilter): void {
  filter.value = next
  visibleCount.value = WINDOW
  if (autoScroll.value) void scrollToBottom()
}

async function showMore(): Promise<void> {
  const element = scrollRef.value
  const beforeTop = element?.scrollTop ?? 0
  const beforeHeight = element?.scrollHeight ?? 0
  // Expanding the render window prepends older lines; without an anchor the
  // auto-scroll watcher jumps to the bottom and the newly loaded lines vanish
  // from view. Keep the line the operator was reading where it was.
  restoringScroll = true
  visibleCount.value += WINDOW
  await nextTick()
  if (element) element.scrollTop = beforeTop + (element.scrollHeight - beforeHeight)
  restoringScroll = false
}

function onLimit(event: Event): void {
  const value = Number((event.target as HTMLSelectElement).value)
  if (Number.isFinite(value) && value !== limit.value) {
    limit.value = value
    visibleCount.value = WINDOW
    void logsResource.load()
  }
}

function resetCopyState(): void {
  copiedKey.value = ''
  copyFailedKey.value = ''
  copiedTimer = null
}

function flashCopied(key: string): void {
  copiedKey.value = key
  copyFailedKey.value = ''
  if (copiedTimer !== null) window.clearTimeout(copiedTimer)
  copiedTimer = window.setTimeout(resetCopyState, 1600)
}

function flashCopyFailed(key: string): void {
  copyFailedKey.value = key
  copiedKey.value = ''
  if (copiedTimer !== null) window.clearTimeout(copiedTimer)
  copiedTimer = window.setTimeout(resetCopyState, 2200)
}

async function copyAll(): Promise<void> {
  const text = filteredLines.value.map((line) => line.raw).join('\n')
  if (await copyText(text)) flashCopied('all')
  else flashCopyFailed('all')
}

async function copyLine(index: number): Promise<void> {
  const line = visibleLines.value[index]
  if (!line) return
  if (await copyText(line.raw)) flashCopied(`line:${index}`)
  else flashCopyFailed(`line:${index}`)
}

watch(autoRefresh, syncTimer)
watch(autoScroll, (on) => {
  if (on) void scrollToBottom()
})
watch(
  () => visibleLines.value.length,
  () => {
    if (autoScroll.value && !restoringScroll) void scrollToBottom()
  },
)

onMounted(() => {
  void refresh()
  syncTimer()
})

onBeforeUnmount(() => {
  stopTimer()
  if (copiedTimer !== null) window.clearTimeout(copiedTimer)
})
</script>

<template>
  <div class="v2-page">
    <header class="v2-page-head">
      <div class="v2-page-title">
        <span class="kicker">{{ t('v2.nav.run') }}</span>
        <h1>{{ t('v2.logs.title') }}</h1>
        <p class="lede">{{ t('v2.logs.lede') }}</p>
      </div>
      <div class="v2-page-actions">
        <label class="v2-limit">
          <span class="meta">{{ t('v2.logs.lines') }}</span>
          <select
            class="input v2-limit-select"
            :value="limit"
            :aria-label="t('v2.logs.lines')"
            data-testid="logs-limit"
            @change="onLimit"
          >
            <option v-for="n in LIMITS" :key="n" :value="n">{{ n }}</option>
          </select>
        </label>
        <label class="switch" :title="t('v2.logs.autoRefreshHint')">
          <input v-model="autoRefresh" type="checkbox" data-testid="logs-auto-refresh" />
          <span class="track" aria-hidden="true" />
          <span class="switch-text">{{ t('v2.logs.autoRefresh') }}</span>
        </label>
        <button
          type="button"
          class="btn btn-sm"
          :disabled="manualBusy || loading"
          data-testid="logs-refresh"
          @click="refresh"
        >
          <AppIcon name="refresh" :size="14" />
          {{ manualBusy ? t('v2.logs.reading') : t('v2.common.refresh') }}
        </button>
      </div>
    </header>

    <StateBlock v-if="loading && !logPage" kind="loading" :title="t('v2.common.loading')" />

    <StateBlock
      v-else-if="errorCopy && !logPage"
      kind="error"
      :title="t('v2.logs.loadFailed')"
      :detail="errorCopy.detail"
      :hint="errorCopy.hint"
      :status="errorCopy.status"
      :request-id="errorCopy.requestId"
      show-retry
      @retry="load"
    >
      <button
        v-if="errorCopy?.code === 'unauthorized'"
        type="button"
        class="btn btn-primary"
        @click="promptForToken()"
      >
        {{ t('v2.nav.token') }}
      </button>
    </StateBlock>

    <template v-else-if="logPage">
      <div v-if="staleError" class="banner banner-warn v2-banner" role="status">
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.logs.loadFailed') }}</strong>
          <p v-if="errorCopy" class="note">{{ errorCopy.hint }}</p>
        </div>
        <button type="button" class="btn btn-sm" @click="refresh">
          {{ t('v2.common.retry') }}
        </button>
      </div>

      <div class="v2-run-toolbar">
        <div class="v2-seg" role="group" :aria-label="t('v2.logs.levelFilterAria')">
          <button
            v-for="option in FILTERS"
            :key="option.value"
            type="button"
            class="v2-seg-btn"
            :class="{ 'is-active': filter === option.value }"
            :aria-pressed="filter === option.value"
            :data-testid="`logs-level-${option.value}`"
            @click="setFilter(option.value)"
          >
            {{ t(option.labelKey) }}
          </button>
        </div>
        <label class="switch" :title="t('v2.logs.autoScrollHint')">
          <input v-model="autoScroll" type="checkbox" data-testid="logs-auto-scroll" />
          <span class="track" aria-hidden="true" />
          <span class="switch-text">{{ t('v2.logs.autoScroll') }}</span>
        </label>
        <span v-if="pausedNotice" class="tag is-warn" data-testid="logs-paused">
          <AppIcon name="pause" :size="11" />
          {{ t('v2.logs.paused') }}
        </span>
        <span class="meta v2-run-count" data-testid="logs-count">
          {{ t('v2.logs.filteredCount', { n: filteredLines.length }) }}
        </span>
        <button
          type="button"
          class="btn btn-sm"
          :disabled="!filteredLines.length"
          data-testid="logs-copy-all"
          @click="copyAll"
        >
          <AppIcon
            :name="copiedKey === 'all' ? 'check' : copyFailedKey === 'all' ? 'alert' : 'copy'"
            :size="13"
          />
          {{ copiedKey === 'all'
            ? t('v2.logs.copied')
            : copyFailedKey === 'all'
              ? t('v2.logs.copyFailed')
              : t('v2.logs.copyAll') }}
        </button>
      </div>

      <div class="v2-logs-meta">
        <span class="meta" data-testid="logs-showing">
          {{ t('v2.logs.showing', { n: rawLines.length }) }}
        </span>
        <span v-if="hiddenCount > 0" class="meta">
          {{ t('v2.logs.renderWindow', { shown: visibleLines.length, total: filteredLines.length }) }}
        </span>
        <span v-if="truncated" class="tag is-warn" data-testid="logs-truncated">
          {{ t('v2.logs.truncated', { n: limit }) }}
        </span>
        <span v-if="logPage?.path" class="meta mono v2-break" :title="logPage.path">
          {{ t('v2.logs.source') }}: {{ logPage.path }}
        </span>
      </div>

      <StateBlock
        v-if="missing"
        kind="empty"
        :title="t('v2.logs.missing')"
        :hint="t('v2.logs.missingHint')"
      />
      <StateBlock
        v-else-if="isEmpty"
        kind="empty"
        :title="t('v2.logs.empty')"
        :hint="t('v2.logs.emptyHint')"
      />
      <StateBlock
        v-else-if="emptyFiltered"
        kind="empty"
        :title="t('v2.logs.empty')"
        :hint="t('v2.logs.emptyHint')"
      >
        <button type="button" class="btn btn-sm" @click="setFilter('all')">
          {{ t('v2.logs.level.all') }}
        </button>
      </StateBlock>

      <template v-else>
        <div class="v2-logs-more">
          <button
            v-if="hiddenCount > 0"
            type="button"
            class="btn btn-sm"
            data-testid="logs-show-more"
            @click="showMore"
          >
            <AppIcon name="chevronDown" :size="13" />
            {{ t('v2.logs.showMore', { n: Math.min(WINDOW, hiddenCount) }) }}
          </button>
          <button
            v-if="!autoScroll"
            type="button"
            class="btn btn-sm"
            data-testid="logs-resume"
            @click="autoScroll = true"
          >
            {{ t('v2.logs.resume') }}
          </button>
        </div>
        <div
          ref="scrollRef"
          class="v2-log-view"
          role="log"
          :aria-label="t('v2.logs.title')"
          data-testid="logs-view"
        >
          <LogLine
            v-for="(line, index) in visibleLines"
            :key="`${line.level}-${index}`"
            :raw="line.raw"
            :level="line.level"
            :copied="copiedKey === `line:${index}`"
            :failed="copyFailedKey === `line:${index}`"
            @copy="copyLine(index)"
          />
        </div>
      </template>
    </template>
  </div>
</template>
