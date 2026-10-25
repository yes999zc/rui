<script setup lang="ts">
/**
 * `keys` step (contract §7.24): write-only keys, an online credential probe and
 * provider-chain selection. Buttons are rendered only for actions the server
 * advertises on this step; no action id is invented here.
 */
import { computed, ref, watch } from 'vue'
import type { SetupStep } from '../../api'
import { apiErrorDetail } from '../../errors'
import { keyTestView, stringList } from '../../setup'
import { t } from '../../i18n'

const props = defineProps<{
  step: SetupStep
  busy: boolean
  /** Last `keys.test` result, only when it belongs to this step. */
  result: Record<string, unknown> | null
  /** Last failed request, used to surface `details.available` on providers.save. */
  errorRaw: unknown
}>()

const emit = defineEmits<{
  submit: [action: string, values: Record<string, unknown>]
}>()

const missing = computed(() => stringList(props.step.evidence?.missing))
const stages = computed<Record<string, boolean>>(() => {
  const raw = props.step.evidence?.stages
  if (!raw || typeof raw !== 'object') return {}
  return raw as Record<string, boolean>
})
const stageIds = ['asr', 'tts', 'llm'] as const

const keyDrafts = ref<Record<string, string>>({})
const extraName = ref('')
const extraValue = ref('')

let lastMissingSignature = ''
watch(
  missing,
  (names) => {
    const signature = names.join('\n')
    if (signature === lastMissingSignature) return
    lastMissingSignature = signature
    const next: Record<string, string> = {}
    for (const name of names) next[name] = ''
    keyDrafts.value = next
  },
  { immediate: true },
)

watch(
  () => props.step.status,
  (status) => {
    if (status !== 'done') return
    // The server confirmed the stage evidence; drop write-only inputs.
    keyDrafts.value = {}
    extraName.value = ''
    extraValue.value = ''
    lastMissingSignature = ''
  },
)
const testStage = ref('asr')
const testProvider = ref('')
const providerStage = ref('asr')
const providerName = ref('')
const providerVoice = ref('')

const testResult = computed(() => keyTestView(props.result))
const availableProviders = computed<string[]>(() => {
  const raw = apiErrorDetail(props.errorRaw, 'available')
  return Array.isArray(raw) ? raw.map(String) : []
})

function hasAction(action: string): boolean {
  return props.step.actions.includes(action)
}

const filledKeys = computed<Record<string, string>>(() => {
  const out: Record<string, string> = {}
  for (const [name, value] of Object.entries(keyDrafts.value)) {
    if (name.trim() && value.trim()) out[name.trim()] = value
  }
  if (extraName.value.trim() && extraValue.value.trim()) {
    out[extraName.value.trim()] = extraValue.value
  }
  return out
})

function saveKeys(): void {
  if (props.busy || Object.keys(filledKeys.value).length === 0) return
  emit('submit', 'keys.save', { values: filledKeys.value })
}

function testKey(): void {
  if (props.busy) return
  const values: Record<string, unknown> = { stage: testStage.value }
  if (testProvider.value.trim()) values.provider = testProvider.value.trim()
  emit('submit', 'keys.test', values)
}

function saveProvider(): void {
  if (props.busy || !providerName.value.trim()) return
  const values: Record<string, unknown> = {
    stage: providerStage.value,
    provider: providerName.value.trim(),
  }
  if (providerStage.value === 'tts' && providerVoice.value.trim()) {
    values.voice = providerVoice.value.trim()
  }
  emit('submit', 'providers.save', values)
}
</script>

