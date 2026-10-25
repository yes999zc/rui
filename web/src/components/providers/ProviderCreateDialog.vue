<script setup lang="ts">
/**
 * "New provider" flow: stage → preset (or custom compatible endpoint) → the fields
 * the provider really needs → write-only key(s) → test → create.
 *
 * The preset list is server-owned: display names, docs, required fields and probe
 * support all come from `GET /api/v1/providers/presets`. Keys stay in this component
 * until create; they are never persisted in the browser, logged, or echoed by the API.
 */
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import AppIcon from '../AppIcon.vue'
import {
  api,
  ApiError,
  type CreateProviderPayload,
  type CreateProviderResult,
  type ProviderPreset,
  type ProviderPresetCredential,
  type ProviderPresetsDoc,
  type ProviderProbeResult,
} from '../../api'
import { pickLocalized, t } from '../../i18n'
import { STAGES, stageShort, stageTitle, type Stage } from './types'

const props = defineProps<{
  open: boolean
  presets: ProviderPresetsDoc | null
  busy?: boolean
}>()

const emit = defineEmits<{
  close: []
  created: [result: CreateProviderResult]
}>()

const stage = ref<Stage>('llm')
const mode = ref<'preset' | 'custom'>('preset')
const presetId = ref('')
const name = ref('')
const baseUrl = ref('')
const model = ref('')
const apiKeyEnv = ref('')
const kind = ref('openai_compat')
const keys = reactive<Record<string, string>>({})
const setPrimary = ref(false)
const testing = ref(false)
const creating = ref(false)
const error = ref('')
const probe = ref<ProviderProbeResult | null>(null)

const stagePresets = computed<ProviderPreset[]>(() =>
  (props.presets?.presets ?? []).filter((item) => item.stage === stage.value),
)
const preset = computed<ProviderPreset | null>(
  () => stagePresets.value.find((item) => item.id === presetId.value) ?? null,
)
const customStage = computed(() => props.presets?.custom.stages[stage.value])
// Custom mode is the OpenAI-compatible escape hatch. Vendor-specific kinds
// (sherpa/volc/xfyun) have presets, so the UI does not offer half-filled versions.
const customKinds = computed(() => {
  const kinds = customStage.value?.kinds ?? []
  const compatible = kinds.filter((item) => item === 'openai_compat')
  return compatible.length ? compatible : ['openai_compat']
})
const presetCredentials = computed<ProviderPresetCredential[]>(
  () => preset.value?.credentials ?? [],
)
const customCredentials = computed<ProviderPresetCredential[]>(() =>
  mode.value === 'custom' && apiKeyEnv.value
    ? [{ role: 'api_key', name: apiKeyEnv.value, label: 'API Key', required: true }]
    : [],
)
const credentials = computed<ProviderPresetCredential[]>(() =>
  mode.value === 'preset' ? presetCredentials.value : customCredentials.value,
)
const requiredLabel = computed(() => {
  if (mode.value === 'preset') {
    const fields = preset.value?.required_fields ?? []
    return fields.length
      ? t('providers.create.required', { fields: fields.join(', ') })
      : t('providers.create.keyless')
  }
  return apiKeyEnv.value
    ? t('providers.create.required', { fields: 'api_key' })
    : t('providers.create.keyless')
})
const canSubmit = computed(() => {
  if (!props.open || creating.value || testing.value) return false
  if (!name.value.trim()) return false
  if (mode.value === 'preset') return Boolean(preset.value)
  if (!baseUrl.value.trim().startsWith('http')) return false
  if (customStage.value && !customKinds.value.includes(kind.value)) return false
  return true
})

function localized(value: unknown): string {
  return pickLocalized(value)
}

function errorText(exc: unknown): string {
  if (exc instanceof ApiError) {
    const detail = exc.detail
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const message = (detail as { message?: unknown }).message
      if (typeof message === 'string' && message) return message
    }
    return exc.message
  }
  return exc instanceof Error ? exc.message : String(exc)
}

