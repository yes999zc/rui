<script setup lang="ts">
/**
 * One catalog field. The control is chosen from `kind`, the range/options from
 * `min`/`max`/`options`, and the restart badge from `effect` — all server data.
 * A read-only field renders its value as text (device fields link elsewhere).
 */
import { computed, ref, watch } from 'vue'
import AppIcon from '../../../components/AppIcon.vue'
import type { ConfigFieldSpec } from '../../api'
import {
  displayValue,
  fieldInputId,
  fieldOptions,
  formatListInput,
  isKnownFieldKind,
  parseListInput,
} from '../../config'
import { pickLocalized, t } from '../../i18n'

const props = withDefaults(
  defineProps<{
    field: ConfigFieldSpec
    modelValue: unknown
    error?: string
  }>(),
  { error: '' },
)

const emit = defineEmits<{ 'update:modelValue': [value: unknown] }>()

const inputId = computed(() => fieldInputId(props.field.path))
const options = computed(() =>
  fieldOptions(props.field).map((option) => ({
    value: option.value,
    label: pickLocalized(option.label),
  })),
)
const isKnownKind = computed(() => isKnownFieldKind(props.field.kind))
const isUnsupported = computed(() => !isKnownKind.value)
const isReadOnly = computed(() => !props.field.writable)
const isEditable = computed(() => props.field.writable && isKnownKind.value)
const isRestart = computed(() => props.field.effect === 'restart')
const unit = computed(() => pickLocalized(props.field.unit))
const valueText = computed(() => {
  const value = props.modelValue
  return value === null || value === undefined ? '' : String(value)
})

// `list` keeps the raw textarea text so newlines survive while typing; the
// emitted value is the parsed array.
const listText = ref(formatListInput(props.modelValue))
watch(
  () => props.modelValue,
  (value) => {
    const next = formatListInput(value)
    if (parseListInput(listText.value).join('\n') !== next) listText.value = next
  },
)

function emitText(event: Event): void {
  emit('update:modelValue', (event.target as HTMLInputElement | HTMLTextAreaElement).value)
}

function emitNumber(event: Event): void {
  const raw = (event.target as HTMLInputElement).value
  if (raw === '') {
    emit('update:modelValue', props.field.nullable ? null : '')
    return
  }
  const value = Number(raw)
  emit('update:modelValue', Number.isFinite(value) ? value : raw)
}

function emitBool(event: Event): void {
  emit('update:modelValue', (event.target as HTMLInputElement).checked)
}

function emitSelect(event: Event): void {
  const raw = (event.target as HTMLSelectElement).value
  if (raw === '' && props.field.nullable) {
    emit('update:modelValue', null)
    return
  }
  // Preserve the catalog's option type: numeric options (e.g. bit depth 16/32)
  // must be sent as numbers, not as DOM strings.
  const match = options.value.find((option) => String(option.value) === raw)
  emit('update:modelValue', match ? match.value : raw)
}

function emitList(event: Event): void {
  listText.value = (event.target as HTMLTextAreaElement).value
  emit('update:modelValue', parseListInput(listText.value))
}
</script>

<template>
  <div
    class="field-row v2-config-field"
    :class="{ invalid: Boolean(error) }"
    :data-field-path="field.path"
    :data-field-group="field.group"
    :data-field-effect="field.effect"
  >
    <div class="field-meta">
      <label class="field-label" :for="isEditable ? inputId : undefined">
        <span>{{ pickLocalized(field.label) }}</span>
        <span v-if="isRestart" class="badge badge-restart" data-testid="field-restart-badge">
          <AppIcon name="refresh" :size="11" />
          {{ t('v2.config.effect.restart') }}
        </span>
        <span v-if="field.advanced" class="badge badge-muted">
          {{ t('v2.common.advanced') }}
        </span>
        <span
          v-if="isUnsupported"
          class="badge badge-warn"
          data-testid="config-unsupported-badge"
        >
          {{ t('v2.config.unsupportedKind', { kind: field.kind }) }}
        </span>
        <span v-else-if="isReadOnly" class="badge badge-muted">{{ t('v2.common.readOnly') }}</span>
      </label>
      <p class="field-hint">{{ pickLocalized(field.hint) }}</p>
      <p class="field-meta-line v2-config-path">{{ field.path }}</p>
    </div>

    <div class="field-control" :class="{ wide: field.kind === 'textarea' || field.kind === 'list' }">
      <div
        v-if="isUnsupported"
        class="v2-config-readonly"
        data-testid="config-unsupported-value"
      >
        {{ displayValue(modelValue) }}
      </div>

      <div v-else-if="isReadOnly" class="v2-config-readonly" data-testid="config-readonly-value">
        {{ displayValue(modelValue) }}
      </div>

      <label v-else-if="field.kind === 'bool'" class="switch v2-config-switch">
        <input
          :id="inputId"
          type="checkbox"
          :checked="Boolean(modelValue)"
          :aria-invalid="Boolean(error)"
          @change="emitBool"
        />
        <span class="track" aria-hidden="true" />
        <span class="switch-text">
          {{ modelValue ? t('v2.common.enabled') : t('v2.common.disabled') }}
        </span>
      </label>

      <select
        v-else-if="field.kind === 'select'"
        :id="inputId"
        class="input v2-select"
        :class="{ invalid: Boolean(error) }"
        :value="valueText"
        :aria-invalid="Boolean(error)"
        @change="emitSelect"
      >
        <option v-if="field.nullable" value="">—</option>
        <option v-for="option in options" :key="option.value" :value="option.value">
          {{ option.label }}
        </option>
      </select>

      <textarea
        v-else-if="field.kind === 'textarea'"
        :id="inputId"
        class="input"
        :class="{ invalid: Boolean(error) }"
        rows="4"
        :value="valueText"
        :aria-invalid="Boolean(error)"
        @input="emitText"
      />

      <textarea
        v-else-if="field.kind === 'list'"
        :id="inputId"
        class="input v2-config-list"
        :class="{ invalid: Boolean(error) }"
        rows="3"
        :value="listText"
        :aria-invalid="Boolean(error)"
        @input="emitList"
      />

      <div v-else-if="field.kind === 'number'" class="v2-config-number">
        <input
          :id="inputId"
          class="input"
          :class="{ invalid: Boolean(error) }"
          type="number"
          :value="valueText"
          :min="field.min ?? undefined"
          :max="field.max ?? undefined"
          :step="Number.isInteger(field.min) && Number.isInteger(field.max) ? 1 : 'any'"
          :aria-invalid="Boolean(error)"
          @input="emitNumber"
        />
        <span v-if="unit" class="v2-config-unit">{{ unit }}</span>
      </div>

      <input
        v-else
        :id="inputId"
        class="input"
        :class="{ invalid: Boolean(error) }"
        type="text"
        :value="valueText"
        :aria-invalid="Boolean(error)"
        @input="emitText"
      />

      <p v-if="unit && field.kind !== 'number'" class="field-meta-line">{{ unit }}</p>
      <p v-if="field.kind === 'list'" class="field-meta-line">
        {{ t('v2.config.listHint') }}
      </p>
      <p v-if="field.min !== null || field.max !== null" class="field-meta-line">
        {{ t('v2.config.range', { min: field.min ?? '—', max: field.max ?? '—' }) }}
      </p>
      <p v-if="error" class="field-error" data-testid="config-field-error">
        {{ error }}
      </p>
    </div>
  </div>
</template>
