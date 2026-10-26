<script setup lang="ts">
/**
 * `/v2/credentials` (M9b) — secret presence, write-only updates and provider probes.
 *
 * `GET /api/v1/secrets` returns names and presence only; there is no endpoint
 * that reads a value back. The page therefore renders `key + configured/not
 * configured`, never a value. Values typed here live only in a local ref until
 * the POST succeeds, then the input is cleared; they never enter localStorage,
 * the URL or a console log.
 *
 * `POST /api/v1/providers/probe` takes a preset id (no value): the server reads
 * the stored key itself, so "verify after writing" does not re-send the secret.
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import StateBlock from '../components/StateBlock.vue'
import {
  api,
  type ProviderPreset,
  type ProviderProbeRequest,
  type ProviderProbeResponse,
  type ProviderStatusRow,
  type SecretEntry,
} from '../api'
import { describeError, type ErrorCopy } from '../errors'
import { pickLocalized, secretCategoryLabel, t } from '../i18n'
import { useResource } from '../useResource'

interface SecretRow {
  name: string
  entry: SecretEntry
  presets: ProviderPreset[]
}

const secretsResource = useResource(() => api.secrets())
const presetsResource = useResource(() => api.providerPresets())
const statusResource = useResource(() => api.providerStatus())
const { data: secretsPage, error: secretsError, loading: secretsLoading } = secretsResource
const { data: presetsPage, error: presetsError } = presetsResource
const { data: providerStatus } = statusResource

const editing = ref('')
const valueInput = ref('')
const busy = ref('')
const fieldError = ref('')
const actionError = ref<ErrorCopy | null>(null)
const success = ref('')
const lastWritten = ref('')
const pendingClear = ref<SecretRow | null>(null)
const verifyChoice = ref<Record<string, string>>({})
const probeResults = ref<Record<string, ProviderProbeResponse>>({})
const probeErrors = ref<Record<string, ErrorCopy>>({})
const probing = ref('')

const secretsErrorCopy = computed(() =>
  secretsError.value ? describeError(secretsError.value) : null,
)
const presetsErrorCopy = computed(() =>
  presetsError.value ? describeError(presetsError.value) : null,
)

/** Environment variable name -> the presets that can probe it. */
const presetsByEnv = computed<Record<string, ProviderPreset[]>>(() => {
  const map: Record<string, ProviderPreset[]> = {}
  for (const preset of presetsPage.value?.presets ?? []) {
    for (const credential of preset.credentials) {
      const list = map[credential.name] ?? (map[credential.name] = [])
      if (!list.some((item) => item.id === preset.id)) list.push(preset)
    }
  }
  return map
})

const rows = computed<SecretRow[]>(() => {
  const entries = secretsPage.value?.secrets ?? {}
  const names = new Set<string>([
    ...(secretsPage.value?.names ?? []),
    ...Object.keys(entries),
  ])
  return [...names]
    .sort((a, b) => a.localeCompare(b))
    .map((name) => ({
      name,
      entry:
        entries[name] ??
        ({
          has_value: false,
          category: 'other',
          used_by: [],
          consumers: [],
          hint: { zh: '', en: '' },
          writable: true,
        } satisfies SecretEntry),
      presets: presetsByEnv.value[name] ?? [],
    }))
})

const configuredCount = computed(() => rows.value.filter((row) => row.entry.has_value).length)

function consumerLabels(row: SecretRow): string {
  const labels = row.entry.consumers.map((consumer) => pickLocalized(consumer.label))
  if (labels.length) return labels.join(' · ')
  return row.entry.used_by.join(' · ') || t('v2.credentials.noConsumer')
}

function startEdit(row: SecretRow): void {
  if (!row.entry.writable || busy.value) return
  editing.value = row.name
  valueInput.value = ''
  fieldError.value = ''
  actionError.value = null
  success.value = ''
}

function cancelEdit(): void {
  editing.value = ''
  valueInput.value = ''
  fieldError.value = ''
}

async function submit(row: SecretRow): Promise<void> {
  if (busy.value) return
  const value = valueInput.value
  if (!value) {
    if (row.entry.has_value) {
      pendingClear.value = row
      return
    }
    fieldError.value = t('v2.credentials.valueRequired')
    return
  }
  await writeSecret(row, value)
}