function clearForm(): void {
  name.value = ''
  baseUrl.value = ''
  model.value = ''
  apiKeyEnv.value = ''
  kind.value = customStage.value?.default_kind || 'openai_compat'
  for (const key of Object.keys(keys)) delete keys[key]
  probe.value = null
  error.value = ''
}

function resetForStage(next: Stage): void {
  stage.value = next
  presetId.value = ''
  mode.value = 'preset'
  setPrimary.value = false
  clearForm()
}

function selectPreset(item: ProviderPreset): void {
  presetId.value = item.id
  name.value = item.default_name
  baseUrl.value = item.base_url
  model.value = item.model
  apiKeyEnv.value = item.api_key_env
  kind.value = item.kind
  for (const key of Object.keys(keys)) delete keys[key]
  probe.value = null
  error.value = ''
}

function switchMode(next: 'preset' | 'custom'): void {
  if (mode.value === next) return
  mode.value = next
  presetId.value = ''
  setPrimary.value = false
  clearForm()
  if (next === 'custom') {
    kind.value = customStage.value?.default_kind || 'openai_compat'
  }
}

function resetAndClose(): void {
  if (creating.value) return
  clearForm()
  presetId.value = ''
  emit('close')
}

function payload(): CreateProviderPayload {
  const values: Record<string, string> = {}
  const allowed = new Set(credentials.value.map((item) => item.name))
  for (const [envName, value] of Object.entries(keys)) {
    if (value && allowed.has(envName)) values[envName] = value
  }
  if (mode.value === 'preset') {
    return {
      preset_id: presetId.value,
      name: name.value.trim() || undefined,
      base_url: baseUrl.value.trim() || undefined,
      model: model.value.trim() || undefined,
      keys: values,
      set_primary: setPrimary.value,
    }
  }
  return {
    custom: {
      name: name.value.trim(),
      stage: stage.value,
      kind: kind.value,
      base_url: baseUrl.value.trim(),
      model: model.value.trim(),
      api_key_env: apiKeyEnv.value.trim() || undefined,
    },
    keys: values,
    set_primary: setPrimary.value,
  }
}

function validate(): boolean {
  error.value = ''
  if (!name.value.trim()) {
    error.value = t('providers.create.needName')
    return false
  }
  if (mode.value === 'preset' && !preset.value) {
    error.value = t('providers.create.needPreset')
    return false
  }
  if (mode.value === 'custom') {
    if (!baseUrl.value.trim().startsWith('http')) {
      error.value = t('providers.create.needBase')
      return false
    }
    if (apiKeyEnv.value && !apiKeyEnv.value.trim().startsWith(props.presets?.custom.env_prefix ?? 'RUI_')) {
      error.value = t('providers.create.needKeyEnv')
      return false
    }
  }
  return true
}

async function testConnection(): Promise<void> {
  if (!validate() || testing.value) return
  testing.value = true
  probe.value = null
  try {
    probe.value = await api.probeProvider(payload())
  } catch (exc) {
    probe.value = null
    error.value = t('providers.create.testFail', { message: errorText(exc) })
  } finally {
    testing.value = false
  }
}

