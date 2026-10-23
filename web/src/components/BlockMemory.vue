<script setup lang="ts">
/**
 * The memory panel: what has been remembered, what is waiting, and a place to
 * add a line (docs/console.md P2's block registry, first CRUD surface).
 *
 * Candidates are shown apart from confirmed entries: a turn mentioning a
 * preference must not silently become memory - confirming it is the user's act.
 */
import { computed, ref } from 'vue'
import { api, ApiError } from '../api'

const props = defineProps<{ block: { title?: string; empty?: string }; data: unknown; refresh: () => void }>()

interface Item {
  id: string
  text: string
  confirmed: boolean
  source: string
}

const items = computed<Item[]>(() => {
  const raw = (props.data as { items?: unknown } | undefined)?.items
  return Array.isArray(raw) ? (raw as Item[]) : []
})
const confirmed = computed(() => items.value.filter((i) => i.confirmed))
const candidates = computed(() => items.value.filter((i) => !i.confirmed))

const draft = ref('')
const error = ref('')
const busy = ref('')

async function send(action: string, payload: Record<string, unknown>) {
  busy.value = action + String(payload.id ?? '')
  error.value = ''
  try {
    await api.post('/api/v1/memory', { action, ...payload })
    props.refresh()
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
    draft.value = action === 'add' ? '' : draft.value
  }
}
</script>

<template>
  <div class="card">
    <h2>{{ block.title || '记住的事' }}</h2>

    <div v-if="!items.length" class="empty">{{ block.empty || '还没有记住任何事。' }}</div>

    <template v-else>
      <div class="group-label">已记住 · 每轮都会带上</div>
      <div v-for="item in confirmed" :key="item.id" class="row">
        <span class="v">{{ item.text }}</span>
        <span class="tag">{{ item.source }}</span>
        <button class="small" :disabled="busy === 'remove' + item.id"
                @click="send('remove', { id: item.id })">删除</button>
      </div>

      <template v-if="candidates.length">
        <div class="group-label">候选 · 确认后才会用</div>
        <div v-for="item in candidates" :key="item.id" class="row candidate">
          <input v-model="item.text" class="edit" aria-label="编辑候选" />
          <button class="small primary" :disabled="busy === 'confirm' + item.id"
                  @click="send('confirm', { id: item.id, text: item.text })">确认</button>
          <button class="small" :disabled="busy === 'remove' + item.id"
                  @click="send('remove', { id: item.id })">丢弃</button>
        </div>
      </template>
    </template>

    <div class="add">
      <input v-model="draft" placeholder="添加一条记忆（比如：我喜欢安静的歌）"
             aria-label="添加记忆" @keyup.enter="draft.trim() && send('add', { text: draft })" />
      <button :disabled="!draft.trim()" class="primary" @click="send('add', { text: draft })">添加</button>
    </div>
    <div v-if="error" class="note fail">{{ error }}</div>
  </div>
</template>

<style scoped>
.group-label { font-size: 11px; color: var(--muted); margin: 10px 0 4px; letter-spacing: 0.04em; }
.row { display: flex; gap: 8px; align-items: center; padding: 6px 0; border-bottom: 1px solid #f2f3f6; }
.row .v { flex: 1; }
.row.candidate { background: #fcfaf4; }
.edit {
  flex: 1; font: inherit; font-size: 13px; padding: 4px 8px;
  border: 1px solid var(--line); border-radius: 6px; min-width: 0;
}
button.small { font-size: 12px; padding: 3px 9px; }
.add { display: flex; gap: 8px; margin-top: 12px; }
.add input {
  flex: 1; font: inherit; font-size: 13px; padding: 6px 9px;
  border: 1px solid var(--line); border-radius: 8px;
}
</style>
