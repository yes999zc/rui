<script setup lang="ts">
/**
 * Channels page (Telegram + WeChat iLink/ClawBot) — maintenance, hand-written.
 *
 * GET/PUT /api/v1/channels is the only data source: Telegram is a bot token +
 * allowlisted chat ids, WeChat is a logged-in iLink account + allowlisted user
 * ids. The gateway itself is a separate process (`rui-channels`), so the
 * page saves `channels.json` and tells the operator to restart the gateway; it
 * never claims the change is live.
 *
 * Secrets stay out of the UI entirely: the API only exposes `token_configured`,
 * never a token value.
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import {
  api,
  ApiError,
  type ChannelsPatch,
  type ChannelsState,
  type ChannelGatewayStatus,
  type ChannelReplyMode,
  type WeixinLoginState,
  type WeixinLoginStatus,
} from '../api'
import { formatUptime, t, type MsgKey } from '../i18n'

interface ChannelDraft {
  enabled: boolean
  ids: string
  reply: ChannelReplyMode
}

interface Baseline {
  enabled: boolean
  ids: string[]
  reply: ChannelReplyMode
}

interface Feedback {
  channel: 'telegram' | 'weixin' | 'page'
  kind: 'ok' | 'fail'
  text: string
}

const REPLY_LABEL: Record<ChannelReplyMode, MsgKey> = {
  text: 'channels.reply.text',
  both: 'channels.reply.both',
  voice: 'channels.reply.voice',
}
const REPLY_MODES: ChannelReplyMode[] = ['text', 'both', 'voice']

const state = ref<ChannelsState | null>(null)
const loading = ref(true)
const loadError = ref('')
const saving = ref<'' | 'telegram' | 'weixin'>('')
const feedback = ref<Feedback | null>(null)
const copied = ref<'' | 'gateway'>('')
const copyFailed = ref(false)

// Managed gateway / 0-1 setup state.
const restartNeeded = ref(false)
const tokenDraft = ref('')
const tokenBusy = ref(false)
const tokenError = ref('')
const tokenNotice = ref('')
const telegramUsername = ref('')
const TG_USERNAME_KEY = 'rui-telegram-username'
const login = ref<WeixinLoginState | null>(null)
const loginBusy = ref(false)
const gatewayBusy = ref<'' | 'start' | 'stop' | 'restart'>('')
const gatewayError = ref('')
const gatewayNotice = ref('')
let loginTimer: number | undefined

const telegram = reactive<ChannelDraft>({ enabled: false, ids: '', reply: 'text' })
const weixin = reactive<ChannelDraft>({ enabled: false, ids: '', reply: 'text' })
const tgBase = ref<Baseline>({ enabled: false, ids: [], reply: 'text' })
const wxBase = ref<Baseline>({ enabled: false, ids: [], reply: 'text' })

/** IDs can arrive comma- or newline-separated; accept Chinese punctuation too. */
function parseIds(text: string): string[] {
  return text
    .split(/[\s,，;；]+/)
    .map((id) => id.trim())
    .filter(Boolean)
}

function idsText(ids: string[]): string {
  return ids.join(', ')
}

function sameIds(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false
  const left = [...a].sort()
  const right = [...b].sort()
  return left.every((id, index) => id === right[index])
}

function diffCount(draft: ChannelDraft, base: Baseline): number {
  let count = 0
  if (draft.enabled !== base.enabled) count += 1
  if (!sameIds(parseIds(draft.ids), base.ids)) count += 1
  if (draft.reply !== base.reply) count += 1
  return count
}

const tgDiff = computed(() => diffCount(telegram, tgBase.value))
const wxDiff = computed(() => diffCount(weixin, wxBase.value))
const dirty = computed(() => tgDiff.value > 0 || wxDiff.value > 0)
// The page-head badge describes what is saved, not the form's pending state.
const enabledCount = computed(
  () => (state.value?.telegram.enabled ? 1 : 0) + (state.value?.weixin.enabled ? 1 : 0),
)
const tgIdCount = computed(() => parseIds(telegram.ids).length)
const wxIdCount = computed(() => parseIds(weixin.ids).length)
const gatewayStatus = computed(() => state.value?.gateway.status ?? null)
const gatewayStatusLabel = computed(() =>
  gatewayStatus.value
    ? gatewayStatus.value.running
      ? t('channels.gateway.statusRunning')
      : t('channels.gateway.statusStopped')
    : t('channels.gateway.statusUnknown'),
)
const gatewayStatusTone = computed(() =>
  gatewayStatus.value ? (gatewayStatus.value.running ? 'badge-ok' : 'badge-muted') : 'badge-warn',
)
const loginActive = computed(
  () => login.value?.status === 'starting' || login.value?.status === 'waiting',
)
const LOGIN_LABEL: Record<WeixinLoginStatus, MsgKey> = {
  idle: 'channels.weixin.loginStatusIdle',
  starting: 'channels.weixin.loginStatusStarting',
  waiting: 'channels.weixin.loginStatusWaiting',
  confirmed: 'channels.weixin.loginStatusConfirmed',
  error: 'channels.weixin.loginStatusError',
}
const loginLabel = computed(() =>
  login.value ? t(LOGIN_LABEL[login.value.status] ?? 'channels.weixin.loginStatusIdle') : t('channels.weixin.loginStatusIdle'),
)
const loginTone = computed(() => {
  switch (login.value?.status) {
    case 'confirmed':
      return 'badge-ok'
    case 'starting':
    case 'waiting':
      return 'badge-warn'
    case 'error':
      return 'badge-fail'
    default:
      return 'badge-muted'
  }
})
/** Only render an inline QR for the data URI the backend actually sends. */
const qrSrc = computed(() => {
  const svg = login.value?.qr_svg ?? ''
  return svg.startsWith('data:image/svg+xml;base64,') ? svg : ''
})
const qrUrl = computed(() => login.value?.qr_url ?? '')
const loginAccount = computed(() => login.value?.account_id || state.value?.weixin.account_id || '')

