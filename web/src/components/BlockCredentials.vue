<script setup lang="ts">
/**
 * Music source + credentials (panel block `credentials`).
 *
 * When the block declares `sources_href`, the backend registry drives the whole
 * section: the source select lists real presets, only the selected preset's credential
 * slots are rendered, and Save sends `{source, ...touched}` in one write. The backend
 * validates the preset id (unknown = 422) and applies a source switch at runtime.
 *
 * Values are write-only: the API returns presence only, fields start empty, only
 * touched fields are sent, and an emptied field is sent as "" (delete semantics).
 * Password/token inputs are cleared after a successful save and never stored outside
 * this component's drafts. Without `sources_href` the block keeps its original
 * single-source behaviour (static `fields`).
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import type { PanelBlock } from '../panels'
import { lookup } from '../panels'
import {
  api,
  ApiError,
  type MusicCredentialField,
  type MusicCredentialsPatch,
  type MusicSourcesDoc,
  type MusicTestResult,
} from '../api'
import { pickLocalized, t } from '../i18n'
import AppIcon from './AppIcon.vue'

const props = defineProps<{ block: PanelBlock; data: unknown; refresh?: () => void }>()

/** The registry shape and the panel's static fallback share this view. */
interface FieldView {
  key: string
  kind: string
  required: boolean
  label: unknown
  hint: unknown
  placeholder: unknown
}

const sources = ref<MusicSourcesDoc | null>(null)
const sourcesError = ref('')
const selectedSource = ref('')
const sourceTouched = ref(false)

const savedSource = computed(() => sources.value?.current ?? '')
const activePreset = computed(() =>
  sources.value?.sources.find((preset) => preset.id === selectedSource.value) ?? null,
)

const fallbackFields = computed<FieldView[]>(() =>
  (props.block.fields ?? []).map((field) => ({
    key: field.key,
    kind: field.kind ?? 'text',
    required: false,
    label: field.label,
    hint: field.hint,
    placeholder: field.placeholder,
  })),
)

const fields = computed<FieldView[]>(() => {
  if (!sources.value) return fallbackFields.value
  const preset = activePreset.value
  if (!preset) return []
  return preset.credential_fields.map((field: MusicCredentialField) => ({
    key: field.key,
    kind: field.kind,
    required: field.required,
    label: field.label,
    hint: field.hint,
    placeholder: field.placeholder,
  }))
})

/** `status_key` is a panel-data path; the registry is fresher when it is loaded. */
const dataPresence = computed(() => {
  const key = props.block.status_key
  if (!key) return false
  return Boolean(lookup(key.startsWith('$') ? key : `$.${key}`, props.data))
})
const hasCredentials = computed(() => {
  if (!sources.value) return dataPresence.value
  return sources.value.credentials.ready && selectedSource.value === savedSource.value
})
const sourceKnown = computed(() => !sources.value || sources.value.source_known)
const sourcePending = computed(() =>
  Boolean(sources.value && selectedSource.value !== savedSource.value),
)

const draft = reactive<Record<string, string>>({})
const touched = reactive<Record<string, boolean>>({})
const busy = ref<'' | 'save' | 'test' | 'clear'>('')
const error = ref('')
const sourceError = ref('')
const savedMessage = ref('')
const testResult = ref<MusicTestResult | null>(null)
const fieldErrors = reactive<Record<string, string>>({})

const dirtyFields = computed(() => fields.value.filter((field) => touched[field.key]).length)
const dirtyCount = computed(() => dirtyFields.value + (sourcePending.value ? 1 : 0))
const canSave = computed(() =>
  dirtyCount.value > 0 && busy.value === '' && Boolean(selectedSource.value)
    && (sourceKnown.value || !sources.value),
)
const canClear = computed(() =>
  hasCredentials.value && !sourcePending.value && busy.value === '',
)

function draftOf(key: string): string {
  return draft[key] ?? ''
}

function onInput(key: string, value: string): void {
  draft[key] = value
  touched[key] = true
  delete fieldErrors[key]
  error.value = ''
  savedMessage.value = ''
  testResult.value = null
}

function onSourceChange(value: string): void {
  selectedSource.value = value
  sourceTouched.value = true
  sourceError.value = ''
  error.value = ''
  savedMessage.value = ''
  testResult.value = null
  for (const field of fields.value) delete fieldErrors[field.key]
}

