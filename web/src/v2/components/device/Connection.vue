<script setup lang="ts">
/**
 * Connection card + token management (M2).
 *
 * Default state is the masked `ws_url` from `GET /devices/{id}/connection`.
 * Revealing calls `POST .../connection/reveal`; rotating calls `POST .../token/rotate`
 * after an explicit second confirmation. Both secrets render once and disappear
 * after 30 seconds (or immediately via "hide"). A 429 rotate cooldown and the
 * two 409 cases (environment token, adapter-managed token) get readable copy;
 * adapter-managed devices never see a raw failure, only the "rotate it on the
 * device" instruction.
 */
import { computed, onUnmounted, ref, watch } from 'vue'
import AppIcon from '../../../components/AppIcon.vue'
import {
  api,
  type ConnectionInfo,
  type RevealedConnection,
  type RotatedToken,
} from '../../api'
import {
  apiErrorCode,
  apiErrorDetail,
  apiRetryAfterSeconds,
  describeError,
  type ErrorCopy,
} from '../../errors'
import { pickLocalized, hostSourceLabel, t, transportLabel } from '../../i18n'
import StateBlock from '../StateBlock.vue'
import ErrorNotice from './ErrorNotice.vue'

const props = defineProps<{
  deviceId: string
  connection: ConnectionInfo | null
  loading: boolean
  errorCopy: ErrorCopy | null
}>()

const emit = defineEmits<{ retry: []; refresh: [] }>()

const HIDE_AFTER_S = 30

const revealed = ref<RevealedConnection | null>(null)
const revealError = ref<ErrorCopy | null>(null)
const revealing = ref(false)
const revealSeconds = ref(0)
const revealCooldown = ref(0)
let revealTimer: number | null = null
let revealCooldownTimer: number | null = null

const rotated = ref<RotatedToken | null>(null)
const rotateError = ref<ErrorCopy | null>(null)
const rotateConfirm = ref(false)
const rotating = ref(false)
const rotateSeconds = ref(0)
const cooldown = ref(0)
const adapterManagedByError = ref(false)
let rotateTimer: number | null = null
let cooldownTimer: number | null = null

const adapterManaged = computed(
  () => Boolean(props.connection?.token_managed_by_adapter) || adapterManagedByError.value,
)

const manageDisabled = computed(() => adapterManaged.value)

function clearRevealTimer(): void {
  if (revealTimer !== null) {
    window.clearInterval(revealTimer)
    revealTimer = null
  }
}

function clearRotateTimer(): void {
  if (rotateTimer !== null) {
    window.clearInterval(rotateTimer)
    rotateTimer = null
  }
}

function clearCooldownTimer(): void {
  if (cooldownTimer !== null) {
    window.clearInterval(cooldownTimer)
    cooldownTimer = null
  }
}

function clearRevealCooldownTimer(): void {
  if (revealCooldownTimer !== null) {
    window.clearInterval(revealCooldownTimer)
    revealCooldownTimer = null
  }
}

function hideReveal(): void {
  clearRevealTimer()
  revealSeconds.value = 0
  revealed.value = null
}

function hideRotated(): void {
  clearRotateTimer()
  rotateSeconds.value = 0
  rotated.value = null
}

function startRevealCountdown(): void {
  clearRevealTimer()
  revealSeconds.value = HIDE_AFTER_S
  revealTimer = window.setInterval(() => {
    revealSeconds.value -= 1
    if (revealSeconds.value <= 0) hideReveal()
  }, 1000)
}

function startRotateCountdown(): void {
  clearRotateTimer()
  rotateSeconds.value = HIDE_AFTER_S
  rotateTimer = window.setInterval(() => {
    rotateSeconds.value -= 1
    if (rotateSeconds.value <= 0) hideRotated()
  }, 1000)
}

function startCooldown(seconds: number): void {
  clearCooldownTimer()
  cooldown.value = Math.max(0, Math.ceil(seconds))
  if (cooldown.value <= 0) return
  cooldownTimer = window.setInterval(() => {
    cooldown.value -= 1
    if (cooldown.value <= 0) {
      clearCooldownTimer()
    }
  }, 1000)
}