function replyLabel(mode: ChannelReplyMode): string {
  return t(REPLY_LABEL[mode] ?? 'channels.reply.text')
}

function syncTelegram(): void {
  const source = state.value?.telegram
  if (!source) return
  telegram.enabled = source.enabled
  telegram.ids = idsText(source.allowed_chat_ids)
  telegram.reply = source.reply_mode
  tgBase.value = {
    enabled: source.enabled,
    ids: [...source.allowed_chat_ids],
    reply: source.reply_mode,
  }
}

function syncWeixin(): void {
  const source = state.value?.weixin
  if (!source) return
  weixin.enabled = source.enabled
  weixin.ids = idsText(source.allowed_user_ids)
  weixin.reply = source.reply_mode
  wxBase.value = {
    enabled: source.enabled,
    ids: [...source.allowed_user_ids],
    reply: source.reply_mode,
  }
}

/** The GET contract does not carry the bot username, so remember the last verified one. */
function cachedTelegramUsername(): string {
  try {
    return window.localStorage.getItem(TG_USERNAME_KEY) ?? ''
  } catch {
    return ''
  }
}

function rememberTelegramUsername(name: string): void {
  try {
    if (name) window.localStorage.setItem(TG_USERNAME_KEY, name)
    else window.localStorage.removeItem(TG_USERNAME_KEY)
  } catch {
    /* private mode: the username just won't survive a reload */
  }
}

/** After a save, refresh only the saved channel so the other draft is preserved. */
function applyState(next: ChannelsState, only?: 'telegram' | 'weixin' | 'none'): void {
  state.value = next
  if (only !== 'none') {
    if (only !== 'weixin') syncTelegram()
    if (only !== 'telegram') syncWeixin()
  }
  if (!next.telegram.token_configured) {
    telegramUsername.value = ''
    rememberTelegramUsername('')
  } else if (!telegramUsername.value) {
    telegramUsername.value = cachedTelegramUsername()
  }
}

function messageOf(exc: unknown): string {
  if (exc instanceof ApiError) return exc.message
  if (exc instanceof Error) return exc.message
  return String(exc)
}

async function load(silent = false, preserveDrafts = false): Promise<void> {
  if (!silent) {
    loading.value = true
    loadError.value = ''
    feedback.value = null
    restartNeeded.value = false
  }
  try {
    const next = await api.channels()
    if (preserveDrafts) applyState(next, 'none')
    else applyState(next)
    await refreshLoginState()
  } catch (exc) {
    if (!silent) loadError.value = messageOf(exc)
  } finally {
    if (!silent) loading.value = false
  }
}

function touched(name: 'telegram' | 'weixin'): void {
  if (feedback.value?.channel === name) feedback.value = null
}

function reset(name: 'telegram' | 'weixin'): void {
  const base = name === 'telegram' ? tgBase.value : wxBase.value
  const draft = name === 'telegram' ? telegram : weixin
  draft.enabled = base.enabled
  draft.ids = idsText(base.ids)
  draft.reply = base.reply
  if (feedback.value?.channel === name) feedback.value = null
}

async function save(name: 'telegram' | 'weixin'): Promise<void> {
  if (saving.value) return
  saving.value = name
  feedback.value = null
  const patch: ChannelsPatch = {}
  if (name === 'telegram') {
    patch.telegram = {
      enabled: telegram.enabled,
      allowed_chat_ids: parseIds(telegram.ids),
      reply_mode: telegram.reply,
    }
  } else {
    patch.weixin = {
      enabled: weixin.enabled,
      allowed_user_ids: parseIds(weixin.ids),
      reply_mode: weixin.reply,
    }
  }
  try {
    applyState(await api.saveChannels(patch), name)
    restartNeeded.value = true
    feedback.value = { channel: name, kind: 'ok', text: t('channels.saved') }
  } catch (exc) {
    feedback.value = {
      channel: name,
      kind: 'fail',
      text: t('channels.saveFailed', { message: messageOf(exc) }),
    }
  } finally {
    saving.value = ''
  }
}

