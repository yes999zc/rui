<script setup lang="ts">
/**
 * `/v2/family` (0.8.16 Card B) — the people whose voices may wake the speaker.
 *
 * Enrollment is two requests, not one: `startEnrollment` only arms the audio
 * plane, which keeps the next N seconds of post-AEC voice. A countdown runs for
 * exactly that window, then `commitEnrollment` embeds the file and writes the
 * profile. A 409 on commit means "still recording" (the server waits 2 s on its
 * own), so it is retried a few times instead of being shown as a failure; 400
 * means the recording itself was unusable and the operator must re-record.
 *
 * The library is keyed by `(name, device_id)`: a living-room mic and a bedroom
 * mic hear the same person differently, so the page records per device and the
 * row action re-records that one device only. `score` is the similarity to the
 * pre-commit library (the same person's previous embedding on that device, else
 * the closest other person on it) — a quality signal, not an identity claim.
 *
 * Samples are fetched as a blob because the API needs `X-Auth-Token`, which an
 * `<audio src>` cannot send. One object URL exists at a time and is revoked on
 * stop, on a new sample and on unmount.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import StateBlock from '../components/StateBlock.vue'
import {
  api,
  promptForToken,
  type DeviceSummary,
  type EnrollCommitResponse,
  type SpeakerDeviceRow,
  type SpeakerEntry,
} from '../api'
import { describeError, type ErrorCopy } from '../errors'
import { t, type MsgKey } from '../i18n'
import { useResource } from '../useResource'

const speakersResource = useResource(() => api.speakers())
const devicesResource = useResource(() => api.devices())
const { data: speakers, error, loading } = speakersResource
const { data: deviceList } = devicesResource

const name = ref('')
const deviceId = ref('')
const seconds = ref(3)
const fieldError = ref('')
const actionError = ref<ErrorCopy | null>(null)
const success = ref('')
/** enroll_id while a recording/countdown is in flight. */
const activeEnroll = ref('')
const remaining = ref(0)
const committing = ref(false)
const playingKey = ref('')
/** True while the form was filled by a row's "re-record" action. */
const reRecordMode = ref(false)
const lastScore = ref<{ score: number | null; reference: string; device: string } | null>(null)
const pendingDelete = ref<{ name: string; device?: string; label: string } | null>(null)
const deleting = ref(false)

let countdownTimer: number | null = null
let successTimer: number | null = null
let audio: HTMLAudioElement | null = null
let sampleUrl = ''

const devices = computed<DeviceSummary[]>(() => deviceList.value?.items ?? [])
const people = computed<SpeakerEntry[]>(() => speakers.value ?? [])
const canRecord = computed(() => Boolean(activeEnroll.value || committing.value))

/** Devices that can be picked: a disabled device cannot record (server 404). */
const recordableDevices = computed(() => devices.value.filter((device) => device.enabled))
const selectedDevice = computed(
  () => devices.value.find((device) => device.id === deviceId.value) ?? null,
)

const errorCopy = computed(() => (error.value ? describeError(error.value) : null))
const fieldCopy = computed(() => {
  if (!actionError.value) return null
  return localizeError(actionError.value)
})

/** The family page's own copy for the codes this flow can produce. */
const ERROR_KEYS: Record<string, string> = {
  speech_too_short: 'v2.family.error.speech_too_short',
  no_speech: 'v2.family.error.no_speech',
  speaker_model_unavailable: 'v2.family.error.speaker_model_unavailable',
  recording_incomplete: 'v2.family.error.recording_incomplete',
  plane_not_connected: 'v2.family.error.plane_not_connected',
  device_busy: 'v2.family.error.device_busy',
  not_found: 'v2.family.error.not_found',
  speaker_store_unavailable: 'v2.family.error.speaker_store_unavailable',
  bad_audio: 'v2.family.error.bad_audio',
  bad_request: 'v2.family.error.bad_request',
  device_required: 'v2.family.error.device_required',
}

function localizeError(copy: ErrorCopy): { title: string; detail: string } {
  const key = ERROR_KEYS[copy.code]
  return {
    title: key ? t(key as MsgKey) : copy.title,
    detail: copy.detail,
  }
}

