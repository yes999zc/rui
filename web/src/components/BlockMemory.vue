<script setup lang="ts">
/**
 * The memory panel: what has been remembered, what is waiting, and a place to add
 * a line. Candidates are shown apart from confirmed entries: a turn mentioning a
 * preference must not silently become memory — confirming it is the user's act.
 */
import { computed, ref } from 'vue'
import { api, ApiError } from '../api'
import { localizeBackend, t, type MsgKey } from '../i18n'

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
const confirmed = computed(() => items.value.filter((item) => item.confirmed))
const candidates = computed(() => items.value.filter((item) => !item.confirmed))

const draft = ref('')
const error = ref('')
const busy = ref('')

const SOURCE_KEYS: Record<string, MsgKey> = {
  voice: 'block.memory.source.voice',
  turn: 'block.memory.source.turn',
  console: 'block.memory.source.console',
}
function sourceLabel(source: string): string {
  const key = SOURCE_KEYS[source]
  return key ? t(key) : source
}

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
    if (action === 'add') draft.value = ''
  }
}
</script>

<template>
  <section class="block">
    <div class="block-head">
      <h2>{{ localizeBackend(block.title) || t('block.memory.title') }}</h2>
      <span class="meta">{{ items.length }}</span>
    </div>

    <div v-if="!items.length" class="empty">{{ localizeBackend(block.empty) || t('block.memory.empty') }}</div>

    <template v-else>
      <div class="group-label">{{ t('block.memory.confirmed') }}</div>
      <div v-for="item in confirmed" :key="item.id" class="mem-row">
        <span class="mem-text">{{ item.text }}</span>
        <span class="badge">{{ sourceLabel(item.source) }}</span>
        <button class="btn btn-sm btn-ghost" :disabled="busy === 'remove' + item.id" @click="send('remove', { id: item.id })">
          {{ t('block.memory.delete') }}
        </button>
      </div>

      <template v-if="candidates.length">
        <div class="group-label">{{ t('block.memory.candidates') }}</div>
        <div v-for="item in candidates" :key="item.id" class="mem-row candidate">
          <input v-model="item.text" class="mem-edit" :aria-label="t('block.memory.candidates')" />
          <button class="btn btn-sm btn-primary" :disabled="busy === 'confirm' + item.id" @click="send('confirm', { id: item.id, text: item.text })">
            {{ t('block.memory.confirm') }}
          </button>
          <button class="btn btn-sm btn-ghost" :disabled="busy === 'remove' + item.id" @click="send('remove', { id: item.id })">
            {{ t('block.memory.discard') }}
          </button>
        </div>
      </template>
    </template>

    <div class="mem-add">
      <input
        v-model="draft"
        :placeholder="t('block.memory.addPlaceholder')"
        :aria-label="t('block.memory.add')"
        @keyup.enter="draft.trim() && send('add', { text: draft })"
      />
      <button class="btn btn-primary" :disabled="!draft.trim()" @click="send('add', { text: draft })">
        {{ t('block.memory.add') }}
      </button>
    </div>
    <p v-if="error" class="note fail" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.group-label {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--muted);
  margin: 16px 0 2px;
}

.mem-row {
  display: flex;
  gap: var(--s2);
  align-items: center;
  padding: 8px 0;
  border-top: 1px solid var(--line);
  min-width: 0;
}

.mem-row:first-of-type {
  border-top: 0;
}

.mem-text {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}

.mem-row.candidate {
  background: var(--warn-soft);
  padding-left: 8px;
  padding-right: 8px;
  border-radius: var(--r-xs);
  border-top-color: transparent;
  margin-top: 4px;
}

.mem-edit {
  flex: 1;
  min-width: 0;
  min-height: 30px;
  padding: 4px 8px;
  font-size: 13px;
}

.mem-add {
  display: flex;
  gap: var(--s2);
  margin-top: var(--s4);
}

.mem-add input {
  flex: 1;
}

@media (max-width: 720px) {
  .mem-row {
    flex-wrap: wrap;
  }

  .mem-text {
    flex: 1 1 100%;
  }

  .mem-edit {
    flex: 1 1 100%;
  }
}
</style>