/** Prefer the path the API exposes; fall back to the documented data-dir layout. */
const channelsPath = computed(() => {
  const explicit = state.value?.gateway.config_path
  if (explicit) return explicit
  const dir = state.value?.weixin.data_dir?.trim() ?? ''
  const match = dir.match(/^(.*)\/channels\/weixin\/?$/)
  if (match) return `${match[1]}/channels.json`
  return '<data_dir>/channels.json'
})

const gatewayCommand = computed(() => {
  const command = state.value?.gateway.command || 'rui-channels'
  return `${command} --channels-config ${channelsPath.value}`
})

async function copy(text: string, key: 'gateway'): Promise<void> {
  copyFailed.value = false
  try {
    await navigator.clipboard.writeText(text)
    copied.value = key
    window.setTimeout(() => {
      if (copied.value === key) copied.value = ''
    }, 2000)
  } catch {
    copyFailed.value = true
  }
}

// — Telegram token (write-only, validated server-side) ————————
async function saveTelegramToken(): Promise<void> {
  const token = tokenDraft.value.trim()
  if (!token || tokenBusy.value) return
  tokenBusy.value = true
  tokenError.value = ''
  tokenNotice.value = ''
  try {
    const result = await api.setTelegramToken(token)
    tokenDraft.value = ''
    telegramUsername.value = result.username
    rememberTelegramUsername(result.username)
    await load(true, true)
    restartNeeded.value = true
    tokenNotice.value = result.username
      ? t('channels.telegram.tokenSavedAs', { username: result.username })
      : t('channels.telegram.tokenSaved')
  } catch (exc) {
    tokenError.value = t('channels.telegram.tokenFailed', { message: tokenErrorMessage(exc) })
  } finally {
    tokenBusy.value = false
  }
}

function tokenErrorMessage(exc: unknown): string {
  const message = messageOf(exc).replace(/^\d{3}\s+/, '')
  if (/Telegram rejected/i.test(message)) return t('channels.telegram.tokenRejected')
  return message
}

async function clearTelegramToken(): Promise<void> {
  if (tokenBusy.value) return
  if (!window.confirm(t('channels.telegram.clearConfirm'))) return
  tokenBusy.value = true
  tokenError.value = ''
  tokenNotice.value = ''
  try {
    await api.setTelegramToken('')
    telegramUsername.value = ''
    rememberTelegramUsername('')
    await load(true, true)
    restartNeeded.value = true
    tokenNotice.value = t('channels.telegram.tokenCleared')
  } catch (exc) {
    tokenError.value = t('channels.telegram.tokenFailed', { message: tokenErrorMessage(exc) })
  } finally {
    tokenBusy.value = false
  }
}

// — WeChat QR login (POST starts the flow, GET polls every 2 s) ——
function stopLoginPolling(): void {
  if (loginTimer !== undefined) {
    window.clearInterval(loginTimer)
    loginTimer = undefined
  }
}

function startLoginPolling(): void {
  stopLoginPolling()
  loginTimer = window.setInterval(() => {
    void pollWeixinLogin()
  }, 2000)
}

async function refreshLoginState(): Promise<void> {
  try {
    const current = await api.weixinLogin()
    login.value = current
    if (current.status === 'starting' || current.status === 'waiting') startLoginPolling()
  } catch {
    // QR is optional; a failed GET must not break the whole channels page.
  }
}

async function beginWeixinLogin(): Promise<void> {
  if (loginBusy.value || loginActive.value) return
  loginBusy.value = true
  try {
    login.value = await api.startWeixinLogin()
    if (loginActive.value) startLoginPolling()
  } catch (exc) {
    login.value = {
      status: 'error',
      qr_url: '',
      qr_svg: '',
      error: messageOf(exc),
      account_id: '',
    }
  } finally {
    loginBusy.value = false
  }
}

async function pollWeixinLogin(): Promise<void> {
  if (!loginActive.value) {
    stopLoginPolling()
    return
  }
  try {
    const next = await api.weixinLogin()
    login.value = next
    if (next.status === 'confirmed') {
      stopLoginPolling()
      await load(true, true)
      restartNeeded.value = true
    } else if (next.status === 'error' || next.status === 'idle') {
      stopLoginPolling()
    }
  } catch {
    // transient poll failure: keep waiting, the next tick retries
  }
}