async function writeSecret(row: SecretRow, value: string): Promise<void> {
  busy.value = row.name
  fieldError.value = ''
  actionError.value = null
  success.value = ''
  try {
    const response = await api.putSecret(row.name, value)
    // The only copy of the typed value is this input; clear it immediately.
    valueInput.value = ''
    editing.value = ''
    lastWritten.value = response.has_key ? row.name : ''
    success.value = response.has_key
      ? t('v2.credentials.saved', { name: row.name })
      : t('v2.credentials.cleared', { name: row.name })
    // Refresh provider readiness too: after a write the row's provider may have
    // become `configured`, which is what verify() uses for base_url/model.
    await Promise.all([secretsResource.load(), statusResource.load()])
  } catch (err) {
    const copy = describeError(err)
    fieldError.value = copy.detail || copy.title
    if (copy.status !== 400 && copy.status !== 422) actionError.value = copy
  } finally {
    busy.value = ''
  }
}

async function confirmClear(): Promise<void> {
  const row = pendingClear.value
  pendingClear.value = null
  if (!row) return
  await writeSecret(row, '')
}

function verifyDefault(row: SecretRow): string {
  return verifyChoice.value[row.name] ?? row.presets[0]?.id ?? ''
}

/**
 * The configured provider that actually reads this env var. A probe with only a
 * preset id would test the preset's default base_url/model, which can differ
 * from the operator's saved endpoint and produce a false OK/FAIL. Probes still
 * use a disposable name: the backend rejects an existing catalog name.
 */
function configuredProvider(row: SecretRow): ProviderStatusRow | null {
  const preset = row.presets.find((item) => item.id === verifyDefault(row)) ?? null
  const matches = (providerStatus.value?.providers ?? []).filter(
    (provider) =>
      provider.configured &&
      [provider.api_key_env, provider.app_id_env, provider.api_secret_env].includes(row.name),
  )
  if (!matches.length) return null
  const score = (provider: ProviderStatusRow): number => {
    let value = 0
    if (preset && provider.stage === preset.stage) value += 4
    if (preset && provider.kind === preset.kind) value += 2
    if (provider.ready) value += 1
    if (provider.primary) value += 1
    return value
  }
  return [...matches].sort((a, b) => score(b) - score(a))[0] ?? null
}

function verifyLabel(row: SecretRow): string {
  if (probing.value === row.name) return t('v2.credentials.verifying')
  return configuredProvider(row)
    ? t('v2.credentials.verify')
    : t('v2.credentials.verifyWithDefaults')
}

async function verify(row: SecretRow): Promise<void> {
  const presetId = verifyDefault(row)
  if (!presetId || probing.value) return
  probing.value = row.name
  const pendingErrors = { ...probeErrors.value }
  delete pendingErrors[row.name]
  probeErrors.value = pendingErrors
  const pendingResults = { ...probeResults.value }
  delete pendingResults[row.name]
  probeResults.value = pendingResults
  try {
    // The probe endpoint validates a *draft* provider, and a name that already
    // exists in the catalog is rejected. A disposable name still makes the
    // server read the stored key (keys=null), which is what "verify" means.
    const probeName = `probe-${presetId}`.slice(0, 32)
    const body: ProviderProbeRequest = { preset_id: presetId, name: probeName }
    const provider = configuredProvider(row)
    if (provider) {
      if (provider.base_url) body.base_url = provider.base_url
      if (provider.model) body.model = provider.model
    }
    const result = await api.providerProbe(body)
    const next = { ...probeResults.value }
    next[row.name] = result
    probeResults.value = next
    const errors = { ...probeErrors.value }
    delete errors[row.name]
    probeErrors.value = errors
  } catch (err) {
    const errors = { ...probeErrors.value, [row.name]: describeError(err) }
    probeErrors.value = errors
    const results = { ...probeResults.value }
    delete results[row.name]
    probeResults.value = results
  } finally {
    probing.value = ''
  }
}

function probeElapsed(result: ProviderProbeResponse): string {
  const details = result.details ?? {}
  const value = details.elapsed_ms
  return typeof value === 'number' ? `${value} ms` : '—'
}

