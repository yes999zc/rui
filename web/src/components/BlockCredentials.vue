<script setup lang="ts">
/**
 * Music credentials (panel block `credentials`).
 *
 * The backend never returns url/user/password, only `credentials_present`. The form
 * is therefore write-only: fields start empty, only touched fields are sent, and an
 * emptied field is sent as "" (the backend's delete semantics). Password inputs are
 * cleared after a successful save and never stored outside this component's drafts.
 *
 * Saves are explicit; nothing autosaves. The panel's 4 s poll refreshes the badge.
 */
import { computed, reactive, ref, watch } from 'vue'
import type { PanelBlock } from '../panels'
import { lookup } from '../panels'
import { api, ApiError, type MusicTestResult } from '../api'
import { pickLocalized, t } from '../i18n'
import AppIcon from './AppIcon.vue'

const props = defineProps<{ block: PanelBlock; data: unknown; refresh?: () => void }>()

const fields = computed(() => props.block.fields ?? [])

/** `status_key` is a panel-data path (e.g. `credentials_present`). */
const credentialsPresent = computed(() => {
  const key = props.block.status_key
  if (!key) return false
  return Boolean(lookup(key.startsWith('$') ? key : `$.${key}`, props.data))
})

/** The save response is used until the panel poll brings the same value back. */
const statusOverride = ref<boolean | null>(null)
const hasCredentials = computed(() => statusOverride.value ?? credentialsPresent.value)

watch(credentialsPresent, () => { statusOverride.value = null })

const draft = reactive<Record<string, string>>({})
const touched = reactive<Record<string, boolean>>({})
const busy = ref<'' | 'save' | 'test' | 'clear'>('')
const error = ref('')
const savedMessage = ref('')
const testResult = ref<MusicTestResult | null>(null)
const fieldErrors = reactive<Record<string, string>>({})

const dirtyCount = computed(() => fields.value.filter((field) => touched[field.key]).length)
const canSave = computed(() => dirtyCount.value > 0 && busy.value === '')

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

function clearForm(): void {
  for (const field of fields.value) {
    draft[field.key] = ''
    touched[field.key] = false
    delete fieldErrors[field.key]
  }
}

function placeHolder(field: { key: string }): string {
  if (field.key === 'url') return t('music.credentialsUrlPlaceholder')
  if (field.key === 'user') return t('music.credentialsUserPlaceholder')
  if (field.key === 'password') return t('music.credentialsPasswordPlaceholder')
  return ''
}

function fieldHint(field: { key: string; hint?: string }): string {
  return pickLocalized(field.hint)
}

function defaultHint(field: { key: string }): string {
  if (field.key === 'password') return t('music.credentialsWriteOnly')
  return ''
}

/** Only touched fields travel; empty means "delete this field" on the backend. */
function payload(): Record<string, string> {
  const values: Record<string, string> = {}
  for (const field of fields.value) {
    if (!touched[field.key]) continue
    values[field.key] = draftOf(field.key).trim()
  }
  return values
}

async function save(): Promise<void> {
  if (!canSave.value) return
  busy.value = 'save'
  error.value = ''
  savedMessage.value = ''
  testResult.value = null
  for (const field of fields.value) delete fieldErrors[field.key]
  try {
    const result = await api.saveMusicCredentials(payload(), props.block.save?.href)
    statusOverride.value = result.has_credentials
    clearForm()
    savedMessage.value = t('music.credentialsSaved')
    props.refresh?.()
  } catch (exc) {
    if (exc instanceof ApiError) {
      for (const issue of exc.issues) {
        const key = String((issue.loc ?? [])[0] ?? '')
        if (key && fields.value.some((field) => field.key === key)) {
          fieldErrors[key] = key === 'url' && /must start with http/i.test(issue.msg)
            ? t('music.credentialsUrlInvalid')
            : issue.msg
        }
      }
    }
    const count = Object.values(fieldErrors).filter(Boolean).length
    error.value = count
      ? t('music.credentialsValidationFailed', { n: count })
      : t('music.credentialsError', { message: exc instanceof Error ? exc.message : String(exc) })
  } finally {
    busy.value = ''
  }
}

async function test(): Promise<void> {
  if (busy.value) return
  const values = payload()
  if (!hasCredentials.value && !Object.keys(values).length) {
    testResult.value = { ok: false, latency_ms: null, error: t('music.credentialsTestMissing') }
    return
  }
  busy.value = 'test'
  error.value = ''
  savedMessage.value = ''
  testResult.value = null
  try {
    testResult.value = await api.testMusic(values, props.block.test?.href)
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
  if (busy.value || !hasCredentials.value) return
  if (!window.confirm(t('music.credentialsClearConfirm'))) return
  busy.value = 'clear'
  error.value = ''
  savedMessage.value = ''
  testResult.value = null
  try {
    const result = await api.saveMusicCredentials(
      { url: '', user: '', password: '' },
      props.block.save?.href,
    )
    statusOverride.value = result.has_credentials
    clearForm()
    savedMessage.value = t('music.credentialsCleared')
    props.refresh?.()
  } catch (exc) {
    error.value = t('music.credentialsError', {
      message: exc instanceof Error ? exc.message : String(exc),
    })
  } finally {
    busy.value = ''
  }
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
      <span class="badge" :class="hasCredentials ? 'badge-ok' : 'badge-warn'">
        {{ hasCredentials ? t('music.credentialsConfigured') : t('music.credentialsMissing') }}
      </span>
    </div>

    <p class="field-hint credentials-note">{{ pickLocalized(block.note) || t('music.credentialsNote') }}</p>

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
          <p v-else-if="defaultHint(field)" class="field-hint">{{ defaultHint(field) }}</p>
        </div>
        <div class="field-control">
          <input
            :id="`cred-${field.key}`"
            :value="draftOf(field.key)"
            :type="field.kind === 'password' ? 'password' : 'text'"
            :placeholder="field.placeholder || placeHolder(field)"
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
      <button v-if="hasCredentials" type="button" class="btn btn-danger" :disabled="busy !== ''" @click="clearCredentials">
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
  .credentials-fields .field-row {
    grid-template-columns: minmax(0, 1fr);
    gap: 4px;
    padding: 12px 0;
  }

  .credentials-fields .field-control {
    max-width: none;
  }

  .credentials-actions .btn {
    flex: 1 1 auto;
  }
}
</style>
