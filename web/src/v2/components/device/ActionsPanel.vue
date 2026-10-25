<script setup lang="ts">
/**
 * Capability-gated actions (contract §5.4/§7.21).
 *
 * Every control group is rendered even when its capability is missing; the
 * controls are disabled and the card names the capability plus the server's
 * `reason` (or the `unknown_capabilities` list), so nothing is hidden silently.
 * One action runs at a time (`busy`) to prevent double-clicks, and failures only
 * set a local error — the loaded device detail is never discarded.
 */
import { computed, ref, watch } from 'vue'
import AppIcon from '../../../components/AppIcon.vue'
import {
  api,
  type CapabilityEntry,
  type DeviceActionId,
  type DeviceActionResponse,
  type DeviceDetail,
} from '../../api'
import { capabilityItem, capabilityLabel } from '../../capabilities'
import { describeError, type ErrorCopy } from '../../errors'
import { displayValue } from '../../format'
import {
  capabilityReasonLabel,
  t,
  type MsgKey,
} from '../../i18n'
import StateBlock from '../StateBlock.vue'
import ErrorNotice from './ErrorNotice.vue'

const props = defineProps<{
  detail: DeviceDetail
  enabled: boolean
}>()

const emit = defineEmits<{ spoken: [] }>()

const LED_FALLBACK = [
  'off',
  'listening',
  'thinking',
  'speaking',
  'wake',
  'error',
  'music',
  'music_paused',
  'muted',
]

const EFFECT_KEYS: Record<string, MsgKey> = {
  off: 'v2.actions.effect.off',
  listening: 'v2.actions.effect.listening',
  thinking: 'v2.actions.effect.thinking',
  speaking: 'v2.actions.effect.speaking',
  wake: 'v2.actions.effect.wake',
  error: 'v2.actions.effect.error',
  music: 'v2.actions.effect.music',
  music_paused: 'v2.actions.effect.music_paused',
  muted: 'v2.actions.effect.muted',
}

const busy = ref<DeviceActionId | null>(null)
const lastAction = ref<DeviceActionResponse | null>(null)
const errorCopy = ref<ErrorCopy | null>(null)
const lastAttempt = ref<{ action: DeviceActionId; args: Record<string, unknown> } | null>(null)

const volumeValue = ref(128)
const volumeCurrent = ref<number | null>(null)
const volumeRange = ref('')
const ledEffect = ref('listening')
const sayText = ref('')
const sayConfirm = ref(false)
const playFile = ref('')
const playLoop = ref(false)
const playGain = ref(1)

const capabilityEntries = computed(
  () => new Map((props.detail.capabilities ?? []).map((entry) => [entry.id, entry])),
)
const effectiveIds = computed(
  () =>
    new Set(
      props.detail.effective_capabilities ??
        (props.detail.capabilities ?? [])
          .filter((entry) => entry.effective)
          .map((entry) => entry.id),
    ),
)
const unknownCaps = computed(() => props.detail.unknown_capabilities ?? [])

const ledEffects = computed<string[]>(() => {
  const action = capabilityItem('led')?.actions?.find((entry) => entry.id === 'led.set')
  const schema = action?.args_schema as
    | { properties?: { effect?: { enum?: unknown } } }
    | undefined
  const values = schema?.properties?.effect?.enum
  if (
    Array.isArray(values) &&
    values.length > 0 &&
    values.every((value) => typeof value === 'string')
  ) {
    return values as string[]
  }
  return [...LED_FALLBACK]
})

function capEntry(id: string): CapabilityEntry | undefined {
  return capabilityEntries.value.get(id)
}

function capEffective(id: string): boolean {
  return effectiveIds.value.has(id)
}

/**
 * Why a capability's controls are disabled. Empty string means enabled.
 * `unknown_capabilities` are not action capabilities, so they are explained in
 * their own note rather than mapped onto a button group.
 */