async function create(): Promise<void> {
  if (!validate() || creating.value) return
  creating.value = true
  error.value = ''
  try {
    const result = await api.createProvider(payload())
    clearForm()
    emit('created', result)
  } catch (exc) {
    error.value = t('providers.create.failed', { message: errorText(exc) })
  } finally {
    creating.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    if (!open) return
    resetForStage(stage.value)
  },
)

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && props.open) resetAndClose()
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div v-if="open" class="pc-backdrop" @click.self="resetAndClose">
    <section class="pc-dialog" role="dialog" aria-modal="true" aria-labelledby="pc-title">
      <header class="pc-head">
        <div>
          <h2 id="pc-title">{{ t('providers.create.title') }}</h2>
          <p class="pc-lede">{{ t('providers.create.lede') }}</p>
        </div>
        <button type="button" class="pc-close" :aria-label="t('providers.create.close')" @click="resetAndClose">
          <AppIcon name="close" :size="16" />
        </button>
      </header>

      <div class="pc-body">
        <section class="pc-step">
          <span class="kicker">{{ t('providers.create.stage') }}</span>
          <div class="pc-stages" role="tablist">
            <button
              v-for="item in STAGES"
              :key="item"
              type="button"
              role="tab"
              class="pc-stage"
              :class="{ active: stage === item }"
              :aria-selected="stage === item"
              @click="resetForStage(item)"
            >
              <strong>{{ stageTitle(item) }}</strong>
              <span>{{ stageShort(item) }}</span>
            </button>
          </div>
        </section>

        <section class="pc-step">
          <span class="kicker">{{ t('providers.create.preset') }}</span>
          <div class="pc-mode">
            <button type="button" :class="{ active: mode === 'preset' }" @click="switchMode('preset')">
              {{ t('providers.create.preset') }}
            </button>
            <button type="button" :class="{ active: mode === 'custom' }" @click="switchMode('custom')">
              {{ t('providers.create.custom') }}
            </button>
          </div>

          <template v-if="mode === 'preset'">
            <p v-if="!stagePresets.length" class="pc-empty">{{ t('providers.create.choosePreset') }}</p>
            <div v-else class="pc-presets">
              <button
                v-for="item in stagePresets"
                :key="item.id"
                type="button"
                class="pc-preset"
                :class="{ active: presetId === item.id }"
                @click="selectPreset(item)"
              >
                <span class="pc-preset-title">
                  <strong>{{ localized(item.display_name) }}</strong>
                  <span class="badge">{{ item.kind }}</span>
                  <span v-if="item.keyless" class="badge badge-muted">{{ t('providers.create.keyless') }}</span>
                  <span v-if="item.probe.supported" class="badge badge-accent">{{ t('providers.create.probeSupported') }}</span>
                  <span v-else class="badge badge-muted">{{ t('providers.create.noProbe') }}</span>
                </span>
                <span class="pc-preset-desc">{{ localized(item.description) }}</span>
                <span class="pc-preset-meta">
                  <code class="mono">{{ item.base_url || item.model }}</code>
                  <span>{{ item.required_fields.length
                    ? t('providers.create.required', { fields: item.required_fields.join(', ') })
                    : t('providers.create.keyless') }}</span>
                </span>
              </button>
            </div>
          </template>
          <template v-else>
            <p class="pc-custom-hint">{{ localized(props.presets?.custom.env_hint) || t('providers.create.customHint') }}</p>
            <div class="pc-grid">
              <label class="pc-field">
                <span>{{ t('providers.create.kind') }}</span>
                <select v-model="kind">
                  <option v-for="item in customKinds" :key="item" :value="item">{{ item }}</option>
                </select>
              </label>
            </div>
          </template>

          <div v-if="mode === 'preset' && preset" class="pc-selected">
            <div class="pc-selected-head">
              <strong>{{ localized(preset.display_name) }}</strong>
              <a v-if="preset.docs_url" :href="preset.docs_url" target="_blank" rel="noreferrer">
                {{ t('providers.create.docs') }}
                <AppIcon name="external" :size="12" />
              </a>
              <span class="badge badge-muted">{{ requiredLabel }}</span>
            </div>

            <div class="pc-grid">
              <label class="pc-field">
                <span>{{ t('providers.create.name') }}</span>
                <input v-model="name" class="mono" :placeholder="preset.default_name" />
                <small>{{ t('providers.create.nameHint') }}</small>
              </label>
              <label v-if="preset.model_required" class="pc-field">
                <span>{{ t('providers.create.modelPath') }}</span>
                <input v-model="model" class="mono" />
              </label>
            </div>

            <details class="pc-advanced">
              <summary>{{ t('providers.create.baseUrl') }} / {{ t('providers.create.model') }}</summary>
              <div class="pc-grid">
                <label class="pc-field">
                  <span>{{ t('providers.create.baseUrl') }}</span>
                  <input v-model="baseUrl" class="mono" :placeholder="t('providers.create.basePlaceholder')" />
                </label>
                <label class="pc-field">
                  <span>{{ t('providers.create.model') }}</span>
                  <input v-model="model" class="mono" :placeholder="t('providers.create.modelPlaceholder')" />
                </label>
              </div>
            </details>
          </div>

          <div v-if="mode === 'custom'" class="pc-selected">
            <div class="pc-grid">
              <label class="pc-field">
                <span>{{ t('providers.create.name') }}</span>
                <input v-model="name" class="mono" :placeholder="t('providers.create.namePlaceholder')" />
                <small>{{ t('providers.create.nameHint') }}</small>
              </label>
              <label class="pc-field">
                <span>{{ t('providers.create.baseUrl') }}</span>
                <input v-model="baseUrl" class="mono" :placeholder="t('providers.create.basePlaceholder')" />
              </label>
              <label class="pc-field">
                <span>{{ t('providers.create.model') }}</span>
                <input v-model="model" class="mono" :placeholder="t('providers.create.modelPlaceholder')" />
              </label>
              <label class="pc-field">
                <span>{{ t('providers.create.apiKeyEnv') }}</span>
                <input v-model="apiKeyEnv" class="mono" :placeholder="t('providers.create.keyEnvPlaceholder')" />
                <small>{{ t('providers.create.customHint') }}</small>
              </label>
            </div>
          </div>

          <div v-if="credentials.length" class="pc-keys">
            <span class="kicker">{{ t('providers.create.keys') }}</span>
            <p class="pc-keys-hint">{{ t('providers.create.keyHint') }}</p>
            <label v-for="item in credentials" :key="item.name" class="pc-field">
              <span>
                {{ localized(item.label) || item.role }}
                <code class="mono">{{ item.name }}</code>
              </span>
              <input
                v-model="keys[item.name]"
                type="password"
                autocomplete="new-password"
                class="mono"
                :placeholder="t('providers.create.keyPlaceholder', { env: item.name })"
              />
            </label>
          </div>
        </section>

        <div v-if="probe" class="banner" :class="probe.ok ? 'banner-ok' : 'banner-fail'" role="status">
          <AppIcon :name="probe.ok ? 'check' : 'alert'" :size="16" class="banner-icon" />
          <span>
            {{ probe.ok
              ? t('providers.create.testOk', { message: probe.message })
              : t('providers.create.testFail', { message: probe.message }) }}
          </span>
        </div>
        <div v-if="error" class="banner banner-fail" role="alert">
          <AppIcon name="alert" :size="16" class="banner-icon" />
          <span>{{ error }}</span>
        </div>
      </div>

      <footer class="pc-foot">
        <label class="pc-primary">
          <input v-model="setPrimary" type="checkbox" :disabled="creating" />
          <span>{{ t('providers.create.setPrimary') }}</span>
        </label>
        <div class="pc-actions">
          <button type="button" class="btn btn-sm" :disabled="testing || creating || !canSubmit" @click="testConnection">
            {{ testing ? t('providers.create.testing') : t('providers.create.test') }}
          </button>
          <button type="button" class="btn btn-sm" :disabled="creating" @click="resetAndClose">
            {{ t('providers.create.close') }}
          </button>
          <button type="button" class="btn btn-sm btn-primary" :disabled="creating || !canSubmit" @click="create">
            {{ creating ? t('providers.create.creating') : t('providers.create.create') }}
          </button>
        </div>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.pc-backdrop {
  position: fixed;
  inset: 0;
  z-index: 80;
  background: rgb(0 0 0 / 42%);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--s4);
}