/** Same UX as the rotate cooldown, driven by a 429 from the reveal endpoint. */
function startRevealCooldown(seconds: number): void {
  clearRevealCooldownTimer()
  revealCooldown.value = Math.max(0, Math.ceil(seconds))
  if (revealCooldown.value <= 0) return
  revealCooldownTimer = window.setInterval(() => {
    revealCooldown.value -= 1
    if (revealCooldown.value <= 0) {
      clearRevealCooldownTimer()
    }
  }, 1000)
}

function resetSecrets(): void {
  hideReveal()
  hideRotated()
  clearCooldownTimer()
  clearRevealCooldownTimer()
  cooldown.value = 0
  revealCooldown.value = 0
  revealError.value = null
  rotateError.value = null
  rotateConfirm.value = false
  adapterManagedByError.value = false
}

async function reveal(): Promise<void> {
  if (revealing.value || revealCooldown.value > 0) return
  revealing.value = true
  revealError.value = null
  try {
    revealed.value = await api.revealConnection(props.deviceId)
    startRevealCountdown()
  } catch (err) {
    const code = apiErrorCode(err)
    if (code === 'token_managed_by_adapter') {
      adapterManagedByError.value = true
    } else {
      const copy = describeError(err)
      revealError.value = copy
      // 429: honour details.retry_after_s, then Retry-After, then 10 s. The
      // button and its retry action stay disabled until the countdown ends.
      if (copy.status === 429 || code === 'rate_limited') {
        startRevealCooldown(apiRetryAfterSeconds(err) ?? 10)
      }
    }
  } finally {
    revealing.value = false
  }
}

/** Retry must re-open the confirmation, never rotate directly. */
function requestRotate(): void {
  if (rotating.value || cooldown.value > 0 || manageDisabled.value) return
  rotateConfirm.value = true
}

async function rotate(): Promise<void> {
  // Defense in depth: the API call is only reachable after the confirm gate.
  if (!rotateConfirm.value) return
  if (rotating.value || cooldown.value > 0 || manageDisabled.value) return
  rotating.value = true
  rotateError.value = null
  try {
    rotated.value = await api.rotateToken(props.deviceId)
    rotateConfirm.value = false
    hideReveal()
    startRotateCountdown()
    // The server enforces a 10 s rotation cooldown; reflect it so the button
    // never invites a click that is guaranteed to 429.
    startCooldown(10)
    emit('refresh')
  } catch (err) {
    const code = apiErrorCode(err)
    rotateConfirm.value = false
    if (code === 'token_managed_by_adapter') {
      adapterManagedByError.value = true
    } else {
      rotateError.value = describeError(err)
      if (code === 'rate_limited') {
        const retry = Number(apiErrorDetail(err, 'retry_after_s'))
        startCooldown(Number.isFinite(retry) && retry > 0 ? retry : 10)
      }
    }
  } finally {
    rotating.value = false
  }
}

function connectionHint(info: ConnectionInfo | null): string {
  if (!info?.hint) return ''
  return typeof info.hint === 'string' ? info.hint : pickLocalized(info.hint)
}

function hostHint(info: ConnectionInfo | null): string {
  return info ? pickLocalized(info.host_hint) : ''
}

function boolText(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return t('v2.common.na')
  return value ? t('v2.common.yes') : t('v2.common.no')
}

watch(
  () => props.deviceId,
  () => resetSecrets(),
)

onUnmounted(() => {
  clearRevealTimer()
  clearRotateTimer()
  clearCooldownTimer()
  clearRevealCooldownTimer()
})
</script>

