<script setup lang="ts">
/**
 * The service log, tailed and filtered (docs/console.md P3).
 *
 * Follows the bottom only while the reader is already there: scrolling up to read
 * an error must not be yanked back by the next poll (the old build's atBottom rule).
 */
import { computed, nextTick, ref, watch } from 'vue'
import type { PanelBlock } from '../panels'
import { localizeBackend, t } from '../i18n'

const props = defineProps<{ block: PanelBlock; data: unknown }>()

const filter = ref('')
const follow = ref(true)
const box = ref<HTMLElement | null>(null)
/** Line noise the spec asks to hide by default (the plane's level line every 2 s). */
const hideNoise = ref(props.block.hide != null)

const all = computed<string[]>(() => {
  const raw = (props.data as { lines?: unknown } | undefined)?.lines
  return Array.isArray(raw) ? raw.map(String) : []
})
const hiddenCount = computed(() => {
  const pattern = props.block.hide
  if (!pattern) return 0
  return all.value.filter((line) => line.includes(pattern)).length
})
const lines = computed<string[]>(() => {
  const pattern = props.block.hide
  const visible = hideNoise.value && pattern
    ? all.value.filter((line) => !line.includes(pattern))
    : all.value
  const needle = filter.value.trim().toLowerCase()
  return needle ? visible.filter((line) => line.toLowerCase().includes(needle)) : visible
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
    <div class="block-head">
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
      <label class="log-check">
        <input v-model="follow" type="checkbox" />
        {{ t('block.follow') }}
      </label>
      <label v-if="block.hide" class="log-check">
        <input v-model="hideNoise" type="checkbox" />
        {{ localizeBackend(block.hide_label) || t('block.hideNoise') }}<template v-if="hiddenCount">（{{ hiddenCount }}）</template>
      </label>
    </div>
    <pre ref="box" class="terminal" @scroll="onScroll">{{ lines.join('\n') }}</pre>
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
</style>
