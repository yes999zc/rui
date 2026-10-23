<script setup lang="ts">
/**
 * The service log, tailed and filtered (docs/console.md P3).
 *
 * Follows the bottom only while the reader is already there: scrolling up to
 * read an error must not be yanked back by the next poll (the old build's
 * atBottom rule, app.js:400-404).
 */
import { computed, nextTick, ref, watch } from 'vue'
import type { PanelBlock } from '../panels'

const props = defineProps<{ block: PanelBlock; data: unknown }>()

const filter = ref('')
const follow = ref(true)
const box = ref<HTMLElement | null>(null)

const lines = computed<string[]>(() => {
  const raw = (props.data as { lines?: unknown } | undefined)?.lines
  const all = Array.isArray(raw) ? raw.map(String) : []
  const needle = filter.value.trim().toLowerCase()
  return needle ? all.filter((line) => line.toLowerCase().includes(needle)) : all
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
  <div class="card">
    <h2>
      日志
      <span v-if="!block.follow" class="tag">不跟随</span>
    </h2>
    <div class="bar">
      <input
        v-if="block.filter !== false"
        v-model="filter"
        class="filter"
        placeholder="过滤（比如 vendor / turn / error）"
        aria-label="过滤日志"
      />
      <label class="check">
        <input v-model="follow" type="checkbox" />
        跟随最新
      </label>
      <span class="count">{{ lines.length }} 行</span>
    </div>
    <pre ref="box" class="log" @scroll="onScroll">{{ lines.join('\n') }}</pre>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
.filter {
  font: inherit; font-size: 12px; padding: 5px 9px; border: 1px solid var(--line);
  border-radius: 8px; flex: 1; min-width: 160px; max-width: 320px;
}
.check { font-size: 12px; color: var(--muted); display: flex; gap: 4px; align-items: center; }
.count { font-size: 11px; color: var(--muted); margin-left: auto; }
.log {
  margin: 0; max-height: 62vh; overflow: auto; background: #f7f8fa;
  border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px;
  font: 11.5px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: pre-wrap; word-break: break-all; color: #24262b;
}
</style>