function probeMissing(result: ProviderProbeResponse): string[] {
  const details = result.details ?? {}
  const missing = details.missing
  return Array.isArray(missing) ? missing.map(String) : []
}

function setChoice(name: string, event: Event): void {
  verifyChoice.value = {
    ...verifyChoice.value,
    [name]: (event.target as HTMLSelectElement).value,
  }
}

async function boot(): Promise<void> {
  await Promise.all([
    secretsResource.load(),
    presetsResource.load(),
    // Readiness is an enhancement: a failed status read falls back to the
    // preset defaults and the button says so.
    statusResource.load(),
  ])
}

onMounted(() => {
  void boot()
})
</script>

<template>
  <div class="v2-page">
    <header class="v2-page-head">
      <div class="v2-page-title">
        <span class="kicker">{{ t('v2.nav.config') }}</span>
        <h1>{{ t('v2.credentials.title') }}</h1>
        <p class="lede">{{ t('v2.credentials.lede') }}</p>
      </div>
      <div class="v2-page-actions">
        <span v-if="rows.length" class="meta" data-testid="secrets-count">
          {{ t('v2.credentials.count', { configured: configuredCount, total: rows.length }) }}
        </span>
        <button
          type="button"
          class="btn btn-sm"
          :disabled="secretsLoading || Boolean(busy)"
          data-testid="secrets-refresh"
          @click="boot()"
        >
          <AppIcon name="refresh" :size="14" />
          {{ t('v2.common.refresh') }}
        </button>
      </div>
    </header>

    <p class="v2-secret-note" data-testid="secret-security-note">
      <AppIcon name="lock" :size="16" />
      <span>{{ t('v2.credentials.securityNote') }}</span>
    </p>

    <div v-if="success" class="banner banner-ok v2-banner" role="status" data-testid="secret-saved">
      <AppIcon name="check" :size="17" />
      <div class="v2-banner-body">
        <strong>{{ success }}</strong>
        <p v-if="lastWritten" class="note">
          {{ t('v2.credentials.verifyHint', { name: lastWritten }) }}
        </p>
      </div>
      <button type="button" class="btn btn-sm" @click="success = ''">
        {{ t('v2.common.close') }}
      </button>
    </div>

    <div
      v-if="presetsErrorCopy"
      class="banner banner-warn v2-banner"
      role="status"
      data-testid="secrets-presets-fallback"
    >
      <AppIcon name="alert" :size="17" />
      <div class="v2-banner-body">
        <strong>{{ t('v2.credentials.presetsUnavailable') }}</strong>
        <p class="note">{{ presetsErrorCopy.hint }}</p>
      </div>
    </div>

    <StateBlock
      v-if="secretsLoading && !secretsPage"
      kind="loading"
      :title="t('v2.common.loading')"
    />

    <StateBlock
      v-else-if="secretsErrorCopy && !secretsPage"
      kind="error"
      :title="t('v2.credentials.loadFailed')"
      :detail="secretsErrorCopy.detail"
      :hint="secretsErrorCopy.hint"
      :status="secretsErrorCopy.status"
      :request-id="secretsErrorCopy.requestId"
      show-retry
      @retry="boot()"
    />

    <template v-else-if="secretsPage">
      <div v-if="secretsErrorCopy" class="banner banner-warn v2-banner" role="status">
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.credentials.loadFailed') }}</strong>
          <p class="note">{{ secretsErrorCopy.hint }}</p>
        </div>
        <button type="button" class="btn btn-sm" @click="boot()">
          {{ t('v2.common.retry') }}
        </button>
      </div>

      <div v-if="actionError" class="banner banner-warn v2-banner" role="alert">
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ actionError.title }}</strong>
          <p class="note">{{ actionError.detail || actionError.hint }}</p>
        </div>
        <button type="button" class="btn btn-sm" @click="actionError = null">
          {{ t('v2.common.close') }}
        </button>
      </div>

      <StateBlock
        v-if="!rows.length"
        kind="empty"
        :title="t('v2.credentials.empty')"
        :hint="t('v2.credentials.emptyHint')"
      />

      <section v-else class="v2-card" data-testid="secrets-list">
        <header class="v2-card-head">
          <h2 class="v2-card-title">{{ t('v2.credentials.listTitle') }}</h2>
          <span class="meta">{{ t('v2.common.items', { n: rows.length }) }}</span>
        </header>
        <p class="v2-card-lede">{{ t('v2.credentials.listLede') }}</p>

        <ul class="v2-secret-list">
          <li
            v-for="row in rows"
            :key="row.name"
            class="v2-secret-row"
            :data-secret-name="row.name"
          >
            <div class="v2-secret-main">
              <div class="v2-secret-head">
                <strong class="mono">{{ row.name }}</strong>
                <span
                  class="badge"
                  :class="row.entry.has_value ? 'badge-ok' : 'badge-warn'"
                  :data-testid="`secret-state-${row.name}`"
                >
                  {{
                    row.entry.has_value
                      ? t('v2.common.configured')
                      : t('v2.common.notConfigured')
                  }}
                </span>
                <span
                  v-if="row.entry.category"
                  class="badge badge-muted"
                  :data-category="row.entry.category"
                >
                  {{ secretCategoryLabel(row.entry.category) }}
                </span>
                <span v-if="!row.entry.writable" class="badge badge-muted">
                  {{ t('v2.common.readOnly') }}
                </span>
                <span class="v2-secret-spacer" />
                <button
                  v-if="row.entry.writable"
                  type="button"
                  class="btn btn-sm"
                  :disabled="Boolean(busy)"
                  :data-testid="`secret-set-${row.name}`"
                  @click="startEdit(row)"
                >
                  {{ row.entry.has_value ? t('v2.credentials.replace') : t('v2.credentials.set') }}
                </button>
                <RouterLink
                  v-else-if="row.name === 'music.credentials'"
                  class="btn btn-sm"
                  to="/music"
                  :data-testid="`secret-open-${row.name}`"
                >
                  {{ t('v2.credentials.openMusic') }}
                </RouterLink>
                <button
                  v-if="row.presets.length"
                  type="button"
                  class="btn btn-sm"
                  :disabled="Boolean(probing)"
                  :data-testid="`secret-verify-${row.name}`"
                  @click="verify(row)"
                >
                  <AppIcon :name="probing === row.name ? 'clock' : 'activity'" :size="13" />
                  {{ verifyLabel(row) }}
                </button>
              </div>

              <p class="v2-secret-consumers">
                <span class="meta">{{ t('v2.credentials.usedBy') }}</span>
                {{ consumerLabels(row) }}
              </p>
              <p v-if="row.entry.hint.zh || row.entry.hint.en" class="field-hint">
                {{ pickLocalized(row.entry.hint) }}
              </p>

              <form
                v-if="editing === row.name"
                class="v2-secret-form"
                :data-testid="`secret-form-${row.name}`"
                @submit.prevent="submit(row)"
              >
                <label class="field-label" :for="`v2-secret-input-${row.name}`">
                  {{ t('v2.credentials.valueLabel') }}
                </label>
                <p
                  v-if="row.name === 'web.auth_token'"
                  class="field-hint v2-secret-warning"
                  data-testid="auth-token-warning"
                >
                  <AppIcon name="alert" :size="14" />
                  {{ t('v2.credentials.authTokenWarning') }}
                </p>
                <input
                  :id="`v2-secret-input-${row.name}`"
                  v-model="valueInput"
                  class="input"
                  :class="{ invalid: Boolean(fieldError) }"
                  type="password"
                  autocomplete="new-password"
                  spellcheck="false"
                  :placeholder="t('v2.credentials.valuePlaceholder')"
                  :aria-invalid="Boolean(fieldError)"
                  data-testid="secret-input"
                />
                <p v-if="fieldError" class="field-error" data-testid="secret-field-error">
                  {{ fieldError }}
                </p>
                <p class="field-meta-line">{{ t('v2.credentials.valueHint') }}</p>
                <div class="v2-secret-form-actions">
                  <button type="button" class="btn btn-sm" @click="cancelEdit">
                    {{ t('v2.common.cancel') }}
                  </button>
                  <button
                    type="submit"
                    class="btn btn-primary btn-sm"
                    :disabled="Boolean(busy)"
                    data-testid="secret-submit"
                  >
                    <AppIcon :name="busy === row.name ? 'clock' : 'check'" :size="13" />
                    {{
                      busy === row.name
                        ? t('v2.common.saving')
                        : t('v2.credentials.saveValue')
                    }}
                  </button>
                </div>
              </form>

              <div
                v-if="row.presets.length"
                class="v2-secret-verify"
                data-testid="secret-verify-panel"
              >
                <label class="v2-inline-field">
                  <span class="meta">{{ t('v2.credentials.probeWith') }}</span>
                  <select
                    class="input v2-select"
                    :value="verifyDefault(row)"
                    :data-testid="`secret-probe-select-${row.name}`"
                    @change="setChoice(row.name, $event)"
                  >
                    <option v-for="preset in row.presets" :key="preset.id" :value="preset.id">
                      {{ pickLocalized(preset.display_name) }} · {{ preset.stage }}
                    </option>
                  </select>
                </label>
                <p
                  v-if="!configuredProvider(row)"
                  class="field-hint"
                  data-testid="verify-default-hint"
                >
                  {{ t('v2.credentials.verifyDefaultHint') }}
                </p>
                <span v-if="probeResults[row.name]" class="badge badge-muted">
                  {{ t('v2.credentials.probeCheck', { check: probeResults[row.name].check }) }}
                </span>
                <span v-if="probeResults[row.name]" class="meta">
                  {{ probeElapsed(probeResults[row.name]) }}
                </span>

                <div
                  v-if="probeResults[row.name]"
                  class="v2-secret-probe-result"
                  :class="probeResults[row.name].ok ? 'is-ok' : 'is-fail'"
                  role="status"
                  :data-testid="`secret-probe-result-${row.name}`"
                  :data-ok="probeResults[row.name].ok"
                >
                  <AppIcon :name="probeResults[row.name].ok ? 'check' : 'alert'" :size="15" />
                  <div>
                    <strong>
                      {{
                        probeResults[row.name].ok
                          ? t('v2.credentials.probeOk')
                          : t('v2.credentials.probeFail')
                      }}
                    </strong>
                    <p v-if="probeResults[row.name].message" class="note mono v2-break">
                      {{ probeResults[row.name].message }}
                    </p>
                    <p v-if="probeMissing(probeResults[row.name]).length" class="note">
                      {{ t('v2.credentials.probeMissing', { fields: probeMissing(probeResults[row.name]).join(', ') }) }}
                    </p>
                  </div>
                </div>
                <div
                  v-if="probeErrors[row.name]"
                  class="v2-secret-probe-result is-fail"
                  role="alert"
                  :data-testid="`secret-probe-error-${row.name}`"
                >
                  <AppIcon name="alert" :size="15" />
                  <div>
                    <strong>{{ probeErrors[row.name].title }}</strong>
                    <p class="note">{{ probeErrors[row.name].detail || probeErrors[row.name].hint }}</p>
                  </div>
                </div>
              </div>
            </div>
          </li>
        </ul>
      </section>

      <nav class="v2-config-links" :aria-label="t('v2.config.otherPages')">
        <span class="meta">{{ t('v2.config.otherPages') }}</span>
        <RouterLink class="btn btn-sm" to="/voice-assistant">
          {{ t('v2.nav.voiceAssistant') }}
        </RouterLink>
        <RouterLink class="btn btn-sm" to="/music">{{ t('v2.nav.music') }}</RouterLink>
        <RouterLink class="btn btn-sm" to="/health">{{ t('v2.nav.health') }}</RouterLink>
      </nav>
    </template>

    <ConfirmDialog
      v-if="pendingClear"
      :title="t('v2.credentials.clearTitle')"
      :confirm-label="t('v2.credentials.clearConfirm')"
      :cancel-label="t('v2.common.cancel')"
      tone="danger"
      :busy="Boolean(busy)"
      :busy-label="t('v2.common.saving')"
      data-testid="secret-clear-dialog"
      @confirm="confirmClear"
      @cancel="pendingClear = null"
    >
      <p class="v2-confirm-lead">
        {{ t('v2.credentials.clearBody', { name: pendingClear.name }) }}
      </p>
      <p class="note">{{ t('v2.credentials.clearHint') }}</p>
    </ConfirmDialog>
  </div>
</template>