<template>
  <div class="v2-setup-step-body">
    <div class="v2-setup-stage-grid">
      <div v-for="stage in stageIds" :key="stage" class="v2-setup-stage">
        <span class="v2-summary-label">{{ t(`v2.setup.keys.stage.${stage}`) }}</span>
        <span
          class="badge"
          :class="stages[stage] ? 'badge-ok' : 'badge-warn'"
          :data-testid="`setup-key-stage-${stage}`"
        >
          {{ stages[stage] ? t('v2.setup.models.ready') : t('v2.setup.models.missing') }}
        </span>
      </div>
    </div>

    <div class="v2-setup-evidence">
      <span class="v2-summary-label">{{ t('v2.setup.keys.missing') }}</span>
      <ul v-if="missing.length" class="v2-setup-list" data-testid="setup-keys-missing">
        <li v-for="name in missing" :key="name" class="mono">{{ name }}</li>
      </ul>
      <p v-else class="note">{{ t('v2.setup.keys.noneMissing') }}</p>
    </div>

    <form
      v-if="hasAction('keys.save')"
      class="v2-setup-subcard"
      data-testid="setup-keys-save"
      @submit.prevent="saveKeys"
    >
      <h4 class="v2-setup-subtitle">{{ t('v2.setup.keys.saveTitle') }}</h4>
      <p class="note">{{ t('v2.setup.keys.saveHint') }}</p>
      <div
        v-for="name in missing"
        :key="`key-${name}`"
        class="field-row"
      >
        <div class="field-meta">
          <label class="field-label mono" :for="`key-${name}`">{{ name }}</label>
        </div>
        <div class="field-control">
          <input
            :id="`key-${name}`"
            v-model="keyDrafts[name]"
            type="password"
            class="input"
            autocomplete="off"
            spellcheck="false"
            :disabled="busy"
          />
        </div>
      </div>
      <div class="field-row">
        <div class="field-meta">
          <label class="field-label" for="key-extra-name">{{ t('v2.setup.keys.extraKey') }}</label>
        </div>
        <div class="field-control">
          <input
            id="key-extra-name"
            v-model="extraName"
            type="text"
            class="input mono"
            autocomplete="off"
            spellcheck="false"
            :disabled="busy"
          />
          <input
            v-model="extraValue"
            type="password"
            class="input"
            autocomplete="off"
            :disabled="busy"
            :aria-label="t('v2.setup.keys.keyValue')"
          />
        </div>
      </div>
      <div class="v2-save-bar">
        <button
          type="submit"
          class="btn btn-primary btn-sm"
          data-testid="setup-keys-save-submit"
          :disabled="busy || Object.keys(filledKeys).length === 0"
        >
          {{ busy ? t('v2.setup.action.busy') : t('v2.setup.keys.save') }}
        </button>
        <p v-if="Object.keys(filledKeys).length === 0" class="note">
          {{ t('v2.setup.keys.noValues') }}
        </p>
      </div>
    </form>

    <form
      v-if="hasAction('keys.test')"
      class="v2-setup-subcard"
      data-testid="setup-keys-test"
      @submit.prevent="testKey"
    >
      <h4 class="v2-setup-subtitle">{{ t('v2.setup.keys.testTitle') }}</h4>
      <p class="note">{{ t('v2.setup.keys.testHint') }}</p>
      <div class="field-row">
        <div class="field-meta">
          <label class="field-label" for="key-test-stage">{{ t('v2.setup.keys.stage') }}</label>
        </div>
        <div class="field-control">
          <select id="key-test-stage" v-model="testStage" class="input" :disabled="busy">
            <option v-for="stage in stageIds" :key="stage" :value="stage">
              {{ t(`v2.setup.keys.stage.${stage}`) }}
            </option>
          </select>
        </div>
      </div>
      <div class="field-row">
        <div class="field-meta">
          <label class="field-label" for="key-test-provider">
            {{ t('v2.setup.keys.provider') }}
          </label>
        </div>
        <div class="field-control">
          <input
            id="key-test-provider"
            v-model="testProvider"
            type="text"
            class="input mono"
            :placeholder="t('v2.setup.keys.providerPlaceholder')"
            autocomplete="off"
            spellcheck="false"
            :disabled="busy"
          />
        </div>
      </div>
      <div class="v2-save-bar">
        <button
          type="submit"
          class="btn btn-sm"
          data-testid="setup-keys-test-submit"
          :disabled="busy"
        >
          {{ busy ? t('v2.setup.keys.testing') : t('v2.setup.keys.test') }}
        </button>
      </div>

      <div v-if="testResult" class="v2-setup-result" data-testid="setup-keys-test-result">
        <div class="v2-setup-result-head">
          <strong>{{ t('v2.setup.keys.testResult') }}</strong>
          <span class="badge" :class="testResult.ok ? 'badge-ok' : 'badge-fail'">
            {{ testResult.ok ? t('v2.setup.keys.testOk') : t('v2.setup.keys.testFail') }}
          </span>
          <span v-if="testResult.provider" class="mono">{{ testResult.provider }}</span>
        </div>
        <p v-if="testResult.message" class="note v2-break">{{ testResult.message }}</p>
        <p v-if="testResult.missing.length" class="note">
          {{ t('v2.setup.keys.missing') }}:
          <span class="mono">{{ testResult.missing.join(', ') }}</span>
        </p>
        <a
          v-if="testResult.docsUrl"
          class="btn btn-sm"
          :href="testResult.docsUrl"
          target="_blank"
          rel="noopener noreferrer"
        >
          {{ t('v2.setup.keys.docs') }}
        </a>
      </div>
    </form>

    <form
      v-if="hasAction('providers.save')"
      class="v2-setup-subcard"
      data-testid="setup-providers-save"
      @submit.prevent="saveProvider"
    >
      <h4 class="v2-setup-subtitle">{{ t('v2.setup.keys.providersTitle') }}</h4>
      <p class="note">{{ t('v2.setup.keys.providersHint') }}</p>
      <div class="field-row">
        <div class="field-meta">
          <label class="field-label" for="provider-stage">{{ t('v2.setup.keys.stage') }}</label>
        </div>
        <div class="field-control">
          <select id="provider-stage" v-model="providerStage" class="input" :disabled="busy">
            <option v-for="stage in stageIds" :key="stage" :value="stage">
              {{ t(`v2.setup.keys.stage.${stage}`) }}
            </option>
          </select>
        </div>
      </div>
      <div class="field-row">
        <div class="field-meta">
          <label class="field-label" for="provider-name">
            {{ t('v2.setup.keys.providerName') }}
          </label>
        </div>
        <div class="field-control">
          <input
            id="provider-name"
            v-model="providerName"
            type="text"
            class="input mono"
            autocomplete="off"
            spellcheck="false"
            :disabled="busy"
          />
          <p v-if="availableProviders.length" class="note" data-testid="setup-provider-available">
            {{
              t('v2.setup.keys.providerAvailable', {
                providers: availableProviders.join(', '),
              })
            }}
          </p>
        </div>
      </div>
      <div v-if="providerStage === 'tts'" class="field-row">
        <div class="field-meta">
          <label class="field-label" for="provider-voice">{{ t('v2.setup.keys.voice') }}</label>
        </div>
        <div class="field-control">
          <input
            id="provider-voice"
            v-model="providerVoice"
            type="text"
            class="input"
            :placeholder="t('v2.setup.keys.voicePlaceholder')"
            autocomplete="off"
            :disabled="busy"
          />
        </div>
      </div>
      <div class="v2-save-bar">
        <button
          type="submit"
          class="btn btn-sm"
          data-testid="setup-providers-save-submit"
          :disabled="busy || !providerName.trim()"
        >
          {{ busy ? t('v2.setup.action.busy') : t('v2.setup.keys.saveProvider') }}
        </button>
      </div>
    </form>

    <p v-if="!step.actions.length" class="note">{{ t('v2.setup.action.noActions') }}</p>
  </div>
</template>