// — managed gateway process ————————————————————————————————
async function gatewayAction(action: 'start' | 'stop' | 'restart'): Promise<void> {
  if (gatewayBusy.value) return
  if (action === 'restart' && !window.confirm(t('channels.gateway.confirmRestart'))) return
  gatewayBusy.value = action
  gatewayError.value = ''
  gatewayNotice.value = ''
  try {
    let result: ChannelGatewayStatus
    if (action === 'start') result = await api.startChannelGateway()
    else if (action === 'stop') result = await api.stopChannelGateway()
    else result = await api.restartChannelGateway()
    if (state.value) {
      state.value = { ...state.value, gateway: { ...state.value.gateway, status: result } }
    }
    if (action !== 'stop' && result.running) restartNeeded.value = false
    if (result.last_error) {
      gatewayError.value = t('channels.gateway.actionFailed', { message: result.last_error })
    } else {
      gatewayNotice.value =
        action === 'start'
          ? t('channels.gateway.started')
          : action === 'stop'
            ? t('channels.gateway.stopped')
            : t('channels.gateway.restarted')
    }
  } catch (exc) {
    gatewayError.value = t('channels.gateway.actionFailed', { message: messageOf(exc) })
  } finally {
    gatewayBusy.value = ''
  }
}

onBeforeRouteLeave(() => {
  if (!dirty.value) return true
  return window.confirm(t('channels.unsavedLeave'))
})

onBeforeUnmount(stopLoginPolling)

onMounted(() => {
  void load()
})
</script>

