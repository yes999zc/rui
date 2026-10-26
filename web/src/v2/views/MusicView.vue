<script setup lang="ts">
/**
 * `/v2/music` (M9b) — music sources, playback status and write-only credentials.
 *
 * - `GET /api/v1/music/sources` is the registry + presence-only credential state;
 * - `GET /api/v1/music` is the configured/playing status;
 * - `PUT /api/v1/music/credentials` writes only the slots typed on this page and
 *   may switch `source`;
 * - `POST /api/v1/music/test` pings with the unsaved draft and writes nothing.
 *
 * Credential values are never rendered: each slot shows "configured / not
 * configured" plus an empty password input. A successful save clears the draft.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import ErrorNotice from '../components/device/ErrorNotice.vue'
import StateBlock from '../components/StateBlock.vue'
import {
  api,
  type MusicCredentialField,
  type MusicCredentialsRequest,
  type MusicPreset,
  type MusicTestResponse,
} from '../api'
import { describeError, type ErrorCopy } from '../errors'
import { pickLocalized, t } from '../i18n'
import { useResource } from '../useResource'

const sourcesResource = useResource(() => api.musicSources())
const musicResource = useResource(() => api.music())
const { data: sourcesInfo, error: sourcesError, loading: sourcesLoading } = sourcesResource
const { data: musicInfo, error: musicError } = musicResource

const selectedSource = ref('')
const credentialDraft = ref<Record<string, string>>({})
const fieldErrors = ref<Record<string, string>>({})
const actionError = ref<ErrorCopy | null>(null)
const success = ref('')
const busy = ref<'' | 'save' | 'test'>('')
const testResult = ref<MusicTestResponse | null>(null)
const clearKey = ref('')

const sourcesErrorCopy = computed(() =>
  sourcesError.value ? describeError(sourcesError.value) : null,
)
const musicErrorCopy = computed(() =>
  musicError.value ? describeError(musicError.value) : null,
)
const sources = computed<MusicPreset[]>(() => sourcesInfo.value?.sources ?? [])
const selectedPreset = computed<MusicPreset | null>(
  () => sources.value.find((preset) => preset.id === selectedSource.value) ?? null,
)
const selectedIsCurrent = computed(
  () => Boolean(sourcesInfo.value) && selectedSource.value === sourcesInfo.value?.current,
)
const credentialFields = computed<MusicCredentialField[]>(
  () => selectedPreset.value?.credential_fields ?? [],
)
const hasDraftValues = computed(() =>
  credentialFields.value.some((field) => (credentialDraft.value[field.key] ?? '').trim() !== ''),
)
const sourceChanged = computed(
  () => Boolean(sourcesInfo.value) && selectedSource.value !== sourcesInfo.value?.current,
)
const canSubmit = computed(
  () =>
    Boolean(selectedPreset.value?.available) &&
    (hasDraftValues.value || sourceChanged.value),
)
const missingLabels = computed(() => {
  const missing = sourcesInfo.value?.credentials.missing ?? []
  return missing.map((key) => {
    const field = sourcesInfo.value?.current_preset?.credential_fields.find(
      (item) => item.key === key,
    )
    return field ? pickLocalized(field.label) : key
  })
})

function localizedName(preset: MusicPreset | null): string {
  return preset ? pickLocalized(preset.name) : ''
}

function localizedSourceName(): string {
  const name = musicInfo.value?.source_name
  if (name) return pickLocalized(name)
  const preset = sources.value.find((item) => item.id === sourcesInfo.value?.current)
  return preset ? localizedName(preset) : (sourcesInfo.value?.current ?? '—')
}

function musicStateLabel(state: string | undefined): string {
  if (!state) return t('v2.common.na')
  if (state === 'idle') return t('v2.music.state.idle')
  if (state === 'playing') return t('v2.music.state.playing')
  if (state === 'paused') return t('v2.music.state.paused')
  return t('v2.music.state.other', { state })
}

function presence(field: MusicCredentialField): boolean | null {
  if (!selectedIsCurrent.value) return null
  return Boolean(sourcesInfo.value?.credentials.fields[field.key])
}

function slotError(key: string): string {
  return fieldErrors.value[key] ?? ''
}

function selectSource(id: string): void {
  if (selectedSource.value === id) return
  selectedSource.value = id
}

function resetDraft(): void {
  credentialDraft.value = {}
  fieldErrors.value = {}
  testResult.value = null
}

watch(selectedSource, () => {
  resetDraft()
  clearKey.value = ''
  actionError.value = null
  success.value = ''
})

function buildBody(): MusicCredentialsRequest {
  const body: MusicCredentialsRequest = { source: selectedSource.value }
  for (const field of credentialFields.value) {
    const value = (credentialDraft.value[field.key] ?? '').trim()
    if (value) body[field.key] = value
  }
  return body
}

/**
 * Apply only errors that point at a rendered slot. Returns how many fields were
 * mapped: when none were, the caller shows the full error banner instead of
 * suppressing a 422 whose `loc` disagrees with the input keys.
 */
