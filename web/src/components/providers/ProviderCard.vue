<script setup lang="ts">
/**
 * One provider inside a stage: identity, key state, model/voice summary, the
 * fields people actually change, and a collapsed group of troubleshooting fields.
 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { providerDescription, providerName, t } from '../../i18n'
import {
  coerceValue,
  equalValue,
  EXCLUDED,
  fieldKey,
  kindLabel,
  roleLabel,
  secretsFor,
  voiceLabel,
  type ProviderDef,
  type SecretMeta,
  type Stage,
  type VoiceOption,
} from './types'
import ProviderField from './ProviderField.vue'
import VoiceAudition from './VoiceAudition.vue'

const props = defineProps<{
  def: ProviderDef
  stage: Stage
  roleIndex: number
  fieldDraft: Record<string, unknown>
  fieldBaseline: Record<string, unknown>
  errors: Record<string, string>
  secrets: Record<string, SecretMeta>
  voices: VoiceOption[]
  auditionReady: boolean
  disabled?: boolean
}>()

const emit = defineEmits<{ 'update-field': [path: string, value: unknown] }>()

const name = computed(() => props.def.name)
const excluded = computed(() => EXCLUDED[props.stage].includes(name.value))
const secretNames = computed(() => secretsFor(props.secrets, props.stage, name.value))
const configuredKeys = computed(() => secretNames.value.filter((key) => props.secrets[key]?.has_value).length)
const keyText = computed(() =>
  secretNames.value.length
    ? t('providers.key.summary', { configured: configuredKeys.value, total: secretNames.value.length })
    : t('providers.key.noneNeeded'),
)

function valueOf(field: { path: string }): unknown {
  return props.fieldDraft[field.path]
}

function dirty(field: { path: string }): boolean {
  return !equalValue(props.fieldDraft[field.path], props.fieldBaseline[field.path])
}

function onUpdate(field: { path: string; kind: string; options?: unknown }, value: unknown): void {
  const catalogField = props.def.fields.find((item) => item.path === field.path)
  emit('update-field', field.path, catalogField ? coerceValue(catalogField, value) : value)
}

const voiceField = computed(() => props.def.fields.find((field) => fieldKey(field, name.value) === 'voice'))
const modelField = computed(() => props.def.fields.find((field) => fieldKey(field, name.value) === 'model'))

const modelSummary = computed(() => {
  const value = modelField.value ? valueOf(modelField.value) : ''
  return value == null || value === '' ? '' : String(value)
})

const voiceSummary = computed(() => {
  if (!voiceField.value) return ''
  const value = valueOf(voiceField.value)
  if (value == null || value === '') return ''
  return voiceLabel(String(value), props.voices, voiceField.value)
})

const isVoice = (field: { path: string }): boolean => field.path === voiceField.value?.path

const prosody = computed(() => {
  const prefix = `providers.catalog.${name.value}.`
  return {
    rate: props.fieldDraft[`${prefix}speech_rate`],
    emotion: props.fieldDraft[`${prefix}emotion`],
    scale: props.fieldDraft[`${prefix}emotion_scale`],
  }
})
</script>

<template>
  <article class="p-card" :class="{ primary: roleIndex === 0, excluded }">
    <header class="p-head">
      <div class="p-title-block">
        <div class="p-title">
          <h3>{{ providerName(def.name) }}</h3>
          <code class="p-id mono">{{ def.name }}</code>
          <span class="badge">{{ kindLabel(def.kind) }}</span>
          <span class="badge" :class="roleIndex === 0 ? 'badge-accent' : 'badge-muted'">{{ roleLabel(roleIndex) }}</span>
          <span v-if="excluded" class="badge badge-warn">{{ t('providers.unused.removed') }}</span>
        </div>
        <p v-if="providerDescription(def.name)" class="p-desc">{{ providerDescription(def.name) }}</p>
      </div>
      <div class="p-key" :class="configuredKeys === secretNames.length || !secretNames.length ? 'ok' : 'warn'">
        <span class="dot" :class="configuredKeys === secretNames.length || !secretNames.length ? 'ok' : 'warn'" />
        <span>{{ keyText }}</span>
        <RouterLink v-if="secretNames.length" class="p-key-link" to="/secrets">{{ t('providers.key.manage') }}</RouterLink>
      </div>
    </header>

    <div v-if="modelSummary || voiceSummary" class="p-summary">
      <span v-if="modelSummary" class="p-summary-item">
        <span class="p-summary-k">{{ t('providers.summary.model') }}</span>
        <code>{{ modelSummary }}</code>
      </span>
      <span v-if="voiceSummary" class="p-summary-item">
        <span class="p-summary-k">{{ t('providers.summary.voice') }}</span>
        <span>{{ voiceSummary }}</span>
      </span>
    </div>

    <div class="p-grid">
      <ProviderField
        v-for="field in def.core"
        :key="field.path"
        :field="field"
        :name="name"
        :value="valueOf(field)"
        :dirty="dirty(field)"
        :invalid="Boolean(errors[field.path])"
        :error="errors[field.path]"
        :disabled="disabled || excluded"
        :voice-options="isVoice(field) ? voices : undefined"
        :class="{ wide: isVoice(field) }"
        @update="onUpdate(field, $event)"
      >
        <template v-if="isVoice(field)" #extra>
          <VoiceAudition
            v-if="stage === 'tts'"
            stage="tts"
            :provider="name"
            :voice="String(valueOf(field) ?? '')"
            :speech-rate="prosody.rate"
            :emotion="prosody.emotion"
            :emotion-scale="prosody.scale"
            :ready="auditionReady && !excluded"
          />
        </template>
      </ProviderField>
    </div>

    <details v-if="def.advanced.length" class="p-advanced">
      <summary>
        <span>{{ t('providers.advanced', { n: def.advanced.length }) }}</span>
        <span class="p-advanced-hint">{{ t('providers.advancedHint') }}</span>
      </summary>
      <div class="p-grid advanced-grid">
        <ProviderField
          v-for="field in def.advanced"
          :key="field.path"
          :field="field"
          :name="name"
          :value="valueOf(field)"
          :dirty="dirty(field)"
          :invalid="Boolean(errors[field.path])"
          :error="errors[field.path]"
          :disabled="disabled || excluded"
          @update="onUpdate(field, $event)"
        />
      </div>
    </details>
  </article>
</template>

<style scoped>
.p-card {
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: var(--surface);
  padding: var(--s4) var(--s5);
  position: relative;
  min-width: 0;
}

.p-card.primary {
  border-left: 2px solid var(--accent);
}

.p-card.excluded {
  background: var(--surface-2);
}

.p-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s3);
  flex-wrap: wrap;
}

.p-title-block {
  min-width: 0;
}

.p-title {
  display: flex;
  align-items: baseline;
  gap: var(--s2);
  flex-wrap: wrap;
}

.p-title h3 {
  font-family: var(--font-ui);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.p-id {
  font-size: 11px;
  color: var(--muted);
}

.p-desc {
  margin-top: 3px;
  color: var(--muted);
  font-size: 11.5px;
  line-height: 1.5;
  max-width: 68ch;
}

.p-key {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.04em;
  color: var(--muted);
  white-space: nowrap;
}

.p-key .dot {
  width: 6px;
  height: 6px;
}

.p-key-link {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 2px;
  white-space: nowrap;
}

.p-summary {
  display: flex;
  gap: var(--s4);
  flex-wrap: wrap;
  margin-top: var(--s3);
  padding: 6px 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.p-summary-item {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  min-width: 0;
}

.p-summary-k {
  font-size: 9px;
  letter-spacing: 0.1em;
  color: var(--muted);
}

.p-summary-item code {
  font-size: 11.5px;
  overflow-wrap: anywhere;
  background: none;
  padding: 0;
}

.p-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--s4) var(--s5);
  margin-top: var(--s4);
}

.p-grid .wide {
  grid-column: 1 / -1;
}

.p-advanced {
  margin-top: var(--s4);
  padding-top: var(--s3);
  border-top: 1px dashed var(--line-strong);
}

.p-advanced summary {
  display: flex;
  align-items: baseline;
  gap: var(--s2);
  flex-wrap: wrap;
  cursor: pointer;
  color: var(--ink-2);
  font-family: var(--font-mono);
  font-size: 10.5px;
  letter-spacing: 0.05em;
  list-style: none;
}

.p-advanced summary::-webkit-details-marker {
  display: none;
}

.p-advanced-hint {
  color: var(--muted);
  letter-spacing: 0;
  font-family: var(--font-ui);
  font-size: 11px;
}

.advanced-grid {
  margin-top: var(--s3);
}

@media (max-width: 960px) {
  .p-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 720px) {
  .p-card {
    padding: var(--s4);
  }

  .p-key-link {
    white-space: normal;
    min-height: 44px;
    display: inline-flex;
    align-items: center;
  }

  .p-advanced summary {
    min-height: 44px;
    align-items: center;
  }
}
</style>