<template>
  <header class="page-head">
    <div>
      <div class="kicker">{{ t('channels.kicker') }}</div>
      <h1>{{ t('channels.title') }}</h1>
      <p class="lede">{{ t('channels.lede') }}</p>
      <p class="ch-intro">{{ t('channels.introNote') }}</p>
    </div>
    <div v-if="state" class="page-head-side">
      <span class="badge" :class="gatewayStatusTone">{{ gatewayStatusLabel }}</span>
      <span class="badge" :class="enabledCount ? 'badge-ok' : 'badge-muted'">
        {{ t('channels.enabledCount', { n: enabledCount }) }}
      </span>
    </div>
  </header>

  <p v-if="loading" class="empty">{{ t('channels.loading') }}</p>

  <div v-if="loadError" class="banner banner-fail" role="alert">
    <AppIcon name="alert" :size="18" class="banner-icon" />
    <div>
      <p>{{ t('channels.loadError', { message: loadError }) }}</p>
      <button type="button" class="btn btn-sm" style="margin-top: 10px" @click="load()">{{ t('channels.retry') }}</button>
    </div>
  </div>

  <template v-if="state && !loading && !loadError">
    <div class="channels-grid">
      <!-- Telegram -->
      <section class="surface channel-card" :class="{ on: telegram.enabled }">
        <header class="ch-head">
          <div class="ch-title">
            <span class="ch-mark"><AppIcon name="chat" :size="18" /></span>
            <div>
              <div class="ch-kicker">{{ t('channels.telegram.kicker') }}</div>
              <h2>{{ t('channels.telegram.title') }}</h2>
            </div>
          </div>
          <div class="ch-chips">
            <span class="badge" :class="telegram.enabled ? 'badge-ok' : 'badge-muted'">
              {{ telegram.enabled ? t('channels.enabled') : t('channels.disabled') }}
            </span>
            <span class="badge" :class="state.telegram.token_configured ? 'badge-ok' : 'badge-warn'">
              {{ state.telegram.token_configured ? t('channels.telegram.tokenConfigured') : t('channels.telegram.tokenMissing') }}
            </span>
          </div>
        </header>
        <p class="ch-desc">{{ t('channels.telegram.desc') }}</p>

        <div class="ch-body">
          <div class="ch-field inline">
            <span class="ch-label">{{ t('channels.enable') }}</span>
            <label class="switch">
              <input v-model="telegram.enabled" type="checkbox" :disabled="saving === 'telegram'" @change="touched('telegram')" />
              <span class="track" />
              <span class="switch-text">{{ telegram.enabled ? t('channels.enabled') : t('channels.disabled') }}</span>
            </label>
          </div>

          <div class="ch-field">
            <div class="ch-label-row">
              <label class="ch-label" for="tg-ids">{{ t('channels.telegram.allowlist') }}</label>
              <span class="field-hint">{{ t('channels.allowlistCount', { n: tgIdCount }) }}</span>
            </div>
            <textarea
              id="tg-ids"
              v-model="telegram.ids"
              rows="3"
              spellcheck="false"
              :placeholder="t('channels.allowlistPlaceholder')"
              :disabled="saving === 'telegram'"
              @input="touched('telegram')"
            />
            <p class="field-hint">{{ t('channels.allowlistHint') }}</p>
          </div>

          <div class="ch-field">
            <label class="ch-label" for="tg-reply">{{ t('channels.replyMode') }}</label>
            <select id="tg-reply" v-model="telegram.reply" :disabled="saving === 'telegram'">
              <option v-for="mode in REPLY_MODES" :key="mode" :value="mode">{{ replyLabel(mode) }}</option>
            </select>
          </div>

          <div class="ch-token-editor">
            <div class="ch-label-row">
              <label class="ch-label" for="tg-token">{{ t('channels.telegram.tokenInput') }}</label>
              <span v-if="telegramUsername && state.telegram.token_configured" class="field-hint">
                {{ t('channels.telegram.botUsername', { username: telegramUsername }) }}
              </span>
            </div>
            <div class="token-row">
              <input
                id="tg-token"
                v-model="tokenDraft"
                type="password"
                autocomplete="off"
                spellcheck="false"
                :placeholder="t('channels.telegram.tokenPlaceholder')"
                :disabled="tokenBusy"
                @keyup.enter="saveTelegramToken"
              />
              <button
                type="button"
                class="btn btn-primary"
                :disabled="tokenBusy || !tokenDraft.trim()"
                @click="saveTelegramToken"
              >
                {{ tokenBusy ? t('channels.telegram.verifying') : t('channels.telegram.saveAndVerify') }}
              </button>
            </div>
            <p v-if="!state.telegram.token_configured" class="field-hint">
              {{ t('channels.telegram.tokenMissingHint') }}
            </p>
            <div v-if="state.telegram.token_configured" class="token-actions">
              <button
                type="button"
                class="btn btn-ghost btn-sm"
                :disabled="tokenBusy"
                @click="clearTelegramToken"
              >
                {{ t('channels.telegram.clearToken') }}
              </button>
            </div>
            <p v-if="tokenError" class="note fail" role="alert">{{ tokenError }}</p>
            <p v-if="tokenNotice" class="note ok" role="status">{{ tokenNotice }}</p>
          </div>
          <p class="ch-token-note">{{ t('channels.telegram.tokenNote') }}</p>
        </div>

        <footer class="ch-foot">
          <span v-if="tgDiff" class="badge badge-warn">{{ t('channels.dirty', { n: tgDiff }) }}</span>
          <p v-if="feedback?.channel === 'telegram'" class="note ch-feedback" :class="feedback.kind">
            {{ feedback.text }}
          </p>
          <div class="actions ch-actions">
            <button type="button" class="btn" :disabled="saving !== '' || !tgDiff" @click="reset('telegram')">
              {{ t('channels.reset') }}
            </button>
            <button type="button" class="btn btn-primary" :disabled="saving !== '' || !tgDiff" @click="save('telegram')">
              {{ saving === 'telegram' ? t('channels.saving') : t('channels.save') }}
            </button>
          </div>
        </footer>
      </section>

      <!-- WeChat iLink / ClawBot -->
      <section class="surface channel-card" :class="{ on: weixin.enabled }">
        <header class="ch-head">
          <div class="ch-title">
            <span class="ch-mark"><AppIcon name="link" :size="18" /></span>
            <div>
              <div class="ch-kicker">{{ t('channels.weixin.kicker') }}</div>
              <h2>{{ t('channels.weixin.title') }}</h2>
            </div>
          </div>
          <div class="ch-chips">
            <span class="badge" :class="weixin.enabled ? 'badge-ok' : 'badge-muted'">
              {{ weixin.enabled ? t('channels.enabled') : t('channels.disabled') }}
            </span>
            <span class="badge" :class="state.weixin.logged_in ? 'badge-ok' : 'badge-warn'">
              {{ state.weixin.logged_in ? t('channels.weixin.loggedIn') : t('channels.weixin.notLoggedIn') }}
            </span>
          </div>
        </header>
        <p class="ch-desc">{{ t('channels.weixin.desc') }}</p>

        <div class="ch-body">
          <div class="ch-field inline">
            <span class="ch-label">{{ t('channels.enable') }}</span>
            <label class="switch">
              <input v-model="weixin.enabled" type="checkbox" :disabled="saving === 'weixin'" @change="touched('weixin')" />
              <span class="track" />
              <span class="switch-text">{{ weixin.enabled ? t('channels.enabled') : t('channels.disabled') }}</span>
            </label>
          </div>

          <div class="ch-field">
            <span class="ch-label">{{ t('channels.weixin.loginStatus') }}</span>
            <div v-if="state.weixin.logged_in" class="ch-meta">
              <div>
                <span class="ch-k">{{ t('channels.weixin.account') }}</span>
                <code class="mono">{{ state.weixin.account_id || '—' }}</code>
              </div>
              <div>
                <span class="ch-k">{{ t('channels.weixin.user') }}</span>
                <code class="mono">{{ state.weixin.user_id || '—' }}</code>
              </div>
            </div>
            <p v-else class="field-hint">{{ t('channels.weixin.noAccount') }}</p>
          </div>

          <div class="ch-help ch-login">
            <div class="ch-label-row">
              <h3>{{ t('channels.weixin.qrTitle') }}</h3>
              <span class="badge" :class="loginTone">{{ loginLabel }}</span>
            </div>
            <p class="field-hint">{{ t('channels.weixin.loginHint') }}</p>

            <div v-if="qrSrc" class="qr-box">
              <img :src="qrSrc" :alt="t('channels.weixin.qrAlt')" referrerpolicy="no-referrer" />
              <a v-if="qrUrl" :href="qrUrl" target="_blank" rel="noopener noreferrer">
                {{ t('channels.weixin.qrFallback') }}
              </a>
            </div>
            <p v-else-if="qrUrl" class="field-hint">
              <a :href="qrUrl" target="_blank" rel="noopener noreferrer">{{ t('channels.weixin.qrFallback') }}</a>
            </p>

            <p v-if="login?.status === 'starting'" class="field-hint">{{ t('channels.weixin.loginStarting') }}</p>
            <p v-else-if="login?.status === 'waiting'" class="field-hint">{{ t('channels.weixin.loginWaiting') }}</p>
            <p v-else-if="login?.status === 'confirmed'" class="note ok" role="status">
              {{ t('channels.weixin.loginConfirmed') }}
            </p>
            <p v-else-if="login?.status === 'error'" class="note fail" role="alert">
              {{ t('channels.weixin.loginError', { message: login.error || t('common.error') }) }}
            </p>

            <div class="actions login-actions">
              <button
                type="button"
                class="btn btn-primary"
                :disabled="loginBusy || loginActive"
                @click="beginWeixinLogin"
              >
                {{ loginBusy || login?.status === 'starting'
                  ? t('channels.weixin.loginStarting')
                  : loginActive
                    ? t('channels.weixin.loginWaiting')
                    : login?.status === 'confirmed'
                      ? t('channels.weixin.relogin')
                      : t('channels.weixin.loginButton') }}
              </button>
            </div>

            <p v-if="loginAccount" class="field-hint">
              {{ t('channels.weixin.boundAccount', { account: loginAccount }) }}
            </p>
            <p class="field-hint">
              {{ t('channels.weixin.dataDirShort') }}: <code class="mono">{{ state.weixin.data_dir }}</code>
            </p>
          </div>

          <div class="ch-field">
            <div class="ch-label-row">
              <label class="ch-label" for="wx-ids">{{ t('channels.weixin.allowlist') }}</label>
              <span class="field-hint">{{ t('channels.allowlistCount', { n: wxIdCount }) }}</span>
            </div>
            <textarea
              id="wx-ids"
              v-model="weixin.ids"
              rows="3"
              spellcheck="false"
              :placeholder="t('channels.allowlistPlaceholder')"
              :disabled="saving === 'weixin'"
              @input="touched('weixin')"
            />
            <p class="field-hint">{{ t('channels.allowlistHint') }}</p>
          </div>

          <div class="ch-field">
            <label class="ch-label" for="wx-reply">{{ t('channels.replyMode') }}</label>
            <select id="wx-reply" v-model="weixin.reply" :disabled="saving === 'weixin'">
              <option v-for="mode in REPLY_MODES" :key="mode" :value="mode">{{ replyLabel(mode) }}</option>
            </select>
          </div>
        </div>

        <footer class="ch-foot">
          <span v-if="wxDiff" class="badge badge-warn">{{ t('channels.dirty', { n: wxDiff }) }}</span>
          <p v-if="feedback?.channel === 'weixin'" class="note ch-feedback" :class="feedback.kind">
            {{ feedback.text }}
          </p>
          <div class="actions ch-actions">
            <button type="button" class="btn" :disabled="saving !== '' || !wxDiff" @click="reset('weixin')">
              {{ t('channels.reset') }}
            </button>
            <button type="button" class="btn btn-primary" :disabled="saving !== '' || !wxDiff" @click="save('weixin')">
              {{ saving === 'weixin' ? t('channels.saving') : t('channels.save') }}
            </button>
          </div>
        </footer>
      </section>
    </div>

    <div v-if="restartNeeded" class="banner banner-warn restart-banner" role="status">
      <AppIcon name="refresh" :size="18" class="banner-icon" />
      <span>{{ t('channels.restartNeeded') }}</span>
      <button
        type="button"
        class="btn btn-sm btn-primary"
        :disabled="gatewayBusy !== '' || !gatewayStatus || !gatewayStatus.enabled"
        @click="gatewayAction('restart')"
      >
        {{ gatewayBusy === 'restart' ? t('channels.gateway.restarting') : t('channels.gateway.restart') }}
      </button>
    </div>

    <section class="surface gateway-card">
      <div class="block-head">
        <h2>{{ t('channels.gateway.title') }}</h2>
        <div class="ch-chips">
          <span class="badge" :class="gatewayStatusTone">{{ gatewayStatusLabel }}</span>
          <span class="badge badge-muted">{{ t('channels.gateway.managed') }}</span>
        </div>
      </div>
      <p class="ch-desc">{{ t('channels.gateway.desc') }}</p>

      <div class="gw-status">
        <p v-if="!gatewayStatus" class="ch-token-note">{{ t('channels.gateway.unavailable') }}</p>
        <template v-else>
          <div class="gw-metrics">
            <div>
              <span class="ch-k">{{ t('channels.gateway.pid') }}</span>
              <code class="mono">{{ gatewayStatus.pid ?? '—' }}</code>
            </div>
            <div>
              <span class="ch-k">{{ t('channels.gateway.uptime') }}</span>
              <code class="mono">{{ gatewayStatus.running ? formatUptime(gatewayStatus.uptime_s) : '—' }}</code>
            </div>
            <div class="wide">
              <span class="ch-k">{{ t('channels.gateway.logPath') }}</span>
              <code class="mono">{{ gatewayStatus.log_path || '—' }}</code>
            </div>
          </div>
          <p v-if="!gatewayStatus.enabled" class="field-hint">{{ t('channels.gateway.disabledHint') }}</p>
          <p v-if="gatewayStatus.last_error" class="note fail">
            {{ t('channels.gateway.lastError') }}: {{ gatewayStatus.last_error }}
          </p>
        </template>
      </div>

      <div class="actions gateway-actions">
        <button
          type="button"
          class="btn"
          :disabled="gatewayBusy !== '' || !gatewayStatus || gatewayStatus.running || !gatewayStatus.enabled"
          @click="gatewayAction('start')"
        >
          <AppIcon name="play" :size="13" />
          {{ gatewayBusy === 'start' ? t('channels.gateway.starting') : t('channels.gateway.start') }}
        </button>
        <button
          type="button"
          class="btn"
          :disabled="gatewayBusy !== '' || !gatewayStatus?.running"
          @click="gatewayAction('stop')"
        >
          <AppIcon name="stop" :size="13" />
          {{ gatewayBusy === 'stop' ? t('channels.gateway.stopping') : t('channels.gateway.stop') }}
        </button>
        <button
          type="button"
          class="btn btn-primary"
          :disabled="gatewayBusy !== '' || !gatewayStatus || !gatewayStatus.enabled"
          @click="gatewayAction('restart')"
        >
          <AppIcon name="refresh" :size="13" />
          {{ gatewayBusy === 'restart' ? t('channels.gateway.restarting') : t('channels.gateway.restart') }}
        </button>
      </div>

      <p v-if="gatewayNotice" class="note ok" role="status">{{ gatewayNotice }}</p>
      <p v-if="gatewayError" class="note fail" role="alert">{{ gatewayError }}</p>

      <div class="ch-field gw-command">
        <div class="ch-label-row">
          <span class="ch-label">{{ t('channels.gateway.command') }}</span>
          <span v-if="copied === 'gateway'" class="field-hint">{{ t('channels.gateway.copied') }}</span>
        </div>
        <div class="cmd-row">
          <code class="mono">{{ gatewayCommand }}</code>
          <button type="button" class="btn btn-sm" @click="copy(gatewayCommand, 'gateway')">
            {{ copied === 'gateway' ? t('channels.gateway.copied') : t('channels.gateway.copy') }}
          </button>
        </div>
        <p v-if="copyFailed" class="note fail">{{ t('channels.gateway.copyFailed') }}</p>
        <p class="field-hint">
          {{ t('channels.gateway.configPath') }}: <code class="mono">{{ channelsPath }}</code>
        </p>
      </div>

      <p class="ch-restart">{{ t('channels.gateway.restartNote') }}</p>
    </section>
  </template>
