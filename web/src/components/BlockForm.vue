<script setup lang="ts">
/**
 * The config form primitive (docs/console.md P2): the spec names a section and
 * its fields, this renders them, PUT /api/v1/config validates, and a 422 marks
 * the offending inputs by name. No per-feature form code - a new setting is one
 * PanelSpec entry.
 */
import { computed, ref, watch } from 'vue'
import { api, ApiError, type ConfigPatch, type ValidationIssue } from '../api'
import type { PanelBlock } from '../panels'

const props = defineProps<{ block: PanelBlock; data: unknown; refresh: () => void }>()

type Value = string | number | null
type Field = NonNullable<PanelBlock['fields']>[number]

const section = computed(() => props.block.section ?? '')
const server = computed<Record<string, Value>>(() => {
  const all = props.data as Record<string, unknown> | undefined
  const part = all?.[section.value]
  return (part as Record<string, Value>) ?? {}
})

const draft = ref<Record<string, Value>>({})
const errors = ref<Record<string, string>>({})
const state = ref<'idle' | 'saving' | 'saved' | 'error'>('idle')
const message = ref('')

function sync() {
  const next: Record<string, Value> = {}
  for (const f of props.block.fields ?? []) next[f.key] = server.value[f.key] ?? ''
  draft.value = next
  errors.value = {}
  state.value = 'idle'
  message.value = ''
}
watch(server, sync, { immediate: true, deep: true })

const dirty = computed(() =>
  (props.block.fields ?? []).some(
    (f) => String(draft.value[f.key] ?? '') !== String(server.value[f.key] ?? ''),
  ),
)

function coerce(field: Field): Value {
  const raw = draft.value[field.key]
  if (field.kind === 'number') return raw === '' ? null : Number(raw)
  return raw ?? ''
}

function issueField(issue: ValidationIssue): string {
  const last = (issue.loc ?? []).slice(-1)[0]
  return typeof last === 'string' ? last : ''
}

async function save() {
  if (state.value === 'saving') return
  state.value = 'saving'
  errors.value = {}
  message.value = ''
  const patch: Record<string, Value> = {}
  for (const f of props.block.fields ?? []) patch[f.key] = coerce(f)
  try {
    await api.saveConfig({ [section.value]: patch } as ConfigPatch)
    state.value = 'saved'
    props.refresh()
  } catch (exc) {
    state.value = 'error'
    if (exc instanceof ApiError) {
      message.value = exc.message
      for (const issue of exc.issues ?? []) {
        const key = issueField(issue)
        if (key) errors.value[key] = issue.msg
      }
    } else {
      message.value = String(exc)
    }
  }
}

function revert() {
  sync()
}
</script>

<template>
  <div class="card">
    <h2>
      {{ block.title || section }}
      <span v-if="block.apply" class="tag">{{ block.apply }}</span>
    </h2>

    <div v-for="f in block.fields" :key="f.key" class="field">
      <label class="form-row">
        <span class="label">
          {{ f.label }}<span v-if="f.unit" class="unit">（{{ f.unit }}）</span>
        </span>
        <select v-if="f.kind === 'select'" v-model="draft[f.key]" class="input">
          <option v-for="opt in f.options" :key="opt" :value="opt">{{ opt }}</option>
        </select>
        <textarea v-else-if="f.kind === 'textarea'" v-model="draft[f.key]" class="input" rows="4" />
        <input
          v-else
          v-model="draft[f.key]"
          class="input"
          :type="f.kind === 'number' ? 'number' : 'text'"
          :min="f.min"
          :max="f.max"
        />
      </label>
      <div v-if="f.hint && !errors[f.key]" class="note">{{ f.hint }}</div>
      <div v-if="errors[f.key]" class="note fail">{{ errors[f.key] }}</div>
    </div>

    <div class="actions">
      <button :disabled="!dirty || state === 'saving'" class="btn-primary" @click="save">
        {{ state === 'saving' ? '保存中…' : '保存' }}
      </button>
      <button :disabled="!dirty || state === 'saving'" @click="revert">还原</button>
      <span v-if="state === 'saved'" class="ok">已保存</span>
      <span v-if="state === 'error'" class="fail">保存失败</span>
    </div>
    <div v-if="message" class="note fail">{{ message }}</div>
  </div>
</template>

<style scoped>
.field { margin-bottom: 12px; }
.form-row { display: flex; align-items: center; gap: 12px; }
.label { width: 180px; color: var(--muted); font-size: 13px; flex: none; }
.unit { font-size: 11px; }
.input {
  font: inherit; font-size: 13px; padding: 6px 9px; border: 1px solid var(--line);
  border-radius: 8px; background: #fff; width: 100%; max-width: 340px;
}
textarea.input { max-width: 560px; resize: vertical; }
.actions { display: flex; gap: 8px; align-items: center; margin-top: 4px; }
.actions .ok { color: var(--ok); font-size: 12px; }
.actions .fail { color: var(--fail); font-size: 12px; }
</style>
