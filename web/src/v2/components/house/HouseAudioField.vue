<script setup lang="ts">
/**
 * One global-audio field row: value, explicit/restart badges, editor control,
 * inline 422 error and the clear-to-inherit action.
 *
 * The parent owns the draft/diff state; this component is deliberately
 * stateless so the same row renders inside a group and inside its advanced
 * disclosure without duplicating logic.
 */
import { computed } from 'vue'
import type { HouseAudioRow } from '../../houseAudio'
import { displayValue } from '../../format'
import { t } from '../../i18n'

const props = defineProps<{
  row: HouseAudioRow
  controlValue: string
  error: string
  cleared: boolean
  dirty: boolean
  canClear: boolean
  canEdit: boolean
}>()

const emit = defineEmits<{
  input: [event: Event]
  clear: []
  undo: []
}>()

const control = computed(() => props.row.spec?.control ?? null)
const inputTestId = computed(() => `audio-input-${props.row.path}`)
const errorTestId = computed(() => `audio-error-${props.row.path}`)
</script>

<template>
  <div
    class="v2-house-audio-row"
    :class="{
      'is-dirty': dirty,
      'is-explicit': row.explicit,
      'is-invalid': Boolean(error),
    }"
    :data-testid="`audio-row-${row.path}`"
  >
    <div class="v2-house-audio-path">
      <span class="mono v2-house-audio-path-text">{{ row.path }}</span>
      <span v-if="row.explicit" class="tag badge-accent">
        {{ t('v2.houseAudio.explicit') }}
      </span>
      <span v-else class="tag">{{ t('v2.houseAudio.implicit') }}</span>
      <span v-if="row.restart" class="tag">{{ t('v2.houseAudio.restartField') }}</span>
    </div>

    <div class="v2-house-audio-value">
      <span class="v2-audio-effective-label">{{ t('v2.houseAudio.value') }}</span>
      <span class="mono v2-audio-effective-value">{{ displayValue(row.value) }}</span>
    </div>

    <div class="v2-house-audio-control">
      <template v-if="row.spec">
        <input
          v-if="control === 'number'"
          class="input v2-input v2-audio-input"
          :class="{ 'is-invalid': Boolean(error) }"
          type="number"
          step="1"
          :min="row.spec.min"
          :max="row.spec.max"
          :value="controlValue"
          :disabled="!canEdit"
          :aria-invalid="Boolean(error)"
          :data-testid="inputTestId"
          @input="emit('input', $event)"
        />
        <input
          v-else-if="control === 'text'"
          class="input v2-input v2-audio-input"
          :class="{ 'is-invalid': Boolean(error) }"
          type="text"
          :value="controlValue"
          :disabled="!canEdit"
          :aria-invalid="Boolean(error)"
          :data-testid="inputTestId"
          @input="emit('input', $event)"
        />
        <select
          v-else-if="control === 'boolean'"
          class="select v2-select v2-audio-input"
          :class="{ 'is-invalid': Boolean(error) }"
          :value="controlValue"
          :disabled="!canEdit"
          :aria-invalid="Boolean(error)"
          :data-testid="inputTestId"
          @change="emit('input', $event)"
        >
          <option value="" disabled>{{ t('v2.houseAudio.clearPending') }}</option>
          <option value="true">{{ t('v2.common.yes') }}</option>
          <option value="false">{{ t('v2.common.no') }}</option>
        </select>
        <select
          v-else-if="control === 'enum'"
          class="select v2-select v2-audio-input"
          :class="{ 'is-invalid': Boolean(error) }"
          :value="controlValue"
          :disabled="!canEdit"
          :aria-invalid="Boolean(error)"
          :data-testid="inputTestId"
          @change="emit('input', $event)"
        >
          <option value="" disabled>{{ t('v2.houseAudio.clearPending') }}</option>
          <option
            v-for="option in row.spec.options ?? []"
            :key="String(option.value)"
            :value="String(option.value)"
          >
            {{ option.label }}
          </option>
        </select>
        <p v-if="cleared" class="v2-house-audio-clear-note">
          {{ t('v2.houseAudio.clearPending') }}
        </p>
        <p v-if="error" class="v2-field-error" :data-testid="errorTestId">
          {{ error }}
        </p>
      </template>
      <span v-else class="note v2-audio-readonly">{{ t('v2.houseAudio.readOnly') }}</span>
    </div>

    <div class="v2-house-audio-actions">
      <button
        v-if="cleared"
        type="button"
        class="btn btn-sm"
        :disabled="!canEdit"
        @click="emit('undo')"
      >
        {{ t('v2.houseAudio.undoClear') }}
      </button>
      <button
        v-else-if="row.explicit && canClear"
        type="button"
        class="btn btn-sm"
        :disabled="!canEdit"
        :title="t('v2.houseAudio.clearHint')"
        :data-testid="`audio-clear-${row.path}`"
        @click="emit('clear')"
      >
        {{ t('v2.houseAudio.clear') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.v2-house-audio-row {
  display: grid;
  grid-template-columns: minmax(180px, 1.1fr) minmax(110px, 0.55fr) minmax(180px, 1fr) minmax(90px, auto);
  gap: var(--s3);
  align-items: start;
  padding: 11px 0 11px 10px;
  border-bottom: 1px solid var(--line);
  border-left: 3px solid transparent;
}

.v2-house-audio-row.is-explicit {
  border-left-color: var(--accent);
}

.v2-house-audio-row.is-dirty {
  background: var(--warn-soft);
}

.v2-house-audio-row.is-invalid {
  border-left-color: var(--fail);
}

.v2-house-audio-path {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
  min-width: 0;
}

.v2-house-audio-path-text {
  font-size: 12px;
  overflow-wrap: anywhere;
}

.v2-house-audio-value {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.v2-house-audio-control {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  min-width: 0;
}

.v2-house-audio-control .v2-audio-input {
  width: 100%;
  max-width: 260px;
  min-height: 32px;
  padding: 5px 8px;
  font-size: 12px;
}

.v2-house-audio-control .v2-audio-input.is-invalid {
  border-color: var(--fail);
  box-shadow: 0 0 0 2px var(--fail-soft);
}

.v2-house-audio-clear-note {
  margin: 0;
  color: var(--warn);
  font-size: 10.5px;
}

.v2-field-error {
  margin: 2px 0 0;
  color: var(--fail);
  font-size: 11.5px;
  line-height: 1.45;
}

.v2-house-audio-actions {
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 900px) {
  .v2-house-audio-row {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }

  .v2-house-audio-actions {
    justify-content: flex-start;
  }
}

@media (max-width: 560px) {
  .v2-house-audio-row {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