</template>

<style scoped>
.ch-intro {
  margin-top: var(--s2);
  color: var(--muted);
  font-size: 12px;
  line-height: 1.55;
}

.channels-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--s4);
  align-items: stretch;
}

.channel-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: var(--s5);
  border-top: 2px solid var(--line-strong);
}

.channel-card.on {
  border-top-color: var(--accent);
}

.ch-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s3);
  flex-wrap: wrap;
}

.ch-title {
  display: flex;
  align-items: center;
  gap: var(--s3);
  min-width: 0;
}

.ch-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: var(--r-sm);
  background: var(--accent-soft);
  color: var(--accent);
  flex: none;
}

.ch-title h2 {
  font-family: var(--font-display);
  font-size: 17px;
  font-weight: 500;
  margin: 1px 0 0;
}

.ch-kicker {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
}

.ch-chips {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.ch-desc {
  margin-top: var(--s3);
  color: var(--ink-2);
  font-size: 12px;
  line-height: 1.55;
}

.ch-body {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
  margin-top: var(--s4);
}

.ch-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.ch-field.inline {
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  gap: var(--s3);
  min-height: 36px;
}

.ch-label {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--muted);
}

.ch-label-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--s2);
  flex-wrap: wrap;
}

.ch-field textarea,
.ch-field select {
  width: 100%;
}