function gateReason(id: string): string {
  if (!props.detail.enabled) return t('v2.actions.disabledDevice')
  if (!capEffective(id)) {
    const entry = capEntry(id)
    const reason = entry
      ? capabilityReasonLabel(entry.reason)
      : t('v2.capability.reason.not_declared')
    return t('v2.actions.capabilityMissing', {
      capability: capabilityLabel(id),
      reason,
    })
  }
  if (props.detail.connected !== true) return t('v2.actions.offline')
  return ''
}

function blocked(id: string): boolean {
  return Boolean(gateReason(id))
}

function clampVolume(value: number): number {
  if (!Number.isFinite(value)) return 128
  return Math.min(255, Math.max(1, Math.round(value)))
}

function effectLabel(effect: string): string {
  const key = EFFECT_KEYS[effect]
  return key ? t(key) : effect
}

/**
 * Only `sendSay()` owns this module-private token, which owns the mandatory
 * second confirmation. Everything else (including retry) must not be able to
 * replay speech silently.
 */
const SAY_CONFIRM = Symbol('v2.audio.say.confirmed')

interface RunOptions {
  sayToken?: typeof SAY_CONFIRM
}

async function run(
  action: DeviceActionId,
  args: Record<string, unknown> = {},
  options: RunOptions = {},
): Promise<void> {
  if (busy.value) return
  if (action === 'audio.say' && options.sayToken !== SAY_CONFIRM) {
    // Defense in depth: even a future call site cannot bypass the say confirm.
    errorCopy.value = {
      code: 'say_confirmation_required',
      status: null,
      title: t('v2.actions.sayConfirmTitle'),
      detail: t('v2.actions.sayConfirmRequired'),
      hint: t('v2.actions.sayWarning'),
      backendHint: '',
      requestId: null,
      retryable: false,
      retryAfterS: null,
      fields: [],
    }
    return
  }
  lastAttempt.value = { action, args }
  busy.value = action
  errorCopy.value = null
  try {
    const response = await api.deviceAction(props.detail.id, action, args)
    lastAction.value = response
    const result = response.result ?? {}
    if (action === 'volume.read' || action === 'volume.set') {
      if (typeof result.value === 'number') {
        volumeCurrent.value = result.value
        volumeValue.value = clampVolume(result.value)
      }
      if (typeof result.range === 'string') volumeRange.value = result.range
    }
    if (action === 'audio.say') emit('spoken')
  } catch (err) {
    errorCopy.value = describeError(err)
  } finally {
    busy.value = null
  }
}

function askSay(): void {
  if (!sayText.value.trim() || blocked('playback') || busy.value) return
  sayConfirm.value = true
}

async function sendSay(): Promise<void> {
  const text = sayText.value.trim()
  if (!text || busy.value || blocked('playback')) return
  sayConfirm.value = false
  await run('audio.say', { text }, { sayToken: SAY_CONFIRM })
}

const gainValue = computed(() => {
  const value = Number(playGain.value)
  return Number.isFinite(value) && value > 0 ? value : 1
})

function sendPlay(): void {
  const file = playFile.value.trim()
  if (!file) return
  void run('audio.play', { file, loop: playLoop.value, gain: gainValue.value })
}

function retryLast(): void {
  const attempt = lastAttempt.value
  if (!attempt) return
  if (attempt.action === 'audio.say') {
    // Retry must never replay speech directly: restore the original text and
    // re-arm the confirmation gate instead.
    const text = typeof attempt.args.text === 'string' ? attempt.args.text : ''
    if (!text.trim()) return
    sayText.value = text
    errorCopy.value = null
    sayConfirm.value = true
    return
  }
  void run(attempt.action, attempt.args)
}

const canRetry = computed(() => {
  const error = errorCopy.value
  const attempt = lastAttempt.value
  if (!error || !attempt) return false
  if (attempt.action === 'audio.say') {
    const text = typeof attempt.args.text === 'string' ? attempt.args.text : ''
    if (!text.trim()) return false
  }
  return error.retryable || error.code === 'unauthorized' || error.code === 'device_not_connected'
})

