<script setup lang="ts">
/**
 * The config form primitive (docs/console.md P2). It predates the catalog-driven
 * ConfigCenter and still serves any PanelSpec that declares a simple form; the
 * five main config pages now use `config-center`.
 */
import { computed, ref, watch } from 'vue'
import { api, ApiError, type ConfigPatch, type ValidationIssue } from '../api'
import type { PanelBlock } from '../panels'
import { localizeBackend, localizeIssue, t } from '../i18n'

const props = defineProps<{ block: PanelBlock; data: unknown; refresh: () => void }>()

type Value = string | number | boolean | null
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
  for (const field of props.block.fields ?? []) next[field.key] = server.value[field.key] ?? ''
  draft.value = next
  errors.value = {}
  state.value = 'idle'
  message.value = ''
}
watch(server, sync, { immediate: true, deep: true })

const dirty = computed(() =>
  (props.block.fields ?? []).some(
    (field) => String(draft.value[field.key] ?? '') !== String(server.value[field.key] ?? ''),
  ),
)

function coerce(field: Field): Value {
  const raw = draft.value[field.key]
  if (field.kind === 'bool') return raw === true || raw === 'true'
  if (field.kind === 'number') return raw === '' ? null : Number(raw)
  return raw ?? ''
}

function asInput(value: Value): string | number {
  return value == null || typeof value === 'boolean' ? '' : value
}

function setText(field: Field, raw: string): void {
  draft.value[field.key] = raw
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
  for (const field of props.block.fields ?? []) patch[field.key] = coerce(field)
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
        if (key) errors.value[key] = localizeIssue(issue.msg)
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
  <section class="block">
    <div class="block-head">
      <h2>
        {{ localizeBackend(block.title || section) }}
        <span v-if="block.apply" class="badge badge-save">{{ localizeBackend(block.apply) }}</span>
      </h2>
    </div>

    <div class="form-fields">
      <div v-for="field in block.fields" :key="field.key" class="field-row" :class="{ invalid: errors[field.key] }">
        <div class="field-meta">
          <label class="field-label" :for="`bf-${section}-${field.key}`">
            {{ localizeBackend(field.label) }}
            <span v-if="field.unit" class="badge">{{ localizeBackend(field.unit) }}</span>
          </label>
          <p v-if="field.hint" class="field-hint">{{ localizeBackend(field.hint) }}</p>
        </div>
        <div class="field-control">
          <label v-if="field.kind === 'bool'" class="switch">
            <input :id="`bf-${section}-${field.key}`" v-model="draft[field.key]" type="checkbox" />
            <span class="track" />
            <span class="switch-text">{{ draft[field.key] ? t('common.on') : t('common.off') }}</span>
          </label>
          <select
            v-else-if="field.kind === 'select'"
            :id="`bf-${section}-${field.key}`"
            v-model="draft[field.key]"
            :class="{ invalid: errors[field.key] }"
          >
            <option v-for="option in field.options" :key="option" :value="option">{{ localizeBackend(option) }}</option>
          </select>
          <textarea
            v-else-if="field.kind === 'textarea'"
            :id="`bf-${section}-${field.key}`"
            :value="asInput(draft[field.key])"
            rows="4"
            :class="{ invalid: errors[field.key] }"
            @input="setText(field, ($event.target as HTMLTextAreaElement).value)"
          />
          <input
            v-else
            :id="`bf-${section}-${field.key}`"
            :value="asInput(draft[field.key])"
            :type="field.kind === 'number' ? 'number' : 'text'"
            :min="field.min"
            :max="field.max"
            :class="{ invalid: errors[field.key] }"
            @input="setText(field, ($event.target as HTMLInputElement).value)"
          />
          <p v-if="errors[field.key]" class="field-error">{{ errors[field.key] }}</p>
        </div>
      </div>
    </div>

    <div class="actions" style="margin-top: var(--s4)">
      <button type="button" class="btn btn-primary" :disabled="!dirty || state === 'saving'" @click="save">
        {{ state === 'saving' ? t('block.saving') : t('block.save') }}
      </button>
      <button type="button" class="btn" :disabled="!dirty || state === 'saving'" @click="revert">
        {{ t('block.revert') }}
      </button>
      <span v-if="state === 'saved'" class="ok small">{{ t('common.saved') }}</span>
      <span v-if="state === 'error'" class="fail small">{{ t('block.saveFailed') }}</span>
    </div>
    <p v-if="message" class="note fail">{{ message }}</p>
  </section>
</template>