function clearForm(): void {
  for (const field of fields.value) {
    draft[field.key] = ''
    touched[field.key] = false
    delete fieldErrors[field.key]
  }
}

function fieldType(field: FieldView): string {
  return field.kind === 'password' ? 'password' : 'text'
}

function placeHolder(field: FieldView): string {
  return pickLocalized(field.placeholder) || defaultPlaceholder(field.key)
}

function defaultPlaceholder(key: string): string {
  if (key === 'url') return t('music.credentialsUrlPlaceholder')
  if (key === 'user') return t('music.credentialsUserPlaceholder')
  if (key === 'password') return t('music.credentialsPasswordPlaceholder')
  return ''
}

function fieldHint(field: FieldView): string {
  return pickLocalized(field.hint) || defaultHint(field.key)
}

function defaultHint(key: string): string {
  if (key === 'password' || key === 'token') return t('music.credentialsWriteOnly')
  return ''
}

/** Only touched fields travel; empty means "delete this field" on the backend. */
function payload(): MusicCredentialsPatch {
  const values: MusicCredentialsPatch = {}
  for (const field of fields.value) {
    if (!touched[field.key]) continue
    values[field.key] = draftOf(field.key).trim()
  }
  return values
}

async function loadSources(): Promise<void> {
  const href = props.block.sources_href
  if (!href) return
  try {
    const doc = await api.musicSources(href)
    sources.value = doc
    sourcesError.value = ''
    if (!sourceTouched.value) {
      selectedSource.value = doc.current
    } else if (!doc.sources.some((preset) => preset.id === selectedSource.value)) {
      // The registry no longer offers the staged id (e.g. a release removed it).
      selectedSource.value = doc.current
      sourceTouched.value = false
    }
  } catch (exc) {
    sourcesError.value = exc instanceof Error ? exc.message : String(exc)
    if (!selectedSource.value) {
      const fromData = lookup('$.source', props.data)
      selectedSource.value = typeof fromData === 'string' ? fromData : ''
    }
  }
}

watch(() => lookup('$.source', props.data), (next) => {
  if (typeof next !== 'string' || next === selectedSource.value) return
  if (!sourceTouched.value) {
    selectedSource.value = next
    void loadSources()
  }
})

onMounted(loadSources)

async function save(): Promise<void> {
  if (!canSave.value) return
  busy.value = 'save'
  error.value = ''
  sourceError.value = ''
  savedMessage.value = ''
  testResult.value = null
  for (const field of fields.value) delete fieldErrors[field.key]
  try {
    const values = payload()
    const body: MusicCredentialsPatch = { ...values }
    if (props.block.sources_href && selectedSource.value) body.source = selectedSource.value
    const result = await api.saveMusicCredentials(body, props.block.save?.href)
    clearForm()
    sourceTouched.value = false
    savedMessage.value = result.has_credentials
      ? t('music.credentialsSaved')
      : t('music.credentialsSavedIncomplete')
    await loadSources()
    props.refresh?.()
  } catch (exc) {
    if (exc instanceof ApiError) {
      for (const issue of exc.issues) {
        const key = String((issue.loc ?? [])[0] ?? '')
        if (key === 'source') {
          sourceError.value = issue.msg
        } else if (key && fields.value.some((field) => field.key === key)) {
          fieldErrors[key] = key === 'url' && /must start with http/i.test(issue.msg)
            ? t('music.credentialsUrlInvalid')
            : issue.msg
        }
      }
    }
    const count = Object.values(fieldErrors).filter(Boolean).length
    error.value = sourceError.value
      || (count
        ? t('music.credentialsValidationFailed', { n: count })
        : t('music.credentialsError', {
            message: exc instanceof Error ? exc.message : String(exc),
          }))
  } finally {
    busy.value = ''
  }
}

async function test(): Promise<void> {
  if (busy.value) return
  if (!sourceKnown.value) {
    testResult.value = {
      ok: false, latency_ms: null, error: t('music.sourceUnknownTest'),
    }
    return
  }
  const values = payload()
  if (!hasCredentials.value && !Object.keys(values).length && !sourcePending.value) {
    testResult.value = { ok: false, latency_ms: null, error: t('music.credentialsTestMissing') }
    return
  }
  busy.value = 'test'
  error.value = ''
  savedMessage.value = ''
  testResult.value = null
  try {
    const body: MusicCredentialsPatch = { ...values }
    if (props.block.sources_href && selectedSource.value) body.source = selectedSource.value
    testResult.value = await api.testMusic(body, props.block.test?.href)
  } catch (exc) {
    testResult.value = {
      ok: false,
      latency_ms: null,
      error: exc instanceof Error ? exc.message : String(exc),
    }
  } finally {
    busy.value = ''
  }
}

