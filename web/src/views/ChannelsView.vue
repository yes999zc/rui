<script setup lang="ts">
/**
 * Channels page (Telegram + WeChat iLink/ClawBot) — maintenance, hand-written.
 *
 * GET/PUT /api/v1/channels is the only data source: Telegram is a bot token +
 * allowlisted chat ids, WeChat is a logged-in iLink account + allowlisted user
 * ids. The gateway itself is a separate process (`house-ear-channels`), so the
 * page saves `channels.json` and tells the operator to restart the gateway; it
 * never claims the change is live.
 *
 * Secrets stay out of the UI entirely: the API only exposes `token_configured`,
 * never a token value.
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import {
  api,
  ApiError,
  type ChannelsPatch,
  type ChannelsState,
  type ChannelReplyMode,
} from '../api'
import { t, type MsgKey } from '../i18n'

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
const copied = ref<'' | 'gateway' | 'login'>('')
const copyFailed = ref(false)

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

/** After a save, refresh only the saved channel so the other draft is preserved. */
function applyState(next: ChannelsState, only?: 'telegram' | 'weixin'): void {
  state.value = next
  if (only !== 'weixin') syncTelegram()
  if (only !== 'telegram') syncWeixin()
}

function messageOf(exc: unknown): string {
  if (exc instanceof ApiError) return exc.message
  if (exc instanceof Error) return exc.message
  return String(exc)
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  feedback.value = null
  try {
    applyState(await api.channels())
  } catch (exc) {
    loadError.value = messageOf(exc)
  } finally {
    loading.value = false
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

/** The channels.json sits next to the weixin data dir, e.g. <data_dir>/channels.json. */
const channelsPath = computed(() => {
  const dir = state.value?.weixin.data_dir?.trim() ?? ''
  const match = dir.match(/^(.*)\/channels\/weixin\/?$/)
  if (match) return `${match[1]}/channels.json`
  return state.value?.gateway.config_path || '<data_dir>/channels.json'
})

const gatewayCommand = computed(() => {
  const command = state.value?.gateway.command || 'house-ear-channels'
  return `${command} --channels-config ${channelsPath.value}`
})

async function copy(text: string, key: 'gateway' | 'login'): Promise<void> {
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

onBeforeRouteLeave(() => {
  if (!dirty.value) return true
  return window.confirm(t('channels.unsavedLeave'))
})

onMounted(load)
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
      <button type="button" class="btn btn-sm" style="margin-top: 10px" @click="load">{{ t('channels.retry') }}</button>
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

          <div class="ch-help">
            <h3>{{ t('channels.weixin.qrTitle') }}</h3>
            <ol>
              <li>
                {{ t('channels.weixin.qrStep1') }}
                <div class="cmd-row">
                  <code class="mono">{{ t('channels.weixin.loginCommand') }}</code>
                  <button type="button" class="btn btn-sm" @click="copy(t('channels.weixin.loginCommand'), 'login')">
                    {{ copied === 'login' ? t('channels.gateway.copied') : t('channels.gateway.copy') }}
                  </button>
                </div>
              </li>
              <li>{{ t('channels.weixin.qrStep2') }}</li>
              <li>{{ t('channels.weixin.qrStep3') }}</li>
            </ol>
            <p class="field-hint">
              {{ t('channels.weixin.dataDir') }}: <code class="mono">{{ state.weixin.data_dir }}</code>
            </p>
            <p v-if="copyFailed" class="note fail">{{ t('channels.gateway.copyFailed') }}</p>
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

    <!-- Gateway process -->
    <section class="surface gateway-card">
      <div class="block-head">
        <h2>{{ t('channels.gateway.title') }}</h2>
        <span class="badge badge-muted">{{ t('channels.gateway.independent') }}</span>
      </div>
      <p class="ch-desc">{{ t('channels.gateway.desc') }}</p>

      <div class="ch-field">
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

      <p class="ch-restart">{{ t('channels.gateway.restart') }}</p>
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

  .cmd-row {
    flex-wrap: wrap;
  }

  .cmd-row .btn {
    width: 100%;
    min-height: 44px;
  }
}
</style>