function applyFieldErrors(copy: ErrorCopy): number {
  const keys = new Set(credentialFields.value.map((field) => field.key))
  const map: Record<string, string> = {}
  for (const field of copy.fields) {
    const last = field.path.split('.').pop() ?? field.path
    const key = keys.has(field.path) ? field.path : keys.has(last) ? last : ''
    if (!key) continue
    map[key] = field.message || t('v2.music.fieldInvalid')
  }
  fieldErrors.value = map
  return Object.keys(map).length
}

function clearLabel(key: string): string {
  const field = credentialFields.value.find((item) => item.key === key)
  return field ? pickLocalized(field.label) : key
}

function requestClear(key: string): void {
  if (busy.value) return
  clearKey.value = key
}

async function clearSlot(): Promise<void> {
  const key = clearKey.value
  clearKey.value = ''
  if (!key || busy.value) return
  const label = clearLabel(key)
  busy.value = 'save'
  actionError.value = null
  success.value = ''
  testResult.value = null
  fieldErrors.value = {}
  try {
    // An empty string deletes the slot server-side (PUT /music/credentials).
    await api.musicCredentials({ source: selectedSource.value, [key]: '' })
    const nextDraft = { ...credentialDraft.value }
    delete nextDraft[key]
    credentialDraft.value = nextDraft
    await Promise.all([sourcesResource.load(), musicResource.load()])
    success.value = t('v2.music.slotCleared', { name: label })
  } catch (err) {
    const copy = describeError(err)
    if (!applyFieldErrors(copy)) actionError.value = copy
  } finally {
    busy.value = ''
  }
}

async function saveCredentials(): Promise<void> {
  if (!selectedPreset.value || busy.value || !canSubmit.value) return
  busy.value = 'save'
  actionError.value = null
  success.value = ''
  testResult.value = null
  fieldErrors.value = {}
  try {
    const response = await api.musicCredentials(buildBody())
    // Presence only: the draft (which held the only copy of what was typed) is
    // cleared once the server confirms the write.
    credentialDraft.value = {}
    await Promise.all([sourcesResource.load(), musicResource.load()])
    success.value = response.has_credentials
      ? t('v2.music.savedReady')
      : t('v2.music.savedIncomplete')
  } catch (err) {
    const copy = describeError(err)
    if (!applyFieldErrors(copy)) actionError.value = copy
  } finally {
    busy.value = ''
  }
}

async function testSource(): Promise<void> {
  if (!selectedPreset.value || busy.value) return
  busy.value = 'test'
  actionError.value = null
  testResult.value = null
  fieldErrors.value = {}
  try {
    // Unsaved draft values are tested in memory by the server; nothing is written.
    testResult.value = await api.musicTest(buildBody())
  } catch (err) {
    const copy = describeError(err)
    if (!applyFieldErrors(copy)) actionError.value = copy
  } finally {
    busy.value = ''
  }
}