async function clearCredentials(): Promise<void> {
  if (!canClear.value) return
  if (!window.confirm(t('music.credentialsClearConfirm'))) return
  busy.value = 'clear'
  error.value = ''
  savedMessage.value = ''
  testResult.value = null
  try {
    const values: MusicCredentialsPatch = {}
    for (const field of fields.value) values[field.key] = ''
    const result = await api.saveMusicCredentials(values, props.block.save?.href)
    clearForm()
    savedMessage.value = result.has_credentials
      ? t('music.credentialsSaved')
      : t('music.credentialsCleared')
    await loadSources()
    props.refresh?.()
  } catch (exc) {
    error.value = t('music.credentialsError', {
      message: exc instanceof Error ? exc.message : String(exc),
    })
  } finally {
    busy.value = ''
  }
}

function badgeClass(): string {
  if (!sourceKnown.value) return 'badge-fail'
  if (sourcePending.value) return 'badge-warn'
  return hasCredentials.value ? 'badge-ok' : 'badge-warn'
}

function badgeText(): string {
  if (!sourceKnown.value) return t('music.sourceUnknown')
  if (sourcePending.value) return t('music.sourcePending')
  return hasCredentials.value
    ? t('music.credentialsConfigured')
    : t('music.credentialsMissing')
}

function presetLabel(preset: { name: unknown; protocol_family: string; default_port: number | null }): string {
  const name = pickLocalized(preset.name)
  const port = preset.default_port ? ` :${preset.default_port}` : ''
  return `${name} · ${preset.protocol_family.toUpperCase()}${port}`
}

function testMessage(result: MusicTestResult): string {
  if (result.ok) return t('music.credentialsTestOk', { ms: result.latency_ms ?? '?' })
  return t('music.credentialsTestFail', { message: result.error || t('common.error') })
}
</script>

