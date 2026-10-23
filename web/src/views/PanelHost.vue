<script setup lang="ts">
/**
 * Renders one PanelSpec: polls its source(s) while visible, hands each block its
 * data. The panel's own component escape hatch lands here too (a hand-written
 * view replaces the blocks when a spec declares `component`).
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { usePanels } from '../composables/usePanels'
import BlockStats from '../components/BlockStats.vue'
import BlockNow from '../components/BlockNow.vue'
import BlockActions from '../components/BlockActions.vue'
import BlockList from '../components/BlockList.vue'
import BlockForm from '../components/BlockForm.vue'

const route = useRoute()
const { panels } = usePanels()
const panel = computed(() => panels.value.find(p => p.id === route.params.id))
const main = ref<Record<string, unknown>>({})
const extras = ref<Record<string, unknown>>({})
const error = ref('')
let timer: number | undefined

function dataFor(block: { source?: string }): unknown {
  return block.source ? extras.value[block.source] : main.value
}

async function poll() {
  const p = panel.value
  if (!p || document.hidden) return
  try {
    const [primary, extra] = await Promise.all([
      api.json(p.source),
      p.extra_sources
        ? Promise.all(Object.entries(p.extra_sources).map(async ([k, href]) => [k, await api.json(href)] as const))
        : Promise.resolve([] as const),
    ])
    main.value = primary as Record<string, unknown>
    extras.value = Object.fromEntries(extra)
    error.value = ''
  } catch (exc) {
    // Keep the last good data visible; the banner says what failed (old build's
    // errbox rule: the backend's own words, never a blank page).
    error.value = exc instanceof Error ? exc.message : String(exc)
  }
}

function restart() {
  if (timer) window.clearInterval(timer)
  main.value = {}
  extras.value = {}
  void poll()
  const seconds = panel.value?.refresh_s ?? 5
  if (seconds > 0) timer = window.setInterval(poll, seconds * 1000)
}

onMounted(restart)
watch(() => route.params.id, restart)
onUnmounted(() => { if (timer) window.clearInterval(timer) })
</script>

<template>
  <div v-if="!panel">
    <h1>没有这个面板</h1>
    <p class="sub">注册表里找不到「{{ route.params.id }}」。服务可能刚重启，刷新一次。</p>
  </div>
  <div v-else-if="!panel.enabled" class="card">
    <h1>{{ panel.title }}</h1>
    <p class="sub fail">{{ panel.enabled_reason || '此面板未启用' }}</p>
  </div>
  <template v-else>
    <h1>{{ panel.title }}</h1>
    <p v-if="error" class="sub fail">{{ error }}（显示的是最后一次成功的数据）</p>
    <component :is="null" />
    <template v-for="block in panel.blocks" :key="block.type + (block.title ?? '')">
      <BlockStats v-if="block.type === 'stats'" :block="block" :data="dataFor(block)" />
      <BlockNow v-else-if="block.type === 'now'" :block="block" :data="dataFor(block)" />
      <BlockActions v-else-if="block.type === 'actions'" :block="block" :data="dataFor(block)" :refresh="poll" />
      <BlockList v-else-if="block.type === 'list'" :block="block" :data="dataFor(block)" />
      <BlockForm v-else-if="block.type === 'form'" :block="block" :data="dataFor(block)" :refresh="poll" />
    </template>
  </template>
</template>