async function boot(): Promise<void> {
  await Promise.all([sourcesResource.load(), musicResource.load()])
  if (sourcesInfo.value && !selectedSource.value) {
    selectedSource.value = sourcesInfo.value.current || sources.value[0]?.id || ''
  }
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
        <h1>{{ t('v2.music.title') }}</h1>
        <p class="lede">{{ t('v2.music.lede') }}</p>
      </div>
      <div class="v2-page-actions">
        <button
          type="button"
          class="btn btn-sm"
          :disabled="Boolean(busy) || sourcesLoading"
          data-testid="music-refresh"
          @click="boot()"
        >
          <AppIcon name="refresh" :size="14" />
          {{ t('v2.common.refresh') }}
        </button>
      </div>
    </header>

    <StateBlock
      v-if="sourcesLoading && !sourcesInfo"
      kind="loading"
      :title="t('v2.common.loading')"
    />

    <StateBlock
      v-else-if="sourcesErrorCopy && !sourcesInfo"
      kind="error"
      :title="t('v2.music.loadFailed')"
      :detail="sourcesErrorCopy.detail"
      :hint="sourcesErrorCopy.hint"
      :status="sourcesErrorCopy.status"
      :request-id="sourcesErrorCopy.requestId"
      show-retry
      @retry="boot()"
    />

    <template v-else-if="sourcesInfo">
      <div v-if="sourcesErrorCopy" class="banner banner-warn v2-banner" role="status">
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.music.loadFailed') }}</strong>
          <p class="note">{{ sourcesErrorCopy.hint }}</p>
        </div>
        <button type="button" class="btn btn-sm" @click="boot()">
          {{ t('v2.common.retry') }}
        </button>
      </div>

      <div
        v-if="musicErrorCopy"
        class="banner banner-warn v2-banner"
        role="status"
        data-testid="music-status-fallback"
      >
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.music.statusFallback') }}</strong>
          <p class="note">{{ musicErrorCopy.hint }}</p>
        </div>
      </div>

      <section class="v2-card" data-testid="music-status">
        <header class="v2-card-head">
          <h2 class="v2-card-title">{{ t('v2.music.statusTitle') }}</h2>
          <span
            class="badge"
            :class="musicInfo?.enabled ? 'badge-ok' : 'badge-muted'"
            data-testid="music-enabled-badge"
          >
            {{ musicInfo?.enabled ? t('v2.common.enabled') : t('v2.common.disabled') }}
          </span>
        </header>
        <dl class="v2-kv">
          <div>
            <dt>{{ t('v2.music.currentSource') }}</dt>
            <dd>{{ localizedSourceName() }}</dd>
          </div>
          <div>
            <dt>{{ t('v2.music.runningSource') }}</dt>
            <dd>{{ musicInfo?.running_source || t('v2.common.na') }}</dd>
          </div>
          <div>
            <dt>{{ t('v2.music.credentials') }}</dt>
            <dd>
              <span
                class="badge"
                :class="sourcesInfo.credentials.ready ? 'badge-ok' : 'badge-warn'"
                data-testid="music-credentials-badge"
              >
                {{
                  sourcesInfo.credentials.ready
                    ? t('v2.common.configured')
                    : t('v2.common.notConfigured')
                }}
              </span>
              <span v-if="missingLabels.length" class="meta">
                {{ t('v2.music.missingSlots', { fields: missingLabels.join(', ') }) }}
              </span>
            </dd>
          </div>
          <div>
            <dt>{{ t('v2.music.state') }}</dt>
            <dd>{{ musicStateLabel(musicInfo?.state) }}</dd>
          </div>
          <div>
            <dt>{{ t('v2.music.track') }}</dt>
            <dd>
              <template v-if="musicInfo?.track">
                {{ musicInfo.track.title }}
                <span class="meta">· {{ musicInfo.track.artist }}</span>
              </template>
              <template v-else>{{ t('v2.music.noTrack') }}</template>
            </dd>
          </div>
          <div>
            <dt>{{ t('v2.music.queueLeft') }}</dt>
            <dd>{{ musicInfo?.queue_left ?? t('v2.common.na') }}</dd>
          </div>
          <div>
            <dt>{{ t('v2.music.lastPlayed') }}</dt>
            <dd>
              <template v-if="musicInfo?.last">
                {{ musicInfo.last.title }} <span class="meta">· {{ musicInfo.last.artist }}</span>
              </template>
              <template v-else>{{ t('v2.music.noHistory') }}</template>
            </dd>
          </div>
          <div>
            <dt>{{ t('v2.music.topArtists') }}</dt>
            <dd>
              <span v-if="musicInfo?.top?.length">
                {{ musicInfo.top.map((row) => `${row.artist} (${row.plays})`).join(' · ') }}
              </span>
              <span v-else>{{ t('v2.music.noHistory') }}</span>
            </dd>
          </div>
        </dl>
        <p v-if="sourcesInfo.restart_required" class="v2-catalog-note" data-testid="music-restart-required">
          {{ t('v2.music.restartRequired') }}
        </p>
      </section>

      <section class="v2-card" data-testid="music-sources">
        <header class="v2-card-head">
          <h2 class="v2-card-title">{{ t('v2.music.sourcesTitle') }}</h2>
          <span class="meta">{{ t('v2.common.items', { n: sources.length }) }}</span>
        </header>
        <p class="v2-card-lede">{{ t('v2.music.sourcesLede') }}</p>

        <div v-if="sources.length" class="v2-source-grid">
          <article
            v-for="preset in sources"
            :key="preset.id"
            class="v2-source-card"
            :class="{
              'is-current': preset.id === sourcesInfo.current,
              'is-selected': preset.id === selectedSource,
              'is-unavailable': !preset.available,
            }"
            :data-source-id="preset.id"
          >
            <div class="v2-source-head">
              <h3>{{ localizedName(preset) }}</h3>
              <span v-if="preset.id === sourcesInfo.current" class="badge badge-ok">
                {{ t('v2.music.currentBadge') }}
              </span>
              <span
                v-if="preset.id === sourcesInfo.running_source"
                class="badge badge-accent"
              >
                {{ t('v2.music.runningBadge') }}
              </span>
              <span v-if="!preset.available" class="badge badge-warn">
                {{ t('v2.music.unavailableBadge') }}
              </span>
            </div>
            <p class="v2-source-desc">{{ pickLocalized(preset.description) }}</p>
            <p class="meta mono">
              {{ preset.protocol_family }}
              <template v-if="preset.default_port"> · :{{ preset.default_port }}</template>
            </p>
            <div class="v2-source-actions">
              <button
                type="button"
                class="btn btn-sm"
                :class="{ 'btn-primary': preset.id === selectedSource }"
                :disabled="!preset.available"
                :data-testid="`music-select-${preset.id}`"
                @click="selectSource(preset.id)"
              >
                {{ preset.id === selectedSource ? t('v2.music.selected') : t('v2.music.select') }}
              </button>
              <a
                v-if="preset.docs_url"
                class="btn btn-sm"
                :href="preset.docs_url"
                target="_blank"
                rel="noreferrer"
              >
                {{ t('v2.music.docs') }}
                <AppIcon name="external" :size="12" />
              </a>
            </div>
          </article>
        </div>
        <p v-else class="note">{{ t('v2.music.noSources') }}</p>
      </section>

      <section class="v2-card" data-testid="music-credentials">
        <header class="v2-card-head">
          <h2 class="v2-card-title">{{ t('v2.music.credentialsTitle') }}</h2>
          <span
            v-if="selectedPreset"
            class="badge"
            :class="selectedIsCurrent ? 'badge-ok' : 'badge-muted'"
          >
            {{ selectedIsCurrent ? t('v2.music.currentBadge') : t('v2.music.pendingSwitch') }}
          </span>
        </header>
        <p class="v2-card-lede">{{ t('v2.music.credentialsLede') }}</p>

        <p v-if="!selectedPreset" class="note">{{ t('v2.music.selectFirst') }}</p>
        <p v-else-if="!selectedPreset.available" class="note">
          {{ t('v2.music.unavailableHint') }}
        </p>

        <form v-else @submit.prevent="saveCredentials">
          <div
            v-for="field in credentialFields"
            :key="field.key"
            class="field-row v2-credential-row"
            :class="{ invalid: Boolean(slotError(field.key)) }"
            :data-credential-slot="field.key"
          >
            <div class="field-meta">
              <label class="field-label" :for="`v2-music-${field.key}`">
                {{ pickLocalized(field.label) }}
                <span class="badge badge-muted">
                  {{ field.required ? t('v2.music.required') : t('v2.music.optional') }}
                </span>
                <span
                  v-if="presence(field) !== null"
                  class="badge"
                  :class="presence(field) ? 'badge-ok' : 'badge-warn'"
                  :data-testid="`music-slot-presence-${field.key}`"
                >
                  {{ presence(field) ? t('v2.common.configured') : t('v2.common.notConfigured') }}
                </span>
              </label>
              <p class="field-hint">{{ pickLocalized(field.hint) }}</p>
            </div>
            <div class="field-control">
              <input
                :id="`v2-music-${field.key}`"
                v-model="credentialDraft[field.key]"
                class="input"
                :class="{ invalid: Boolean(slotError(field.key)) }"
                :type="field.secret ? 'password' : field.kind === 'url' ? 'url' : 'text'"
                :autocomplete="field.secret ? 'new-password' : 'off'"
                :placeholder="
                  selectedIsCurrent && presence(field)
                    ? t('v2.music.keepPlaceholder')
                    : pickLocalized(field.placeholder)
                "
                :aria-invalid="Boolean(slotError(field.key))"
              />
              <p v-if="slotError(field.key)" class="field-error">
                {{ slotError(field.key) }}
              </p>
              <p v-if="selectedIsCurrent && presence(field)" class="field-meta-line">
                {{ t('v2.music.writeOnlyHint') }}
              </p>
              <div v-if="selectedIsCurrent && presence(field)" class="v2-slot-actions">
                <button
                  type="button"
                  class="btn btn-sm"
                  :disabled="Boolean(busy)"
                  :data-testid="`music-clear-${field.key}`"
                  @click="requestClear(field.key)"
                >
                  <AppIcon name="trash" :size="13" />
                  {{ t('v2.music.clearSlot') }}
                </button>
              </div>
            </div>
          </div>

          <div v-if="!credentialFields.length" class="v2-catalog-note">
            {{ t('v2.music.keyless') }}
          </div>

          <div class="v2-save-bar">
            <p class="v2-save-state note" role="status">
              <template v-if="busy === 'save'">{{ t('v2.common.saving') }}</template>
              <template v-else-if="success">{{ success }}</template>
              <template v-else-if="!canSubmit">{{ t('v2.music.noChanges') }}</template>
              <template v-else>{{ t('v2.music.unsaved') }}</template>
            </p>
            <button
              type="button"
              class="btn"
              :disabled="Boolean(busy) || !selectedPreset.available"
              data-testid="music-test"
              @click="testSource"
            >
              <AppIcon :name="busy === 'test' ? 'clock' : 'activity'" :size="14" />
              {{ busy === 'test' ? t('v2.common.testing') : t('v2.music.test') }}
            </button>
            <button
              type="submit"
              class="btn btn-primary"
              :disabled="Boolean(busy) || !canSubmit"
              data-testid="music-save"
            >
              <AppIcon :name="busy === 'save' ? 'clock' : 'check'" :size="14" />
              {{ busy === 'save' ? t('v2.common.saving') : t('v2.common.save') }}
            </button>
          </div>
        </form>

        <ErrorNotice :error="actionError" data-testid="music-action-error" />

        <div
          v-if="testResult"
          class="v2-music-test-result"
          :class="testResult.ok ? 'is-ok' : 'is-fail'"
          role="status"
          data-testid="music-test-result"
        >
          <AppIcon :name="testResult.ok ? 'check' : 'alert'" :size="17" />
          <div>
            <strong>
              {{
                testResult.ok
                  ? t('v2.music.testOk', { ms: testResult.latency_ms ?? '—' })
                  : t('v2.music.testFailed')
              }}
            </strong>
            <p v-if="testResult.error" class="note mono v2-break">{{ testResult.error }}</p>
            <p class="meta">{{ t('v2.music.testSource', { source: testResult.source }) }}</p>
          </div>
        </div>
      </section>

      <nav class="v2-config-links" :aria-label="t('v2.config.otherPages')">
        <span class="meta">{{ t('v2.config.otherPages') }}</span>
        <RouterLink class="btn btn-sm" to="/voice-assistant">
          {{ t('v2.nav.voiceAssistant') }}
        </RouterLink>
        <RouterLink class="btn btn-sm" to="/credentials">{{ t('v2.nav.credentials') }}</RouterLink>
        <RouterLink class="btn btn-sm" to="/health">{{ t('v2.nav.health') }}</RouterLink>
      </nav>
    </template>

    <ConfirmDialog
      v-if="clearKey"
      :title="t('v2.music.clearSlotTitle')"
      :confirm-label="t('v2.music.clearSlotConfirm')"
      :cancel-label="t('v2.common.cancel')"
      tone="danger"
      :busy="busy === 'save'"
      :busy-label="t('v2.common.saving')"
      data-testid="music-slot-clear-dialog"
      @confirm="clearSlot"
      @cancel="clearKey = ''"
    >
      <p class="v2-confirm-lead">
        {{ t('v2.music.clearSlotBody', { name: clearLabel(clearKey) }) }}
      </p>
      <p class="note">{{ t('v2.music.clearSlotHint') }}</p>
    </ConfirmDialog>
  </div>
</template>