interface ResultRow {
  key: string
  label: string
  value: string
  fail?: boolean
}

function asText(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.join(', ') : t('v2.common.na')
  return displayValue(value)
}

const resultRows = computed<ResultRow[]>(() => {
  const response = lastAction.value
  if (!response) return []
  const result = response.result ?? {}
  const rows: ResultRow[] = []
  switch (response.action) {
    case 'volume.read':
    case 'volume.set':
      rows.push({
        key: 'value',
        label: t('v2.actions.volumeValue'),
        value: displayValue(result.value),
      })
      if (result.range !== undefined) {
        rows.push({
          key: 'range',
          label: t('v2.actions.volumeRange', { range: String(result.range) }),
          value: String(result.range),
        })
      }
      break
    case 'led.set':
      rows.push({
        key: 'effect',
        label: t('v2.actions.ledEffect'),
        value: effectLabel(String(result.effect ?? '')),
      })
      break
    case 'audio.say':
      rows.push({
        key: 'reply',
        label: t('v2.actions.sayResultReply'),
        value: asText(result.reply),
      })
      rows.push({
        key: 'audio_bytes',
        label: t('v2.actions.sayResultBytes'),
        value: displayValue(result.audio_bytes),
      })
      if (result.tools !== undefined) {
        rows.push({
          key: 'tools',
          label: t('v2.actions.sayResultTools'),
          value: asText(result.tools),
        })
      }
      if (result.error) {
        rows.push({
          key: 'turn_error',
          label: t('v2.actions.sayResultError'),
          value: String(result.error),
          fail: true,
        })
      }
      break
    case 'audio.play':
      rows.push({
        key: 'playing',
        label: t('v2.actions.playing'),
        value: displayValue(result.playing),
      })
      break
    case 'audio.stop':
      rows.push({
        key: 'playing',
        label: t('v2.actions.playing'),
        value: t('v2.actions.stopped'),
      })
      break
    default:
      for (const [key, value] of Object.entries(result)) {
        rows.push({ key, label: key, value: asText(value) })
      }
  }
  return rows
})

const resultOk = computed(() => {
  const response = lastAction.value
  if (!response) return false
  const error = response.result?.error
  return response.ok && !error
})

watch(
  () => props.detail.id,
  () => {
    busy.value = null
    lastAction.value = null
    lastAttempt.value = null
    errorCopy.value = null
    volumeValue.value = 128
    volumeCurrent.value = null
    volumeRange.value = ''
    sayText.value = ''
    sayConfirm.value = false
    playFile.value = ''
    playLoop.value = false
    playGain.value = 1
  },
)
</script>

