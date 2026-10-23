<script setup lang="ts">
/**
 * P6: the PhraseBook editor (the PanelSpec escape hatch's first real user).
 *
 * Every sentence the assistant can say lives in the backend's phrase table. This
 * view edits an override layer: defaults stay in code, the console writes
 * data_dir/phrases.yaml. The slot contract is the backend's, not the form's — the
 * save button is only the messenger, and a 422 comes back keyed by phrase.
 */
import { computed, onMounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { api, ApiError, type PhraseDoc, type PhraseKey } from '../api'
import { t, type MsgKey } from '../i18n'

const doc = ref<PhraseDoc | null>(null)
const overrides = ref<Record<string, string>>({})
const filter = ref('')
const showAdvanced = ref(false)
const error = ref('')
const busy = ref('')
const saved = ref('')

const GROUP_KEYS: Record<string, MsgKey> = {
  music: 'phrases.group.music',
  turn: 'phrases.group.turn',
  volume: 'phrases.group.volume',
  memory: 'phrases.group.memory',
  context: 'phrases.group.context',
  device: 'phrases.group.device',
  ha: 'phrases.group.ha',
}

function groupTitle(group: string): string {
  const key = GROUP_KEYS[group]
  return key ? t(key) : group
}

async function load() {
  try {
    const next = await api.phrases()
    apply(next)
    error.value = ''
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  }
}

function apply(next: PhraseDoc) {
  doc.value = next
  overrides.value = { ...next.file_overrides }
  // Show the file's overrides as the starting draft, defaults for everything else.
  for (const key of next.keys) {
    if (!(key.key in overrides.value)) overrides.value[key.key] = key.text
  }
}

const visible = computed<PhraseKey[]>(() => {
  const needle = filter.value.trim().toLowerCase()
  return (doc.value?.keys ?? []).filter((key) => {
    if (key.advanced && !showAdvanced.value) return false
    if (!needle) return true
    return `${key.key} ${key.text} ${key.example}`.toLowerCase().includes(needle)
  })
})

const groups = computed(() => {
  const out: { group: string; title: string; items: PhraseKey[] }[] = []
  for (const key of visible.value) {
    let entry = out.find((item) => item.group === key.group)
    if (!entry) {
      entry = { group: key.group, title: groupTitle(key.group), items: [] }
      out.push(entry)
    }
    entry.items.push(key)
  }
  return out
})

const dirty = computed(() => {
  const changed: string[] = []
  for (const key of doc.value?.keys ?? []) {
    if (overrides.value[key.key] !== key.text) changed.push(key.key)
  }
  return changed
})

function resetKey(key: string) {
  if (!doc.value) return
  const fallback = doc.value.keys.find((item) => item.key === key)?.default ?? ''
  overrides.value[key] = fallback
}

async function save() {
  busy.value = 'save'
  error.value = ''
  saved.value = ''
  try {
    // Send only differences from the defaults: the file stays the override layer,
    // so a future default wording change still reaches every unedited phrase.
    const payload: Record<string, string> = {}
    for (const key of doc.value?.keys ?? []) {
      const value = overrides.value[key.key] ?? key.default
      if (value !== key.default) payload[key.key] = value
    }
    apply(await api.savePhrases(payload))
    saved.value = t('phrases.saved', { n: Object.keys(payload).length })
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : exc instanceof Error ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}

async function resetAll() {
  busy.value = 'reset'
  error.value = ''
  saved.value = ''
  try {
    apply(await api.resetPhrases(null))
    saved.value = t('phrases.restored')
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <p class="lede mgr-intro">{{ t('phrases.lede') }}</p>

  <div v-if="doc" class="phrase-toolbar">
    <label class="sr-only" for="phrase-filter">{{ t('phrases.filter') }}</label>
    <input id="phrase-filter" v-model="filter" type="search" class="phrase-filter" :placeholder="t('phrases.filter')" />
    <label class="phrase-check">
      <input v-model="showAdvanced" type="checkbox" />
      {{ t('phrases.advanced') }}
    </label>
    <span class="meta">{{ t('phrases.count', { shown: visible.length, total: doc.keys.length, overridden: Object.keys(doc.file_overrides).length }) }}</span>
    <div class="actions phrase-actions">
      <button type="button" class="btn btn-primary" :disabled="busy !== '' || !dirty.length" @click="save">
        {{ busy === 'save' ? t('common.saving') : dirty.length ? t('phrases.saveN', { n: dirty.length }) : t('phrases.save') }}
      </button>
      <button type="button" class="btn" :disabled="busy !== ''" @click="resetAll">
        {{ busy === 'reset' ? '…' : t('phrases.resetAll') }}
      </button>
    </div>
  </div>

  <div v-if="saved" class="banner banner-ok" role="status">
    <AppIcon name="check" :size="16" class="banner-icon" />
    <span>{{ saved }}</span>
  </div>
  <div v-if="error" class="banner banner-fail" role="alert">
    <AppIcon name="alert" :size="16" class="banner-icon" />
    <span>{{ error }}</span>
  </div>
  <p v-if="doc?.last_errors.length" class="banner banner-warn" role="status">
    <AppIcon name="alert" :size="16" class="banner-icon" />
    <span>{{ t('phrases.runtimeErrors', { n: doc.last_errors.length }) }}</span>
  </p>
  <p v-if="doc?.file_error" class="banner banner-fail" role="alert">
    <AppIcon name="alert" :size="16" class="banner-icon" />
    <span>{{ t('phrases.fileError', { error: doc.file_error }) }}</span>
  </p>
  <p v-if="doc" class="meta phrase-file">{{ t('phrases.file', { file: doc.file }) }}</p>

  <section v-for="group in groups" :key="group.group" class="block phrase-group">
    <div class="block-head">
      <h2>{{ group.title }}</h2>
      <span class="meta">{{ group.items.length }}</span>
    </div>
    <div v-for="item in group.items" :key="item.key" class="phrase" :class="{ advanced: item.advanced }">
      <div class="phrase-head">
        <code class="phrase-key mono">{{ item.key }}</code>
        <span v-if="item.overridden || overrides[item.key] !== item.default" class="badge badge-accent">
          {{ t('phrases.overridden') }}
        </span>
        <span v-for="slot in item.slots" :key="slot" class="chip slot">{{ '{' + slot + '}' }}</span>
        <button type="button" class="btn btn-sm btn-ghost phrase-reset" :disabled="busy !== ''" @click="resetKey(item.key)">
          {{ t('phrases.restoreKey') }}
        </button>
      </div>
      <label class="sr-only" :for="`phrase-${item.key}`">{{ item.key }}</label>
      <textarea :id="`phrase-${item.key}`" v-model="overrides[item.key]" rows="2" />
      <div class="phrase-example">{{ t('phrases.example', { text: item.example }) }}</div>
    </div>
  </section>

  <p v-if="doc && !visible.length" class="empty">{{ t('phrases.empty') }}</p>
</template>

<style scoped>
.mgr-intro {
  margin-bottom: var(--s5);
}

.phrase-toolbar {
  display: flex;
  align-items: center;
  gap: var(--s3);
  flex-wrap: wrap;
  padding-bottom: var(--s3);
  border-bottom: 1px solid var(--line);
  margin-bottom: var(--s4);
}

.phrase-filter {
  flex: 1;
  min-width: 180px;
  max-width: 340px;
  min-height: 34px;
  padding: 6px 9px;
  font-size: 12.5px;
}

.phrase-check {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--ink-2);
  cursor: pointer;
}

.phrase-check input {
  width: 14px;
  height: 14px;
  min-height: 0;
  padding: 0;
}

.phrase-actions {
  margin-left: auto;
}

.phrase-file {
  margin-bottom: var(--s3);
  overflow-wrap: anywhere;
}

.phrase-group {
  margin-top: var(--s6);
}

.phrase {
  padding: 12px 0;
  border-top: 1px solid var(--line);
}

.phrase:first-of-type {
  border-top: 1px solid var(--line-strong);
}

.phrase.advanced textarea {
  background: var(--surface-2);
}

.phrase-head {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  margin-bottom: 6px;
}

.phrase-key {
  font-size: 11.5px;
  color: var(--muted);
}

.phrase-reset {
  margin-left: auto;
}

.slot {
  font-size: 10.5px;
  color: var(--ink-2);
}

.phrase textarea {
  width: 100%;
  font-size: 13px;
}

.phrase-example {
  margin-top: 5px;
  font-size: 11.5px;
  color: var(--muted);
  overflow-wrap: anywhere;
}

@media (max-width: 720px) {
  .phrase-filter {
    flex: 1 1 100%;
    max-width: none;
  }

  .phrase-actions {
    margin-left: 0;
    width: 100%;
  }

  .phrase-actions .btn {
    flex: 1;
  }
}
</style>