<template>
  <section class="v2-card">
    <h2 class="v2-card-title">{{ t('v2.connection.title') }}</h2>
    <p class="note v2-card-lede">{{ t('v2.connection.lede') }}</p>

    <div
      v-if="errorCopy && connection"
      class="banner banner-warn v2-banner v2-connection-stale"
      role="status"
    >
      <AppIcon name="alert" :size="16" />
      <div class="v2-banner-body">
        <strong>{{ t('v2.connection.stale') }}</strong>
        <p class="note">{{ errorCopy.hint }}</p>
      </div>
      <button type="button" class="btn btn-sm" @click="emit('retry')">
        {{ t('v2.common.retry') }}
      </button>
    </div>

    <StateBlock v-if="loading && !connection" kind="loading" :title="t('v2.common.loading')" />
    <StateBlock
      v-else-if="errorCopy && !connection"
      kind="error"
      :title="t('v2.connection.unavailable')"
      :detail="errorCopy.detail"
      :hint="errorCopy.hint"
      :status="errorCopy.status"
      :request-id="errorCopy.requestId"
      show-retry
      @retry="emit('retry')"
    />
    <StateBlock
      v-else-if="!connection"
      kind="empty"
      :title="t('v2.connection.unavailable')"
      :hint="t('v2.connection.unavailableHint')"
      show-retry
      @retry="emit('retry')"
    />

    <template v-else>
      <dl class="v2-kv">
        <div>
          <dt>{{ t('v2.connection.transport') }}</dt>
          <dd>{{ transportLabel(connection.transport) }}</dd>
        </div>
        <div>
          <dt>{{ t('v2.connection.wsUrl') }}</dt>
          <dd>
            <code v-if="connection.ws_url_masked" class="v2-code">
              {{ connection.ws_url_masked }}
            </code>
            <span v-else class="muted">{{ t('v2.connection.wsUrlNone') }}</span>
          </dd>
        </div>
        <div>
          <dt>{{ t('v2.connection.deviceToken') }}</dt>
          <dd>
            <span
              class="badge"
              :class="connection.token_configured ? 'badge-ok' : 'badge-warn'"
            >
              {{
                connection.token_configured
                  ? t('v2.connection.configured')
                  : t('v2.connection.notConfigured')
              }}
            </span>
            <span v-if="connection.token_managed_by_adapter" class="tag">
              {{ t('v2.connection.tokenManaged') }}
            </span>
            <span
              v-else-if="!connection.token_configured"
              class="note v2-block"
            >
              {{ t('v2.connection.tokenMissing') }}
            </span>
          </dd>
        </div>
        <div v-if="connection.token_env">
          <dt>{{ t('v2.connection.tokenEnv') }}</dt>
          <dd class="mono">{{ connection.token_env }}</dd>
        </div>
        <div>
          <dt>{{ t('v2.connection.allowNoToken') }}</dt>
          <dd>{{ boolText(connection.allow_no_token) }}</dd>
        </div>
        <div v-if="connection.listen_port">
          <dt>{{ t('v2.connection.listenPort') }}</dt>
          <dd class="mono">{{ connection.listen_port }}</dd>
        </div>
        <div>
          <dt>{{ t('v2.connection.hostSource') }}</dt>
          <dd>{{ hostSourceLabel(connection.host_source) }}</dd>
        </div>
      </dl>
      <p v-if="connectionHint(connection)" class="note">
        <span class="v2-k">{{ t('v2.connection.hint') }}</span>
        {{ connectionHint(connection) }}
      </p>
      <p v-if="hostHint(connection)" class="note">{{ hostHint(connection) }}</p>

      <div v-if="adapterManaged" class="v2-adapter-managed" role="note">
        <AppIcon name="lock" :size="17" />
        <div>
          <strong>{{ t('v2.connection.adapterManaged') }}</strong>
          <p class="note">{{ t('v2.connection.adapterManagedHint') }}</p>
        </div>
      </div>

      <div class="v2-connection-manage">
        <h3>{{ t('v2.connection.manage') }}</h3>

        <div class="v2-manage-row">
          <div class="v2-manage-copy">
            <strong>{{ t('v2.connection.reveal') }}</strong>
            <p class="note">{{ t('v2.connection.revealHint') }}</p>
          </div>
          <button
            type="button"
            class="btn btn-sm"
            :disabled="revealing || manageDisabled || revealCooldown > 0"
            @click="reveal"
          >
            <AppIcon name="key" :size="14" />
            {{ revealing ? t('v2.connection.revealing') : t('v2.connection.reveal') }}
          </button>
        </div>

        <ErrorNotice
          :error="revealError"
          :show-retry="Boolean(revealError?.retryable) && revealCooldown === 0"
          @retry="reveal"
        />

        <p v-if="revealCooldown > 0" class="note v2-cooldown" role="status">
          {{ t('v2.connection.revealCooldown', { n: revealCooldown }) }}
        </p>

        <div v-if="revealed" class="v2-secret-block">
          <div class="v2-secret-head">
            <strong>{{ t('v2.connection.revealedToken') }}</strong>
            <span class="tag">{{ t('v2.connection.hideIn', { n: revealSeconds }) }}</span>
            <button type="button" class="btn btn-sm btn-ghost" @click="hideReveal">
              {{ t('v2.connection.hide') }}
            </button>
          </div>
          <code class="v2-secret mono">{{ revealed.token }}</code>
          <p class="v2-secret-label">{{ t('v2.connection.revealedUrl') }}</p>
          <code class="v2-secret mono">{{ revealed.ws_url }}</code>
          <p class="note">{{ t('v2.connection.revealedOnce') }}</p>
        </div>

        <div class="v2-manage-row v2-manage-row--top">
          <div class="v2-manage-copy">
            <strong>{{ t('v2.connection.rotate') }}</strong>
            <p class="note">{{ t('v2.connection.rotateHint') }}</p>
          </div>
          <button
            type="button"
            class="btn btn-sm"
            :disabled="rotating || cooldown > 0 || manageDisabled"
            @click="requestRotate"
          >
            <AppIcon name="refresh" :size="14" />
            {{ rotating ? t('v2.connection.rotating') : t('v2.connection.rotate') }}
          </button>
        </div>

        <p v-if="cooldown > 0" class="note v2-cooldown" role="status">
          {{ t('v2.connection.cooldown', { n: cooldown }) }}
        </p>

        <div
          v-if="rotateConfirm"
          class="v2-confirm"
          role="alertdialog"
          aria-labelledby="v2-rotate-confirm-title"
        >
          <strong id="v2-rotate-confirm-title">
            {{ t('v2.connection.rotateConfirmTitle') }}
          </strong>
          <p class="note">{{ t('v2.connection.rotateConfirmText') }}</p>
          <div class="actions">
            <button type="button" class="btn btn-sm" @click="rotateConfirm = false">
              {{ t('v2.connection.rotateCancel') }}
            </button>
            <button
              type="button"
              class="btn btn-sm btn-primary"
              :disabled="rotating || cooldown > 0"
              @click="rotate"
            >
              {{ t('v2.connection.rotateConfirmAction') }}
            </button>
          </div>
        </div>

        <ErrorNotice
          :error="rotateError"
          :show-retry="Boolean(rotateError?.retryable) && cooldown === 0"
          @retry="requestRotate"
        />

        <div v-if="rotated" class="v2-secret-block">
          <div class="v2-secret-head">
            <strong>{{ t('v2.connection.rotateDone') }}</strong>
            <span class="tag">{{ t('v2.connection.hideIn', { n: rotateSeconds }) }}</span>
            <button type="button" class="btn btn-sm btn-ghost" @click="hideRotated">
              {{ t('v2.connection.hide') }}
            </button>
          </div>
          <p class="v2-secret-label">{{ t('v2.connection.revealedToken') }}</p>
          <code class="v2-secret mono">{{ rotated.token }}</code>
          <p class="v2-secret-label">{{ t('v2.connection.revealedUrl') }}</p>
          <code class="v2-secret mono">{{ rotated.ws_url }}</code>
          <p class="note">{{ t('v2.connection.revealedOnce') }}</p>
        </div>
      </div>
    </template>
  </section>
</template>