<template>
  <section class="block credentials-block">
    <div class="block-head">
      <h2>{{ pickLocalized(block.title) || t('music.credentialsTitle') }}</h2>
      <span class="badge" :class="badgeClass()">{{ badgeText() }}</span>
    </div>

    <p class="field-hint credentials-note">{{ pickLocalized(block.note) || t('music.credentialsNote') }}</p>

    <p v-if="sourcesError" class="note fail" role="alert">
      {{ t('music.sourceRegistryError', { message: sourcesError }) }}
    </p>

    <div v-if="block.sources_href" class="source-picker">
      <div class="field-meta">
        <label class="field-label" for="cred-source">{{ t('music.sourceLabel') }}</label>
        <p class="field-hint">{{ t('music.sourceHint') }}</p>
      </div>
      <div class="field-control">
        <select
          id="cred-source"
          :value="selectedSource"
          :disabled="busy !== '' || !sources"
          :class="{ invalid: sourceError }"
          @change="onSourceChange(($event.target as HTMLSelectElement).value)"
        >
          <option v-if="sources && !sources.source_known" :value="selectedSource">
            {{ t('music.sourceUnknownOption', { id: selectedSource }) }}
          </option>
          <option v-for="preset in sources?.sources ?? []" :key="preset.id" :value="preset.id">
            {{ presetLabel(preset) }}
          </option>
        </select>
        <p v-if="sourceError" class="field-error">{{ sourceError }}</p>
        <p v-else-if="sources && !sources.source_known" class="field-error">
          {{ t('music.sourceUnknownError', { id: sources.current }) }}
        </p>
        <p v-if="activePreset" class="field-hint source-description">
          {{ pickLocalized(activePreset.description) }}
          <a :href="activePreset.docs_url" target="_blank" rel="noreferrer">
            {{ t('music.sourceDocs') }}
          </a>
        </p>
        <p v-if="sources?.restart_required" class="field-hint source-restart">
          {{ t('music.sourceRestart', { running: sources.running_source, current: sources.current }) }}
        </p>
      </div>
    </div>

    <p v-if="!fields.length" class="empty">{{ pickLocalized(block.empty) || t('music.credentialsEmpty') }}</p>

    <div v-else class="credentials-fields">
      <div
        v-for="field in fields"
        :key="field.key"
        class="field-row"
        :class="{ invalid: fieldErrors[field.key] }"
      >
        <div class="field-meta">
          <label class="field-label" :for="`cred-${field.key}`">
            {{ pickLocalized(field.label) || field.key }}
          </label>
          <p v-if="fieldHint(field)" class="field-hint">{{ fieldHint(field) }}</p>
        </div>
        <div class="field-control">
          <input
            :id="`cred-${field.key}`"
            :value="draftOf(field.key)"
            :type="fieldType(field)"
            :placeholder="placeHolder(field)"
            :autocomplete="field.kind === 'password' ? 'new-password' : 'off'"
            spellcheck="false"
            :disabled="busy !== ''"
            @input="onInput(field.key, ($event.target as HTMLInputElement).value)"
          />
          <p v-if="fieldErrors[field.key]" class="field-error">{{ fieldErrors[field.key] }}</p>
        </div>
      </div>
    </div>

    <div class="credentials-actions">
      <button type="button" class="btn btn-primary" :disabled="!canSave" @click="save">
        <AppIcon name="check" :size="14" />
        {{ busy === 'save' ? t('common.saving') : t('music.credentialsSave') }}
      </button>
      <button type="button" class="btn" :disabled="busy !== ''" @click="test">
        <AppIcon name="activity" :size="14" />
        {{ busy === 'test' ? t('music.credentialsTesting') : t('music.credentialsTest') }}
      </button>
      <button v-if="hasCredentials" type="button" class="btn btn-danger" :disabled="!canClear" @click="clearCredentials">
        {{ busy === 'clear' ? t('common.saving') : t('music.credentialsClear') }}
      </button>
      <span v-if="dirtyCount" class="badge badge-warn">{{ t('music.credentialsDirty', { n: dirtyCount }) }}</span>
      <span v-else class="meta">{{ t('music.credentialsNoChanges') }}</span>
    </div>

    <div v-if="testResult" class="credentials-test" :class="testResult.ok ? 'ok' : 'fail'" role="status">
      <AppIcon :name="testResult.ok ? 'check' : 'alert'" :size="14" />
      <span>{{ testMessage(testResult) }}</span>
    </div>
    <p v-if="error" class="note fail" role="alert">{{ error }}</p>
    <p v-if="savedMessage" class="note ok" role="status">{{ savedMessage }}</p>
    <p v-if="block.hint" class="field-hint credentials-footnote">{{ pickLocalized(block.hint) }}</p>
  </section>
</template>

<style scoped>
.credentials-block {
  min-width: 0;
}

.credentials-note {
  max-width: 76ch;
  margin-bottom: var(--s2);
}

.source-picker {
  display: grid;
  grid-template-columns: minmax(200px, 280px) minmax(0, 1fr);
  gap: var(--s3);
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
  margin-bottom: var(--s2);
}

.source-picker .field-control {
  max-width: 520px;
}

.source-picker select {
  width: 100%;
}

.source-picker select.invalid {
  border-color: var(--fail);
}

.source-description {
  margin-top: 6px;
}

.source-description a {
  margin-left: 6px;
  color: var(--accent);
  text-decoration: underline;
}

.source-restart {
  margin-top: 6px;
  color: var(--warn);
}

.credentials-fields .field-row {
  grid-template-columns: minmax(200px, 280px) minmax(0, 1fr);
  padding: 12px 0;
}

.credentials-fields .field-control {
  max-width: 440px;
}

.credentials-actions {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  margin-top: var(--s4);
  padding-top: var(--s3);
  border-top: 1px solid var(--line);
}

.credentials-actions .btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.credentials-test {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: var(--s3);
  padding: 6px 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  font-size: 12.5px;
}

.credentials-test.ok {
  color: var(--ok);
  border-color: var(--ok);
  background: var(--ok-soft);
}

.credentials-test.fail {
  color: var(--fail);
  border-color: var(--fail);
  background: var(--fail-soft);
}

.credentials-footnote {
  margin-top: var(--s3);
}

@media (max-width: 720px) {
  .credentials-fields .field-row,
  .source-picker {
    grid-template-columns: minmax(0, 1fr);
    gap: 4px;
    padding: 12px 0;
  }

  .credentials-fields .field-control,
  .source-picker .field-control {
    max-width: none;
  }

  .credentials-actions .btn {
    flex: 1 1 auto;
  }
}
</style>