.pc-dialog {
  width: min(860px, 100%);
  max-height: min(88vh, 900px);
  overflow: auto;
  background: var(--surface);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-md);
  box-shadow: 0 24px 60px rgb(0 0 0 / 26%);
  display: flex;
  flex-direction: column;
}

.pc-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s4);
  padding: var(--s5) var(--s5) var(--s3);
  border-bottom: 1px solid var(--line);
}

.pc-head h2 {
  font-size: 18px;
}

.pc-lede {
  margin-top: 4px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.55;
  max-width: 72ch;
}

.pc-close {
  border: 0;
  background: none;
  color: var(--muted);
  cursor: pointer;
  padding: 4px;
}

.pc-body {
  padding: var(--s4) var(--s5);
  display: flex;
  flex-direction: column;
  gap: var(--s5);
}

.pc-step {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
}

.pc-stages {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--s2);
}

.pc-stage {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--s2);
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-2);
  color: var(--ink);
  cursor: pointer;
  text-align: left;
}

.pc-stage.active {
  border-color: var(--accent);
  box-shadow: inset 0 0 0 1px var(--accent);
}

.pc-stage strong {
  font-size: 13px;
}

.pc-stage span {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 10px;
}

.pc-mode {
  display: inline-flex;
  width: fit-content;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  overflow: hidden;
}