.ch-field textarea {
  min-height: 72px;
  resize: vertical;
}

.ch-meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--s2);
}

.ch-meta > div {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.ch-meta code {
  font-size: 11px;
  color: var(--ink-2);
  overflow-wrap: anywhere;
}

.ch-k {
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
}

.ch-token-note {
  padding: 9px 11px;
  border-left: 2px solid var(--line-strong);
  background: var(--surface-2);
  color: var(--ink-2);
  font-size: 11px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}

.ch-restart {
  padding: 9px 11px;
  border-left: 2px solid var(--warn);
  background: var(--warn-soft);
  color: var(--ink-2);
  font-size: 11px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}

.ch-help {
  padding: var(--s3);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-2);
}

.ch-help h3 {
  font-family: var(--font-ui);
  font-size: 12px;
  font-weight: 600;
}

.ch-help ol {
  margin: var(--s2) 0 0;
  padding-left: 18px;
  color: var(--ink-2);
  font-size: 11.5px;
  line-height: 1.6;
}

.ch-help li + li {
  margin-top: 4px;
}

.ch-help .field-hint {
  margin-top: var(--s2);
}

.ch-token-editor {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.token-row {
  display: flex;
  align-items: stretch;
  gap: var(--s2);
  margin-top: 3px;
}

.token-row input {
  flex: 1;
  min-width: 0;
  min-height: 36px;
  font-size: 12.5px;
}

.token-row .btn {
  flex: none;
}

.token-actions {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.token-actions .field-hint {
  margin: 0;
}

.qr-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--s2);
  margin-top: var(--s2);
  padding: var(--s3);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface);
}