function flashSuccess(message: string): void {
  success.value = message
  if (successTimer !== null) window.clearTimeout(successTimer)
  successTimer = window.setTimeout(() => {
    success.value = ''
  }, 4000)
}

function clearTimers(): void {
  if (countdownTimer !== null) {
    window.clearInterval(countdownTimer)
    countdownTimer = null
  }
}

function stopSample(): void {
  if (audio) {
    audio.pause()
    audio = null
  }
  if (sampleUrl) {
    URL.revokeObjectURL(sampleUrl)
    sampleUrl = ''
  }
  playingKey.value = ''
}

function rowKey(person: SpeakerEntry, device: SpeakerDeviceRow): string {
  return `${person.name}\u0000${device.device_id}`
}

function deviceLabel(id: string): string {
  const found = devices.value.find((device) => device.id === id)
  return found?.room || id
}

function formatScore(score: number | null): string {
  return score === null ? t('v2.family.scoreNone') : score.toFixed(2)
}

async function refreshAll(): Promise<void> {
  await Promise.all([speakersResource.load(), devicesResource.load()])
}

async function playSample(person: SpeakerEntry, device: SpeakerDeviceRow): Promise<void> {
  const key = rowKey(person, device)
  if (playingKey.value === key) {
    stopSample()
    return
  }
  stopSample()
  actionError.value = null
  try {
    const blob = await api.speakerSample(person.name, device.device_id)
    sampleUrl = URL.createObjectURL(blob)
    audio = new Audio(sampleUrl)
    playingKey.value = key
    audio.addEventListener('ended', () => stopSample(), { once: true })
    await audio.play()
  } catch (err) {
    stopSample()
    actionError.value = describeError(err)
  }
}

function resetForm(): void {
  name.value = ''
  deviceId.value = ''
  seconds.value = 3
  fieldError.value = ''
  reRecordMode.value = false
}

function startReRecord(person: SpeakerEntry, device: SpeakerDeviceRow): void {
  if (canRecord.value) return
  actionError.value = null
  name.value = person.name
  deviceId.value = device.device_id
  seconds.value = 3
  fieldError.value = ''
  reRecordMode.value = true
  document.getElementById('v2-family-name')?.focus()
}

function validate(): boolean {
  fieldError.value = ''
  const value = name.value.trim()
  if (!value) {
    fieldError.value = t('v2.family.nameInvalid')
    return false
  }
  if ([...value].length > 20) {
    fieldError.value = t('v2.family.nameInvalid')
    return false
  }
  if (!deviceId.value) {
    fieldError.value = t('v2.family.deviceRequired')
    return false
  }
  return true
}

/** Commit with the bounded retry the 409 contract asks for ("still recording"). */
async function commitEnrollment(enrollId: string): Promise<void> {
  committing.value = true
  try {
    let lastError: unknown = null
    for (let attempt = 0; attempt < 4; attempt += 1) {
      try {
        const result: EnrollCommitResponse = await api.commitEnrollment(enrollId)
        lastScore.value = {
          score: result.score,
          reference: result.reference,
          device: result.device_id,
        }
        flashSuccess(
          t('v2.family.saved', {
            name: result.name,
            device: deviceLabel(result.device_id),
          }),
        )
        resetForm()
        await speakersResource.load()
        return
      } catch (err) {
        lastError = err
        const copy = describeError(err)
        if (copy.status !== 409 || copy.code !== 'recording_incomplete') {
          actionError.value = copy
          return
        }
        await new Promise((resolve) => window.setTimeout(resolve, 800))
      }
    }
    actionError.value = lastError ? describeError(lastError) : null
  } finally {
    committing.value = false
    activeEnroll.value = ''
    remaining.value = 0
    clearTimers()
  }
}

