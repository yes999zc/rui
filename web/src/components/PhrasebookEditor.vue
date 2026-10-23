<script setup lang="ts">
/**
 * P6: the PhraseBook editor (the PanelSpec escape hatch's first real user).
 *
 * Every sentence the assistant can say lives in the backend's phrase table. This
 * view edits an override layer: defaults stay in code, the console writes
 * data_dir/phrases.yaml. The slot contract is the backend's, not the form's - the
 * save button is only the messenger, and a 422 comes back keyed by phrase.
 */
import { computed, onMounted, ref } from 'vue'
import { api, ApiError, type PhraseDoc, type PhraseKey } from '../api'

const doc = ref<PhraseDoc | null>(null)
const overrides = ref<Record<string, string>>({})
const filter = ref('')
const showAdvanced = ref(false)
const error = ref('')
const busy = ref('')
const saved = ref('')

const GROUP_TITLES: Record<string, string> = {
  music: '音乐', turn: '回合', volume: '音量', memory: '记忆',
  context: '上下文', device: '设备', ha: 'Home Assistant（高级）',
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
      entry = { group: key.group, title: GROUP_TITLES[key.group] ?? key.group, items: [] }
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
    // Send only the differences from the defaults: the file stays the override layer,
    // so a future default wording change still reaches every unedited phrase.
    const payload: Record<string, string> = {}
    for (const key of doc.value?.keys ?? []) {
      const value = overrides.value[key.key] ?? key.default
      if (value !== key.default) payload[key.key] = value
    }
    apply(await api.savePhrases(payload))
    saved.value = `已保存 ${Object.keys(payload).length} 条覆盖；下一句话立即生效`
  } catch (exc) {
    if (exc instanceof ApiError) {
      const detail = exc.message
      error.value = typeof detail === 'string' ? detail : JSON.stringify(detail)
    } else {
      error.value = String(exc)
    }
  } finally {
    busy.value = ''
  }
}

async function resetAll() {
  busy.value = 'reset'
  error.value = ''
  try {
    apply(await api.resetPhrases(null))
    saved.value = '已全部恢复默认'
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <p class="sub">
    助手会说的每一句都在这里。改的是覆盖层（data 卷的 phrases.yaml），默认文案留在代码里；
    <strong>保存后下一句话立即生效</strong>，不用重启。花括号里的槽位是硬约束：可以改措辞、换顺序，不能删掉。
  </p>

  <div v-if="doc" class="card">
    <div class="bar">
      <input v-model="filter" class="filter" placeholder="搜索话术（键名 / 文案 / 示例）" />
      <label class="check"><input v-model="showAdvanced" type="checkbox" /> 显示 HA 提示片段（高级）</label>
      <span class="count">{{ visible.length }} / {{ doc.keys.length }} 条 · 覆盖 {{ Object.keys(doc.file_overrides).length }} 条</span>
    </div>
    <div class="actions">
      <button class="primary" :disabled="busy !== '' || !dirty.length" @click="save">
        {{ busy === 'save' ? '…' : `保存（${dirty.length} 处修改）` }}
      </button>
      <button :disabled="busy !== ''" @click="resetAll">{{ busy === 'reset' ? '…' : '全部恢复默认' }}</button>
    </div>
    <div v-if="saved" class="note ok">{{ saved }}</div>
    <div v-if="error" class="note fail">{{ error }}</div>
    <p v-if="doc.last_errors.length" class="note warn">
      运行时缺槽记录 {{ doc.last_errors.length }} 条（话术里删了槽位或调用方少传参数，已用兜底文本）。
    </p>
    <p v-if="doc.file_error" class="note fail">文件读不出：{{ doc.file_error }}（当前用默认表）</p>
    <p class="note">文件：{{ doc.file }}</p>
  </div>

  <div v-for="group in groups" :key="group.group" class="card">
    <h2>{{ group.title }}</h2>
    <div v-for="item in group.items" :key="item.key" class="phrase" :class="{ advanced: item.advanced }">
      <div class="phrase-head">
        <code class="phrase-key">{{ item.key }}</code>
        <span v-if="item.overridden || overrides[item.key] !== item.default" class="tag">已改</span>
        <span v-for="slot in item.slots" :key="slot" class="slot">{{ '{' + slot + '}' }}</span>
        <button class="small" :disabled="busy !== ''" @click="resetKey(item.key)">恢复默认</button>
      </div>
      <textarea v-model="overrides[item.key]" rows="2" :aria-label="item.key" />
      <div class="example">示例：{{ item.example }}</div>
    </div>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
.filter {
  font: inherit; font-size: 12px; padding: 6px 9px; border: 1px solid var(--line);
  border-radius: 8px; flex: 1; min-width: 180px; max-width: 340px;
}
.check { font-size: 12px; color: var(--muted); display: flex; gap: 4px; align-items: center; }
.count { font-size: 11px; color: var(--muted); margin-left: auto; }
.actions { display: flex; gap: 8px; }
.phrase { padding: 10px 0; border-bottom: 1px solid #f2f3f6; }
.phrase:last-child { border-bottom: 0; }
.phrase.advanced textarea { background: #fcfaf4; }
.phrase-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; }
.phrase-key { font-size: 11.5px; color: var(--muted); }
.phrase-head button { margin-left: auto; }
.slot {
  font: 11px ui-monospace, SFMono-Regular, Menlo, monospace; background: #eef1f7;
  color: #3b4a63; padding: 1px 6px; border-radius: 999px;
}
.phrase textarea {
  width: 100%; font: inherit; font-size: 13px; padding: 7px 10px; border: 1px solid var(--line);
  border-radius: 8px; resize: vertical; box-sizing: border-box;
}
.example { font-size: 11.5px; color: var(--muted); margin-top: 4px; }
button.small { font-size: 11.5px; padding: 2px 8px; }
</style>