.qr-box img {
  display: block;
  width: 200px;
  max-width: 100%;
  height: auto;
}

.qr-box a,
.ch-help a {
  color: var(--accent);
  font-size: 11px;
  overflow-wrap: anywhere;
}

.login-actions {
  justify-content: flex-start;
  margin-top: var(--s2);
}

.gw-status {
  margin-top: var(--s4);
  padding: var(--s3);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-2);
}

.gw-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--s2) var(--s4);
}

.gw-metrics > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.gw-metrics .wide {
  grid-column: 1 / -1;
}

.gw-metrics code {
  font-size: 11px;
  color: var(--ink-2);
  overflow-wrap: anywhere;
}

.gw-status .note,
.gw-status .field-hint {
  margin-top: var(--s2);
}

.gateway-actions {
  margin-top: var(--s4);
}

.restart-banner {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  margin-top: var(--s4);
}

.restart-banner .btn {
  margin-left: auto;
}

.gateway-card .gw-command {
  margin-top: var(--s4);
}

.cmd-row {
  display: flex;
  align-items: center;
  gap: var(--s2);
  margin-top: 5px;
  padding: 7px 9px;
  border: 1px solid var(--line);
  border-radius: var(--r-xs);
  background: var(--surface);
  min-width: 0;
}

.cmd-row code {
  flex: 1;
  min-width: 0;
  font-size: 11px;
  color: var(--ink-2);
  overflow-wrap: anywhere;
}

.cmd-row .btn {
  flex: none;
}

.ch-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s3);
  flex-wrap: wrap;
  margin-top: auto;
  padding-top: var(--s4);
}

.ch-feedback {
  flex: 1 1 100%;
  margin: 0;
}

.ch-actions {
  margin-left: auto;
}

.gateway-card {
  margin-top: var(--s4);
  padding: var(--s5);
}

.gateway-card .ch-field {
  margin-top: var(--s4);
}

@media (max-width: 900px) {
  .channels-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 720px) {
  .channel-card,
  .gateway-card {
    padding: var(--s4);
  }

  .ch-field.inline {
    min-height: 44px;
  }

  .ch-field textarea {
    min-height: 96px;
  }

  .ch-field textarea,
  .ch-field select {
    min-height: 44px;
    font-size: 14px;
  }

  .ch-field .switch {
    min-height: 44px;
  }

  .ch-actions {
    width: 100%;
    margin-left: 0;
  }

  .ch-actions .btn {
    flex: 1;
    min-height: 44px;
  }

  .token-row {
    flex-direction: column;
  }

  .token-row input,
  .token-row .btn {
    width: 100%;
    min-height: 44px;
  }

  .token-actions .btn {
    min-height: 44px;
  }

  .gw-metrics {
    grid-template-columns: minmax(0, 1fr);
  }

  .gateway-actions .btn {
    flex: 1 1 100%;
    min-height: 44px;
  }

  .restart-banner .btn {
    width: 100%;
    margin-left: 0;
    min-height: 44px;
  }

  .qr-box img {
    width: 180px;
  }

  .cmd-row {
    flex-wrap: wrap;
  }

  .cmd-row .btn {
    width: 100%;
    min-height: 44px;
  }
}
</style>