<template>
  <StateBlock
    v-if="!enabled"
    kind="unavailable"
    :title="t('v2.actions.featureOff')"
    :hint="t('v2.actions.featureOffHint')"
  />

  <section v-else class="v2-card">
    <div class="v2-card-head">
      <h2 class="v2-card-title">{{ t('v2.actions.title') }}</h2>
      <span v-if="busy" class="tag">{{ t('v2.actions.running') }}</span>
    </div>
    <p class="note v2-card-lede">{{ t('v2.actions.lede') }}</p>

    <p v-if="unknownCaps.length" class="note v2-warn-text">
      {{ t('v2.actions.unknownCaps', { ids: unknownCaps.join(', ') }) }}
    </p>

    <ErrorNotice :error="errorCopy" :show-retry="canRetry" @retry="retryLast" />

    <!-- volume -->
    <div class="v2-action-group">
      <header class="v2-action-head">
        <h3>{{ t('v2.actions.volume') }}</h3>
        <span class="tag mono">volume</span>
      </header>
      <p v-if="gateReason('volume')" class="v2-gate">{{ gateReason('volume') }}</p>
      <div class="v2-action-controls">
        <button
          type="button"
          class="btn btn-sm"
          :disabled="blocked('volume') || busy !== null"
          @click="run('volume.read')"
        >
          {{ busy === 'volume.read' ? t('v2.actions.running') : t('v2.actions.volumeRead') }}
        </button>
        <span class="mono v2-action-value">
          {{ t('v2.actions.volumeValue') }}:
          {{ volumeCurrent === null ? t('v2.actions.volumeUnread') : volumeCurrent }}
        </span>
        <span v-if="volumeRange" class="tag mono">
          {{ t('v2.actions.volumeRange', { range: volumeRange }) }}
        </span>
      </div>
      <div class="v2-action-controls v2-slider">
        <input
          v-model.number="volumeValue"
          type="range"
          min="1"
          max="255"
          step="1"
          :disabled="blocked('volume') || busy !== null"
          :aria-label="t('v2.actions.volume')"
        />
        <span class="mono v2-slider-value">{{ volumeValue }}</span>
        <button
          type="button"
          class="btn btn-sm btn-primary"
          :disabled="blocked('volume') || busy !== null"
          @click="run('volume.set', { value: volumeValue })"
        >
          {{ busy === 'volume.set' ? t('v2.actions.running') : t('v2.actions.volumeSet') }}
        </button>
      </div>
      <p class="note">{{ t('v2.actions.volumeHint') }}</p>
    </div>

    <!-- led -->
    <div class="v2-action-group">
      <header class="v2-action-head">
        <h3>{{ t('v2.actions.led') }}</h3>
        <span class="tag mono">led</span>
      </header>
      <p v-if="gateReason('led')" class="v2-gate">{{ gateReason('led') }}</p>
      <div class="v2-action-controls">
        <label class="v2-inline-field">
          <span class="meta">{{ t('v2.actions.ledEffect') }}</span>
          <select
            v-model="ledEffect"
            class="input v2-select"
            :disabled="blocked('led') || busy !== null"
            :aria-label="t('v2.actions.ledEffect')"
          >
            <option v-for="effect in ledEffects" :key="effect" :value="effect">
              {{ effectLabel(effect) }}
            </option>
          </select>
        </label>
        <button
          type="button"
          class="btn btn-sm"
          :disabled="blocked('led') || busy !== null"
          @click="run('led.set', { effect: ledEffect })"
        >
          {{ busy === 'led.set' ? t('v2.actions.running') : t('v2.actions.ledSet') }}
        </button>
      </div>
      <p class="note">{{ t('v2.actions.ledHint') }}</p>
    </div>

    <!-- playback / say -->
    <div class="v2-action-group">
      <header class="v2-action-head">
        <h3>{{ t('v2.actions.playback') }}</h3>
        <span class="tag mono">playback</span>
      </header>
      <p v-if="gateReason('playback')" class="v2-gate">{{ gateReason('playback') }}</p>

      <div class="v2-subgroup">
        <label class="field-label" for="v2-say-text">{{ t('v2.actions.sayText') }}</label>
        <textarea
          id="v2-say-text"
          v-model="sayText"
          class="input"
          maxlength="500"
          rows="2"
          :placeholder="t('v2.actions.sayPlaceholder')"
          :disabled="blocked('playback') || busy !== null"
        />
        <div class="v2-say-meta">
          <span class="note">{{ t('v2.actions.sayWarning') }}</span>
          <span class="meta mono">{{ t('v2.actions.charCount', { n: sayText.length }) }}</span>
        </div>
        <div class="actions">
          <button
            type="button"
            class="btn btn-sm btn-primary"
            :disabled="blocked('playback') || busy !== null || !sayText.trim()"
            @click="askSay"
          >
            <AppIcon name="speaker" :size="14" />
            {{ busy === 'audio.say' ? t('v2.actions.running') : t('v2.actions.say') }}
          </button>
        </div>

        <div
          v-if="sayConfirm"
          class="v2-confirm"
          role="alertdialog"
          aria-labelledby="v2-say-confirm-title"
        >
          <strong id="v2-say-confirm-title">{{ t('v2.actions.sayConfirmTitle') }}</strong>
          <p class="note">
            {{ t('v2.actions.sayConfirmHint', { room: detail.room || t('v2.devices.unassigned') }) }}
          </p>
          <p class="note v2-warn-text">{{ t('v2.actions.sayWarning') }}</p>
          <div class="actions">
            <button type="button" class="btn btn-sm" @click="sayConfirm = false">
              {{ t('v2.actions.sayCancel') }}
            </button>
            <button
              type="button"
              class="btn btn-sm btn-primary"
              :disabled="busy !== null || blocked('playback')"
              @click="sendSay"
            >
              {{ t('v2.actions.say') }}
            </button>
          </div>
        </div>
      </div>

      <div class="v2-subgroup">
        <label class="field-label" for="v2-play-file">{{ t('v2.actions.playFile') }}</label>
        <input
          id="v2-play-file"
          v-model="playFile"
          class="input mono"
          type="text"
          spellcheck="false"
          :placeholder="t('v2.actions.playFileHint')"
          :disabled="blocked('playback') || busy !== null"
        />
        <div class="v2-play-row">
          <label class="v2-checkbox">
            <input
              v-model="playLoop"
              type="checkbox"
              :disabled="blocked('playback') || busy !== null"
            />
            <span>{{ t('v2.actions.playLoop') }}</span>
          </label>
          <label class="v2-inline-field v2-gain">
            <span class="meta">{{ t('v2.actions.playGain') }}</span>
            <input
              v-model.number="playGain"
              class="input"
              type="number"
              step="0.1"
              :disabled="blocked('playback') || busy !== null"
            />
          </label>
          <button
            type="button"
            class="btn btn-sm"
            :disabled="blocked('playback') || busy !== null || !playFile.trim()"
            @click="sendPlay"
          >
            <AppIcon name="play" :size="14" />
            {{ busy === 'audio.play' ? t('v2.actions.running') : t('v2.actions.playAction') }}
          </button>
          <button
            type="button"
            class="btn btn-sm"
            :disabled="blocked('playback') || busy !== null"
            @click="run('audio.stop')"
          >
            <AppIcon name="stop" :size="13" />
            {{ busy === 'audio.stop' ? t('v2.actions.running') : t('v2.actions.stop') }}
          </button>
        </div>
        <p class="note">{{ t('v2.actions.playFileHint') }}</p>
      </div>
    </div>

    <!-- last result -->
    <p v-if="errorCopy && lastAction" class="note v2-warn-text">
      {{ t('v2.actions.staleResult') }}
    </p>
    <div v-if="lastAction" class="v2-action-result">
      <header class="v2-card-head">
        <h3>{{ t('v2.actions.result') }}</h3>
        <span class="tag mono">{{ lastAction.action }}</span>
        <span class="badge" :class="resultOk ? 'badge-ok' : 'badge-fail'">
          {{ lastAction.code }}
        </span>
      </header>
      <dl class="v2-kv">
        <div v-for="row in resultRows" :key="row.key">
          <dt>{{ row.label }}</dt>
          <dd class="mono" :class="{ 'v2-fail-text': row.fail }">{{ row.value }}</dd>
        </div>
        <div v-if="lastAction.turn">
          <dt>{{ t('v2.actions.turn') }}</dt>
          <dd class="mono">{{ lastAction.turn.turn_id }}</dd>
        </div>
        <div>
          <dt>{{ t('v2.actions.deviceStatus') }}</dt>
          <dd>
            <span class="tag">
              {{ lastAction.device_status.connected ? t('v2.actions.connected') : t('v2.actions.notConnected') }}
            </span>
            <span class="tag">
              {{ lastAction.device_status.ready ? t('v2.test.ready') : t('v2.test.notReady') }}
            </span>
          </dd>
        </div>
      </dl>
    </div>
    <p v-else-if="!errorCopy" class="empty">{{ t('v2.actions.noResult') }}</p>
    <p v-if="!lastAction && !errorCopy" class="note">{{ t('v2.actions.noResultHint') }}</p>
  </section>
</template>