.pc-mode button {
  border: 0;
  background: var(--surface);
  color: var(--muted);
  padding: 6px 12px;
  font-size: 12px;
  cursor: pointer;
}

.pc-mode button.active {
  background: var(--accent-soft, var(--surface-2));
  color: var(--ink);
}

.pc-presets {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--s2);
}

.pc-preset {
  display: flex;
  flex-direction: column;
  gap: 5px;
  text-align: left;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--ink);
  cursor: pointer;
}

.pc-preset:hover,
.pc-preset.active {
  border-color: var(--accent);
}

.pc-preset-title {
  display: flex;
  align-items: baseline;
  gap: 5px;
  flex-wrap: wrap;
}

.pc-preset-title strong {
  font-size: 13px;
}

.pc-preset-desc {
  color: var(--muted);
  font-size: 11.5px;
  line-height: 1.45;
}

.pc-preset-meta {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--s2);
  flex-wrap: wrap;
  color: var(--muted);
  font-size: 10.5px;
}

.pc-preset-meta code {
  overflow-wrap: anywhere;
}

.pc-custom-hint {
  color: var(--muted);
  font-size: 11.5px;
  line-height: 1.5;
}

.pc-selected {
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  padding: var(--s3);
  display: flex;
  flex-direction: column;
  gap: var(--s3);
}

.pc-selected-head {
  display: flex;
  align-items: baseline;
  gap: var(--s2);
  flex-wrap: wrap;
}

.pc-selected-head a {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--accent);
  font-size: 11px;
}

.pc-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--s3);
}

.pc-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.pc-field > span {
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 11.5px;
  color: var(--ink-2);
}

.pc-field input,
.pc-field select {
  width: 100%;
  box-sizing: border-box;
}

.pc-field small {
  color: var(--muted);
  font-size: 10.5px;
  line-height: 1.4;
}

.pc-field code {
  color: var(--muted);
  font-size: 10px;
}

.pc-advanced summary {
  cursor: pointer;
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 10.5px;
}

.pc-advanced .pc-grid {
  margin-top: var(--s2);
}

.pc-keys {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  padding-top: var(--s2);
  border-top: 1px dashed var(--line-strong);
}

.pc-keys-hint {
  color: var(--muted);
  font-size: 10.5px;
}

.pc-empty {
  color: var(--muted);
  font-size: 12px;
}

.pc-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s3);
  flex-wrap: wrap;
  padding: var(--s3) var(--s5) var(--s4);
  border-top: 1px solid var(--line);
  background: var(--surface);
}

.pc-primary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}

.pc-actions {
  display: flex;
  gap: var(--s2);
  flex-wrap: wrap;
}

@media (max-width: 720px) {
  .pc-backdrop {
    padding: 0;
  }

  .pc-dialog {
    width: 100%;
    max-height: 100vh;
    min-height: 100vh;
    border-radius: 0;
  }

  .pc-presets,
  .pc-grid,
  .pc-stages {
    grid-template-columns: minmax(0, 1fr);
  }

  .pc-foot {
    position: sticky;
    bottom: 0;
  }
}
</style>
