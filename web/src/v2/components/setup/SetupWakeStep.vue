<script setup lang="ts">
/**
 * `wake` step: shows the server's current words and writes new ones through
 * `wake.set` (contract §7.24). Saving regenerates keywords and restarts the
 * audio plane server-side.
 */
import { computed, ref, watch } from 'vue'
import type { SetupStep } from '../../api'
import { boolValue, stringList } from '../../setup'
import { t } from '../../i18n'

const props = defineProps<{
  step: SetupStep
  busy: boolean
}>()

const emit = defineEmits<{
  submit: [action: string, values: Record<string, unknown>]
}>()

const current = computed(() => stringList(props.step.evidence?.words))
const keywordsExists = computed(() => boolValue(props.step.evidence?.keywords_exists))
const canSet = computed(() => props.step.actions.includes('wake.set'))

const draft = ref<string[]>([''])
const localError = ref('')
let lastSignature = ''

watch(
  () => JSON.stringify(current.value),
  (signature) => {
    if (signature === lastSignature) return
    lastSignature = signature
    draft.value = current.value.length ? [...current.value] : ['']
  },
  { immediate: true },
)

function clean(): string[] {
  return draft.value.map((word) => word.trim()).filter(Boolean)
}

function add(): void {
  if (draft.value.length >= 3) return
  draft.value.push('')
}

function remove(index: number): void {
  draft.value.splice(index, 1)
  if (draft.value.length === 0) draft.value.push('')
}

function setWord(index: number, value: string): void {
  draft.value[index] = value
  localError.value = ''
}

function submit(): void {
  if (props.busy || !canSet.value) return
  localError.value = ''
  const words = clean()
  if (!words.length) {
    localError.value = t('v2.setup.wake.required')
    return
  }
  if (words.length > 3) {
    localError.value = t('v2.setup.wake.max')
    return
  }
  for (const word of words) {
    if (word.length > 12) {
      localError.value = t('v2.setup.wake.tooLong')
      return
    }
    if (/\s/.test(word)) {
      localError.value = t('v2.setup.wake.space')
      return
    }
  }
  emit('submit', 'wake.set', { wake_words: words })
}
</script>

<template>
  <div class="v2-setup-step-body">
    <h4 class="v2-setup-subtitle">{{ t('v2.setup.wake.title') }}</h4>

    <div class="v2-setup-evidence" data-testid="setup-wake-current">
      <span class="v2-summary-label">{{ t('v2.setup.wake.current') }}</span>
      <div v-if="current.length" class="v2-setup-chip-row">
        <span v-for="word in current" :key="word" class="badge">{{ word }}</span>
      </div>
      <p v-else class="note">{{ t('v2.setup.wake.none') }}</p>
      <p v-if="!keywordsExists" class="note" data-testid="setup-wake-keywords-missing">
        {{ t('v2.setup.wake.keywordsMissing') }}
      </p>
    </div>

    <p v-if="canSet" class="note">{{ t('v2.setup.wake.hint') }}</p>

    <form
      v-if="canSet"
      class="v2-setup-subcard"
      data-testid="setup-wake-form"
      @submit.prevent="submit"
    >
      <div class="field-row">
        <div class="field-meta">
          <label class="field-label">{{ t('v2.setup.wake.words') }}</label>
        </div>
        <div class="field-control">
          <div v-for="(word, index) in draft" :key="`wake-edit-${index}`" class="v2-wake-row">
            <input
              :id="`setup-wake-word-${index}`"
              :value="word"
              type="text"
              maxlength="12"
              class="input"
              :placeholder="t('v2.setup.wake.placeholder')"
              :disabled="busy"
              :data-testid="`setup-wake-word-${index}`"
              @input="setWord(index, ($event.target as HTMLInputElement).value)"
            />
            <button
              type="button"
              class="btn btn-sm"
              :aria-label="t('v2.setup.wake.remove', { n: index + 1 })"
              :disabled="busy"
              @click="remove(index)"
            >
              ×
            </button>
          </div>
          <button
            type="button"
            class="btn btn-sm v2-wake-add"
            :disabled="busy || draft.length >= 3"
            @click="add"
          >
            {{ t('v2.setup.wake.add') }}
          </button>
        </div>
      </div>
      <p v-if="localError" class="field-error" role="alert">{{ localError }}</p>
      <div class="v2-save-bar">
        <button
          type="submit"
          class="btn btn-primary btn-sm"
          data-testid="setup-wake-submit"
          :disabled="busy || clean().length === 0"
        >
          {{ busy ? t('v2.setup.wake.saving') : t('v2.setup.wake.save') }}
        </button>
      </div>
    </form>
  </div>
</template>
