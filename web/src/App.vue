<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, token } from './api'
import { usePanels } from './composables/usePanels'
import { GROUP_TITLES } from './panels'

const version = ref('')
const online = ref(false)
const { panels } = usePanels()
const needToken = ref(false)
const tokenDraft = ref(token.get())
function saveToken() {
  token.set(tokenDraft.value.trim())
  needToken.value = false
  window.location.reload()
}
onMounted(() => {
  window.addEventListener('house-ear-401', () => { needToken.value = true })
  if (token.get() !== tokenDraft.value) tokenDraft.value = token.get()
})

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

/**
 * Hand-written views that have not migrated into PanelSpecs yet: read-only
 * reference pages whose value is the curated prose, not a form.
 */
const legacy = [
  { path: '/voice', label: '语音' },
  { path: '/models', label: '模型' },
  { path: '/tools', label: '工具' },
  { path: '/secrets', label: '密钥' },
  { path: '/diagnostics', label: '体检' },
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
      <div class="token-box" :class="{ attention: needToken }">
        <div class="token-label">{{ needToken ? '需要访问令牌（401）' : '访问令牌（可选）' }}</div>
        <input v-model="tokenDraft" type="password" placeholder="X-Auth-Token" @keyup.enter="saveToken" />
        <button :disabled="!tokenDraft.trim()" @click="saveToken">保存</button>
      </div>
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
.token-box {
  margin-top: 18px; padding: 10px; border-top: 1px solid var(--line);
  display: flex; flex-wrap: wrap; gap: 6px;
}
.token-box.attention { border-top-color: var(--fail); }
.token-label { width: 100%; font-size: 11px; color: var(--muted); }
.token-box.attention .token-label { color: var(--fail); }
.token-box input { flex: 1; min-width: 0; font-size: 12px; padding: 4px 7px; }
.token-box button { font-size: 12px; padding: 4px 8px; }
</style>
