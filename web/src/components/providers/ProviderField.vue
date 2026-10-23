<script setup lang="ts">
/** One editable provider field: label, control, hint, inline error. */
import { computed } from 'vue'
import type { CatalogField } from '../../api'
import { pickLocalized } from '../../i18n'
import { t } from '../../i18n'
import {
  fieldHint,
  fieldId,
  fieldLabel,
  fieldUnit,
  optionLabel,
  optionValue,
  type VoiceOption,
} from './types'

const props = defineProps<{
  field: CatalogField
  name: string
  value: unknown
  invalid?: boolean
  error?: string
  dirty?: boolean
  disabled?: boolean
  voiceOptions?: VoiceOption[]
}>()

const emit = defineEmits<{ update: [value: unknown] }>()

const id = computed(() => fieldId(props.field))
const label = computed(() => fieldLabel(props.field, props.name))
const hint = computed(() => fieldHint(props.field))
const unit = computed(() => fieldUnit(props.field))

const isVoice = computed(() => props.field.path.endsWith('.voice'))
const voiceChoices = computed<VoiceOption[]>(() => (isVoice.value ? props.voiceOptions ?? [] : []))

const choices = computed(() => {
  if (voiceChoices.value.length) {
    return voiceChoices.value.map((voice) => ({
      value: String(voice.value),
      label: voice.label ? pickLocalized(voice.label) : voice.value,
    }))
  }
  return (props.field.options ?? []).map((option) => ({
    value: String(optionValue(option)),
    label: optionLabel(option),
  }))
})

const asText = computed(() => {
  const value = props.value
  if (value == null) return ''
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  return JSON.stringify(value)
})

function onInput(event: Event): void {
  emit('update', (event.target as HTMLInputElement | HTMLTextAreaElement).value)
}

function onChange(event: Event): void {
  emit('update', (event.target as HTMLSelectElement).value)
}
</script>

<template>
  <div class="p-field" :class="{ invalid }">
    <div class="p-label">
      <label :for="id">
        <span v-if="dirty" class="dot live" aria-hidden="true" />
        {{ label }}
      </label>
      <span v-if="unit" class="p-unit mono">{{ unit }}</span>
    </div>

    <div class="p-control-row">
      <label v-if="field.kind === 'bool'" class="switch">
        <input :id="id" type="checkbox" :checked="value === true" :disabled="disabled" @change="emit('update', ($event.target as HTMLInputElement).checked)" />
        <span class="track" />
        <span class="switch-text">{{ value === true ? t('common.on') : t('common.off') }}</span>
      </label>

      <select
        v-else-if="field.kind === 'select' && choices.length"
        :id="id"
        :value="asText"
        :class="{ invalid }"
        :disabled="disabled"
        @change="onChange"
      >
        <option v-for="choice in choices" :key="choice.value" :value="choice.value">{{ choice.label }}</option>
      </select>

      <input
        v-else-if="field.kind === 'number'"
        :id="id"
        :value="asText"
        type="number"
        :min="field.min ?? undefined"
        :max="field.max ?? undefined"
        :class="{ invalid }"
        :disabled="disabled"
        @input="onInput"
      />

      <textarea
        v-else-if="field.kind === 'textarea'"
        :id="id"
        :value="asText"
        rows="3"
        :class="{ invalid }"
        :disabled="disabled"
        @input="onInput"
      />

      <input
        v-else
        :id="id"
        :value="asText"
        type="text"
        :class="{ invalid }"
        :disabled="disabled"
        @input="onInput"
      />

    </div>

    <div v-if="$slots.extra" class="p-extra">
      <slot name="extra" />
    </div>

    <p v-if="error" class="p-error" role="alert">{{ error }}</p>
    <p v-else-if="hint" class="p-hint" :title="hint">{{ hint }}</p>
  </div>
</template>

<style scoped>
.p-field {
  min-width: 0;
}

.p-label {
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--ink-2);
}

.p-label .dot {
  width: 5px;
  height: 5px;
  animation: none;
}

.p-unit {
  font-size: 10px;
  color: var(--muted);
}

.p-control-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 3px;
  min-width: 0;
}

.p-control-row > :deep(input),
.p-control-row > :deep(select),
.p-control-row > :deep(textarea) {
  min-height: 32px;
  padding: 5px 8px;
  font-size: 12.5px;
}

.p-control-row > :deep(textarea) {
  min-height: 64px;
}

.p-control-row > select,
.p-control-row > input {
  flex: 1;
  min-width: 0;
}

.p-extra {
  margin-top: 6px;
  min-width: 0;
}

.p-hint {
  margin-top: 3px;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.5;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.p-error {
  margin-top: 3px;
  color: var(--fail);
  font-size: 11px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.invalid :deep(input),
.invalid :deep(select),
.invalid :deep(textarea) {
  border-color: var(--fail);
}

/* Touch targets: only on phones, so the desktop density stays untouched. */
@media (max-width: 720px) {
  .p-label {
    font-size: 13px;
  }

  .p-control-row > input,
  .p-control-row > select {
    min-height: 44px;
    padding: 10px 10px;
    font-size: 14px;
  }

  .p-control-row > textarea {
    min-height: 96px;
    padding: 10px;
    font-size: 14px;
  }

  .switch {
    min-height: 44px;
    padding: 8px 0;
  }

  .p-hint {
    font-size: 12px;
  }
}
</style>