async function startRecording(): Promise<void> {
  if (canRecord.value || !validate()) return
  actionError.value = null
  lastScore.value = null
  reRecordMode.value = false
  const person = name.value.trim()
  const device = deviceId.value
  try {
    const started = await api.startEnrollment({
      name: person,
      device_id: device,
      seconds: seconds.value,
    })
    activeEnroll.value = started.enroll_id
    remaining.value = started.seconds
    if (countdownTimer !== null) window.clearInterval(countdownTimer)
    const deadline = Date.now() + started.seconds * 1000
    countdownTimer = window.setInterval(() => {
      const left = Math.max(0, Math.ceil((deadline - Date.now()) / 1000))
      remaining.value = left
      if (left <= 0) {
        clearTimers()
        if (activeEnroll.value) void commitEnrollment(activeEnroll.value)
      }
    }, 200)
  } catch (err) {
    if (describeError(err).status === 401) promptForToken()
    actionError.value = describeError(err)
  }
}

/** Give up on a countdown locally; the server drops the file after 30 s. */
function cancelRecording(): void {
  clearTimers()
  activeEnroll.value = ''
  remaining.value = 0
}

function askDelete(nameValue: string, device?: string): void {
  pendingDelete.value = {
    name: nameValue,
    device,
    label: device
      ? t('v2.family.deleteConfirm', { name: nameValue, device: deviceLabel(device) })
      : t('v2.family.deleteConfirmAll', { name: nameValue }),
  }
}

async function confirmDelete(): Promise<void> {
  const target = pendingDelete.value
  if (!target) return
  deleting.value = true
  actionError.value = null
  try {
    const result = await api.deleteSpeaker(target.name, target.device)
    pendingDelete.value = null
    flashSuccess(t('v2.family.deleted', { count: result.removed }))
    await speakersResource.load()
  } catch (err) {
    actionError.value = describeError(err)
  } finally {
    deleting.value = false
  }
}

onMounted(() => {
  void refreshAll()
})

onBeforeUnmount(() => {
  clearTimers()
  stopSample()
  if (successTimer !== null) window.clearTimeout(successTimer)
})
</script>

