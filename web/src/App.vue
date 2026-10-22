<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from './api'
import { usePanels } from './composables/usePanels'
import { GROUP_TITLES } from './panels'

const version = ref('')
const online = ref(false)
const { panels } = usePanels()

/** Registry panels grouped for the sidebar; disabled ones stay visible, greyed. */
const groups = computed(() => {
  const byGroup = new Map<string, typeof panels.value>()
  for (const p of [...panels.value].sort((a, b) => a.order - b.order)) {
    const list = byGroup.get(p.group) ?? []
    list.push(p)
    byGroup.set(p.group, list)
  }
  return [...byGroup.entries()].map(([group, items]) => ({
    group, title: GROUP_TITLES[group] ?? group, items,
  }))
})

/** Hand-written views that have not migrated into PanelSpecs yet. */
const legacy = [
  { path: '/assistant', label: '助手' },
  { path: '/voice', label: '语音' },
  { path: '/models', label: '模型' },
  { path: '/tools', label: '工具' },
  { path: '/secrets', label: '密钥' },
  { path: '/health', label: '体检' },
  { path: '/logs', label: '日志' },
]

onMounted(async () => {
  try {
    const h = await api.health()
    version.value = h.version
    online.value = true
  } catch {
    online.value = false
  }
})
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">
        house-ear<small>v{{ version || '—' }} · {{ online ? 'control plane online' : 'offline' }}</small>
      </div>
      <nav class="nav">
        <template v-for="g in groups" :key="g.group">
          <div class="nav-group">{{ g.title }}</div>
          <RouterLink
            v-for="p in g.items" :key="p.id" :to="`/panel/${p.id}`"
            :class="{ disabled: !p.enabled }"
          >{{ p.icon ? p.icon + ' ' : '' }}{{ p.title }}</RouterLink>
        </template>
        <div class="nav-group">更多</div>
        <RouterLink v-for="v in legacy" :key="v.path" :to="v.path">{{ v.label }}</RouterLink>
      </nav>
    </aside>
    <main class="main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.nav-group {
  font-size: 11px; color: var(--muted); padding: 14px 10px 4px; letter-spacing: 0.04em;
}
.nav a.disabled { opacity: 0.45; }
</style>