<template>
  <div class="v2-page">
    <header class="v2-page-head">
      <div class="v2-page-title">
        <span class="kicker">{{ t('v2.nav.rooms') }}</span>
        <h1>{{ t('v2.family.title') }}</h1>
        <p class="lede">{{ t('v2.family.subtitle') }}</p>
      </div>
      <div class="v2-page-actions">
        <span v-if="people.length" class="meta" data-testid="family-count">
          {{ t('v2.family.count', { count: people.length }) }}
        </span>
        <button
          type="button"
          class="btn btn-sm"
          :disabled="loading"
          data-testid="family-refresh"
          @click="refreshAll()"
        >
          <AppIcon name="refresh" :size="14" />
          {{ t('v2.common.refresh') }}
        </button>
      </div>
    </header>

    <div v-if="success" class="banner banner-ok v2-banner" role="status" data-testid="family-success">
      <AppIcon name="check" :size="17" />
      <div class="v2-banner-body">
        <strong>{{ success }}</strong>
        <p v-if="lastScore" class="note" data-testid="family-score">
          <template v-if="lastScore.score === null">{{ t('v2.family.scoreNone') }}</template>
          <template v-else>
            {{ t('v2.family.score', { score: formatScore(lastScore.score) }) }} ·
            {{
              lastScore.reference === 'others'
                ? t('v2.family.referenceOthers')
                : t('v2.family.referencePrevious')
            }}
          </template>
        </p>
      </div>
      <button type="button" class="btn btn-sm" @click="success = ''">
        {{ t('v2.common.close') }}
      </button>
    </div>

    <div v-if="actionError" class="banner banner-warn v2-banner" role="alert" data-testid="family-error">
      <AppIcon name="alert" :size="17" />
      <div class="v2-banner-body">
        <strong>{{ fieldCopy?.title }}</strong>
        <p class="note">{{ fieldCopy?.detail }}</p>
      </div>
      <button type="button" class="btn btn-sm" @click="actionError = null">
        {{ t('v2.common.close') }}
      </button>
    </div>

    <div class="v2-detail-grid">
      <section class="v2-card" data-testid="family-list">
        <h2 class="v2-card-title">{{ t('v2.family.title') }}</h2>

        <StateBlock
          v-if="loading && !people.length"
          kind="loading"
          :title="t('v2.common.loading')"
        />
        <StateBlock
          v-else-if="errorCopy && !people.length"
          kind="error"
          :title="errorCopy.title"
          :detail="errorCopy.detail"
          :hint="errorCopy.hint"
          :status="errorCopy.status"
          :request-id="errorCopy.requestId"
          :retry-label="t('v2.common.retry')"
          show-retry
          data-testid="family-load-error"
          @retry="speakersResource.load()"
        />
        <StateBlock
          v-else-if="!people.length"
          kind="empty"
          :title="t('v2.family.empty.title')"
          :detail="t('v2.family.empty.detail')"
          data-testid="family-empty"
        >
          <RouterLink class="btn btn-sm" to="/devices">
            {{ t('v2.family.empty.action') }}
          </RouterLink>
        </StateBlock>

        <div v-else class="v2-family-list">
          <article
            v-for="person in people"
            :key="person.name"
            class="v2-family-person"
            :data-testid="`family-person-${person.name}`"
          >
            <header class="v2-family-head">
              <div>
                <h3 class="v2-family-name">{{ person.name }}</h3>
                <p class="meta">
                  {{ t('v2.family.deviceCount', { count: person.devices.length }) }}
                </p>
              </div>
              <button
                type="button"
                class="btn btn-sm btn-danger"
                :disabled="deleting"
                data-testid="family-delete-all"
                @click="askDelete(person.name)"
              >
                <AppIcon name="trash" :size="14" />
                {{ t('v2.family.deleteAll') }}
              </button>
            </header>

            <div class="table-wrap"><table class="table">
              <thead>
                <tr>
                  <th scope="col">{{ t('v2.family.col.devices') }}</th>
                  <th scope="col">{{ t('v2.family.col.enrolledAt') }}</th>
                  <th scope="col">{{ t('v2.family.col.actions') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="device in person.devices"
                  :key="rowKey(person, device)"
                  :data-testid="`family-device-${device.device_id}`"
                >
                  <td>
                    <strong>{{ deviceLabel(device.device_id) }}</strong>
                    <span class="v2-family-device-id">{{ device.device_id }}</span>
                    <span class="meta">{{ t('v2.family.samples', { count: device.samples }) }}</span>
                  </td>
                  <td class="meta">{{ device.enrolled_at || t('v2.common.na') }}</td>
                  <td class="v2-family-actions">
                    <button
                      v-if="device.sample"
                      type="button"
                      class="btn btn-sm"
                      :data-testid="`family-play-${device.device_id}`"
                      @click="playSample(person, device)"
                    >
                      <AppIcon :name="playingKey === rowKey(person, device) ? 'stop' : 'play'" :size="14" />
                      {{
                        playingKey === rowKey(person, device)
                          ? t('v2.family.stop')
                          : t('v2.family.play')
                      }}
                    </button>
                    <button
                      type="button"
                      class="btn btn-sm"
                      :disabled="canRecord"
                      :data-testid="`family-rerecord-${device.device_id}`"
                      @click="startReRecord(person, device)"
                    >
                      {{ t('v2.family.reRecord') }}
                    </button>
                    <button
                      type="button"
                      class="btn btn-sm btn-danger"
                      :disabled="deleting"
                      :data-testid="`family-delete-${device.device_id}`"
                      @click="askDelete(person.name, device.device_id)"
                    >
                      {{ t('v2.family.delete') }}
                    </button>
                  </td>
                </tr>
              </tbody>
            </table></div>
          </article>
        </div>
      </section>

      <section class="v2-card" data-testid="family-form">
        <h2 class="v2-card-title">
          {{ reRecordMode && name.trim() && deviceId
            ? t('v2.family.form.editing', {
              name: name.trim(), device: deviceLabel(deviceId),
            })
            : t('v2.family.form.title') }}
        </h2>

        <p v-if="!recordableDevices.length" class="v2-warn-text" data-testid="family-no-devices">
          {{ t('v2.family.needDevices') }}
        </p>

        <div v-else class="v2-family-form">
          <div class="field-row" :class="{ invalid: Boolean(fieldError) }">
            <div class="field-meta">
              <label class="field-label" for="v2-family-name">{{ t('v2.family.form.name') }}</label>
            </div>
            <div class="field-control">
              <input
                id="v2-family-name"
                v-model="name"
                class="input"
                type="text"
                maxlength="20"
                :placeholder="t('v2.family.form.namePlaceholder')"
                :disabled="canRecord"
                data-testid="family-name"
              />
            </div>
          </div>

          <div class="field-row">
            <div class="field-meta">
              <label class="field-label" for="v2-family-device">
                {{ t('v2.family.form.device') }}
              </label>
            </div>
            <div class="field-control">
              <select
                id="v2-family-device"
                v-model="deviceId"
                class="select"
                :disabled="canRecord"
                data-testid="family-device"
              >
                <option value="">{{ t('v2.family.form.devicePlaceholder') }}</option>
                <option v-for="device in recordableDevices" :key="device.id" :value="device.id">
                  {{ device.room || device.id }} · {{ device.id }}
                </option>
              </select>
              <p v-if="selectedDevice" class="field-hint">
                {{ selectedDevice.room || selectedDevice.id }}
                · {{ selectedDevice.ready ? t('v2.common.enabled') : t('v2.family.deviceOffline') }}
              </p>
            </div>
          </div>

          <div class="field-row">
            <div class="field-meta">
              <label class="field-label" for="v2-family-seconds">
                {{ t('v2.family.form.seconds') }}
              </label>
            </div>
            <div class="field-control">
              <select
                id="v2-family-seconds"
                v-model.number="seconds"
                class="select"
                :disabled="canRecord"
                data-testid="family-seconds"
              >
                <option v-for="value in [2, 3, 5, 8, 10]" :key="value" :value="value">
                  {{ t('v2.family.form.secondsValue', { count: value }) }}
                </option>
              </select>
            </div>
          </div>

          <p class="field-hint">
            {{ t('v2.family.form.hint', {
              name: name.trim() || t('v2.family.form.namePlaceholder'),
              seconds,
            }) }}
          </p>

          <p v-if="fieldError" class="field-error" role="alert" data-testid="family-field-error">
            {{ fieldError }}
          </p>

          <div v-if="activeEnroll || committing" class="v2-family-recording" role="status" data-testid="family-recording">
            <AppIcon name="speaker" :size="16" />
            <strong v-if="committing">{{ t('v2.family.committing') }}</strong>
            <strong v-else>{{ t('v2.family.recording', { count: remaining }) }}</strong>
            <button
              v-if="!committing"
              type="button"
              class="btn btn-sm"
              data-testid="family-cancel"
              @click="cancelRecording"
            >
              {{ t('v2.family.form.cancel') }}
            </button>
          </div>

          <div class="v2-family-submit">
            <button
              type="button"
              class="btn btn-primary"
              :disabled="canRecord || !recordableDevices.length"
              data-testid="family-start"
              @click="startRecording"
            >
              <AppIcon name="speaker" :size="15" />
              {{ t('v2.family.form.start') }}
            </button>
            <button
              v-if="name || deviceId"
              type="button"
              class="btn"
              :disabled="canRecord"
              data-testid="family-form-reset"
              @click="resetForm"
            >
              {{ t('v2.common.cancel') }}
            </button>
          </div>
        </div>
      </section>
    </div>

    <ConfirmDialog
      v-if="pendingDelete"
      :title="pendingDelete.label"
      :confirm-label="t('v2.family.delete')"
      :cancel-label="t('v2.common.cancel')"
      tone="danger"
      :busy="deleting"
      :busy-label="t('v2.family.busy')"
      data-testid="family-delete-dialog"
      @confirm="confirmDelete"
      @cancel="pendingDelete = null"
    >
      <p class="v2-danger-explain">{{ t('v2.family.deleteNote') }}</p>
    </ConfirmDialog>
  </div>
</template>

<style scoped>
.v2-family-list {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.v2-family-person {
  border-top: 1px solid var(--line, #e5e7eb);
  padding-top: 14px;
}

.v2-family-person:first-child {
  border-top: 0;
  padding-top: 0;
}

.v2-family-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.v2-family-name {
  margin: 0 0 2px;
  font-size: 1rem;
}

.v2-family-device-id {
  margin: 0 6px;
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 0.82em;
  opacity: 0.65;
}

.v2-family-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.v2-family-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.v2-family-recording {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--surface-2, #f6f7f9);
}

.v2-family-submit {
  display: flex;
  gap: 8px;
}
</style>
