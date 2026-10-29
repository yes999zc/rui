<script setup lang="ts">
/**
 * `/v2/channels` (D8) — Telegram / WeChat cards + the managed channel gateway.
 *
 * This restores the capability the v1 channels page had (B1 deleted the legacy
 * view with the rest of the panel system) on v2 components and theme. The data
 * source is the stable v1 non-device surface (C-002 §1.2):
 *
 *   GET  /api/v1/channels                 -> both channels + gateway status
 *   PUT  /api/v1/channels                 -> only the channel being saved
 *   POST /api/v1/channels/gateway/{action} -> start / stop / restart
 *
 * Token values are never edited nor returned here: Telegram only exposes
 * `token_configured`, and WeChat only the saved login account. Saving writes
 * `channels.json` (the gateway reads it at startup), so the page re-reads with a
 * GET after every PUT and reports whether the read-back matches. The channel
 * config does not hot-reload: a restart of the gateway is what applies it.
 */
import { computed, onMounted, reactive, ref } from 'vue'
import AppIcon from '../../components/AppIcon.vue'
import StateBlock from '../components/StateBlock.vue'
import {
  api,
  type ChannelGatewayAction,
  type ChannelGatewayStatus,
  type ChannelReplyMode,
  type ChannelsPatch,
  type ChannelsState,
} from '../api'
import { describeError, type ErrorCopy } from '../errors'
import { t, type MsgKey } from '../i18n'
import { useResource } from '../useResource'

type ChannelName = 'telegram' | 'weixin'

interface ChannelDraft {
  enabled: boolean
  replyMode: ChannelReplyMode
  ids: string[]
}

interface Feedback {
  kind: 'ok' | 'warn' | 'fail'
  text: string
}

interface ChannelCard {
  name: ChannelName
  testid: string
  titleKey: MsgKey
  descKey: MsgKey
  idsLabelKey: MsgKey
}

const REPLY_MODES: ChannelReplyMode[] = ['text', 'voice', 'both']
const REPLY_LABEL: Record<ChannelReplyMode, MsgKey> = {
  text: 'v2.channels.reply.text',
  voice: 'v2.channels.reply.voice',
  both: 'v2.channels.reply.both',
}

const cards: ChannelCard[] = [
  {
    name: 'telegram',
    testid: 'channels-telegram',
    titleKey: 'v2.channels.telegram.title',
    descKey: 'v2.channels.telegram.desc',
    idsLabelKey: 'v2.channels.ids.telegram',
  },
  {
    name: 'weixin',
    testid: 'channels-weixin',
    titleKey: 'v2.channels.weixin.title',
    descKey: 'v2.channels.weixin.desc',
    idsLabelKey: 'v2.channels.ids.weixin',
  },
]

const resource = useResource(() => api.channels())
const { data: state, error: loadError, loading } = resource

const drafts = reactive<Record<ChannelName, ChannelDraft>>({
  telegram: { enabled: false, replyMode: 'text', ids: [] },
  weixin: { enabled: false, replyMode: 'text', ids: [] },
})
const saving = ref<'' | ChannelName>('')
const feedback = ref<Record<ChannelName, Feedback | null>>({ telegram: null, weixin: null })
const gatewayBusy = ref<'' | ChannelGatewayAction>('')
const gatewayNotice = ref('')
const gatewayError = ref<ErrorCopy | null>(null)

const loadErrorCopy = computed(() => (loadError.value ? describeError(loadError.value) : null))
const gatewayStatus = computed<ChannelGatewayStatus | null>(
  () => state.value?.gateway.status ?? null,
)
const gatewayState = computed<'running' | 'stopped' | 'unknown'>(() => {
  const status = gatewayStatus.value
  if (!status) return 'unknown'
  return status.running ? 'running' : 'stopped'
})
const gatewayStateLabel = computed(() => {
  if (gatewayState.value === 'running') return t('v2.channels.gateway.running')
  if (gatewayState.value === 'stopped') return t('v2.channels.gateway.stopped')
  return t('v2.channels.gateway.unknown')
})
const gatewayStateTone = computed(() => {
  if (gatewayState.value === 'running') return 'badge-ok'
  if (gatewayState.value === 'stopped') return 'badge-muted'
  return 'badge-warn'
})
const gatewayConfigPath = computed(() => state.value?.gateway.config_path ?? '')
const gatewayLastError = computed(() => gatewayStatus.value?.last_error || '')

function idsOf(next: ChannelsState, name: ChannelName): string[] {
  return name === 'telegram' ? next.telegram.allowed_chat_ids : next.weixin.allowed_user_ids
}

function syncDraft(name: ChannelName, next: ChannelsState | null = state.value): void {
  if (!next) return
  const channel = name === 'telegram' ? next.telegram : next.weixin
  drafts[name].enabled = channel.enabled
  drafts[name].replyMode = channel.reply_mode
  drafts[name].ids = [...idsOf(next, name)]
}

function syncAll(next: ChannelsState | null = state.value): void {
  syncDraft('telegram', next)
  syncDraft('weixin', next)
}

async function load(): Promise<void> {
  await resource.load()
  if (state.value) syncAll()
}

/** Trim surrounding whitespace; blank rows are allowed in the draft, not in the payload. */
function cleanIds(name: ChannelName): string[] {
  return drafts[name].ids.map((id) => id.trim()).filter(Boolean)
}

function addId(name: ChannelName): void {
  drafts[name].ids.push('')
}

function removeId(name: ChannelName, index: number): void {
  drafts[name].ids.splice(index, 1)
}

function setId(name: ChannelName, index: number, event: Event): void {
  drafts[name].ids[index] = (event.target as HTMLInputElement).value
}

function payloadFor(name: ChannelName): ChannelsPatch {
  if (name === 'telegram') {
    return {
      telegram: {
        enabled: drafts.telegram.enabled,
        allowed_chat_ids: cleanIds('telegram'),
        reply_mode: drafts.telegram.replyMode,
      },
    }
  }
  return {
    weixin: {
      enabled: drafts.weixin.enabled,
      allowed_user_ids: cleanIds('weixin'),
      reply_mode: drafts.weixin.replyMode,
    },
  }
}

function matchesExpected(
  next: ChannelsState,
  name: ChannelName,
  expected: { enabled: boolean; replyMode: ChannelReplyMode; ids: string[] },
): boolean {
  const channel = name === 'telegram' ? next.telegram : next.weixin
  const ids = idsOf(next, name)
  return (
    channel.enabled === expected.enabled &&
    channel.reply_mode === expected.replyMode &&
    ids.length === expected.ids.length &&
    ids.every((id, index) => id === expected.ids[index])
  )
}

type ErrorScope = 'gateway' | 'service'

/**
 * 503 / 422 are the two failure modes this page can explain precisely.
 *
 * The 503 copy depends on what failed: a gateway start/stop/restart means the
 * channel supervisor is not ready, while loading or saving channels is a plain
 * service outage. 422 always carries the backend's validation detail; network
 * and unknown failures keep the shared translator's fallback.
 */
function errorText(copy: ErrorCopy, scope: ErrorScope = 'service'): string {
  if (copy.status === 503) {
    return scope === 'gateway'
      ? t('v2.channels.gateway.error503')
      : t('v2.channels.serviceUnavailable')
  }
  if (copy.status === 422) {
    return t('v2.channels.error422', { detail: copy.detail || copy.title })
  }
  return copy.detail ? `${copy.title}: ${copy.detail}` : copy.title
}

function feedbackFor(name: ChannelName): Feedback | null {
  return feedback.value[name]
}

function feedbackClass(name: ChannelName): string {
  const item = feedback.value[name]
  return item ? `is-${item.kind}` : ''
}

async function save(name: ChannelName): Promise<void> {
  if (saving.value) return
  const body = payloadFor(name)
  const expected = {
    enabled: drafts[name].enabled,
    replyMode: drafts[name].replyMode,
    ids: cleanIds(name),
  }
  saving.value = name
  feedback.value[name] = null
  try {
    await api.saveChannels(body)
    // PUT's echo is not proof: re-read and compare (the server merges into the
    // stored state and strips blank ids, so only the read-back is authoritative).
    await resource.load()
    const next = state.value
    if (loadError.value) {
      const copy = describeError(loadError.value)
      feedback.value[name] = {
        kind: 'warn',
        text: t('v2.channels.savedReadbackFailed', { message: errorText(copy, 'service') }),
      }
      return
    }
    if (!next) return
    syncDraft(name, next)
    const ok = matchesExpected(next, name, expected)
    feedback.value[name] = {
      kind: ok ? 'ok' : 'warn',
      text: ok ? t('v2.channels.savedVerified') : t('v2.channels.savedMismatch'),
    }
  } catch (err) {
    // The page-level "保存失败" title plus the context-specific reason (503 is a
    // service outage here, never "gateway not ready").
    const copy = describeError(err)
    feedback.value[name] = {
      kind: 'fail',
      text: `${t('v2.channels.saveFailed')}: ${errorText(copy, 'service')}`,
    }
  } finally {
    saving.value = ''
  }
}

async function runGateway(action: ChannelGatewayAction): Promise<void> {
  if (gatewayBusy.value) return
  gatewayBusy.value = action
  gatewayError.value = null
  gatewayNotice.value = ''
  try {
    const result = await api.channelGateway(action)
    // Refresh from GET: the action's return value is not the page's source of truth.
    await resource.load()
    const fresh = state.value?.gateway.status ?? null
    if (fresh?.last_error || result.last_error) {
      // The card always renders `gateway.status.last_error`; leave the banner empty
      // instead of claiming success when the gateway refused to start.
      return
    }
    gatewayNotice.value =
      action === 'start'
        ? t('v2.channels.gateway.started')
        : action === 'stop'
          ? t('v2.channels.gateway.stoppedNotice')
          : t('v2.channels.gateway.restarted')
  } catch (err) {
    gatewayError.value = describeError(err)
  } finally {
    gatewayBusy.value = ''
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="v2-page" data-testid="channels-page">
    <header class="v2-page-head">
      <div class="v2-page-title">
        <span class="kicker">{{ t('v2.nav.config') }}</span>
        <h1>{{ t('v2.channels.title') }}</h1>
        <p class="lede">{{ t('v2.channels.lede') }}</p>
      </div>
      <div class="v2-page-actions">
        <button
          type="button"
          class="btn btn-sm"
          :disabled="loading || Boolean(saving) || Boolean(gatewayBusy)"
          data-testid="channels-refresh"
          @click="load()"
        >
          <AppIcon name="refresh" :size="14" />
          {{ t('v2.channels.refresh') }}
        </button>
      </div>
    </header>

    <p class="v2-catalog-note">{{ t('v2.channels.configNote') }}</p>

    <StateBlock v-if="loading && !state" kind="loading" :title="t('v2.common.loading')" />

    <StateBlock
      v-else-if="loadErrorCopy && !state"
      kind="error"
      :title="t('v2.channels.loadFailed')"
      :detail="errorText(loadErrorCopy, 'service')"
      :hint="loadErrorCopy.hint"
      :status="loadErrorCopy.status"
      :request-id="loadErrorCopy.requestId"
      show-retry
      @retry="load()"
    />

    <template v-else-if="state">
      <div v-if="loadErrorCopy" class="banner banner-warn v2-banner" role="status">
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.channels.loadFailed') }}</strong>
          <p class="note">{{ errorText(loadErrorCopy, 'service') }}</p>
        </div>
        <button type="button" class="btn btn-sm" @click="load()">
          {{ t('v2.common.retry') }}
        </button>
      </div>

      <div class="v2-channels-grid">
        <section
          v-for="card in cards"
          :key="card.name"
          class="v2-card v2-channel-card"
          :data-testid="card.testid"
        >
          <header class="v2-card-head">
            <h2 class="v2-card-title">
              <AppIcon :name="card.name === 'telegram' ? 'chat' : 'link'" :size="16" />
              {{ t(card.titleKey) }}
            </h2>
            <span
              class="badge"
              :class="drafts[card.name].enabled ? 'badge-ok' : 'badge-muted'"
              :data-testid="`${card.testid}-enabled`"
            >
              {{
                drafts[card.name].enabled ? t('v2.common.enabled') : t('v2.common.disabled')
              }}
            </span>
          </header>
          <p class="v2-card-lede">{{ t(card.descKey) }}</p>

          <!-- Token / account state is read-only: there is no value to edit here. -->
          <dl class="v2-kv v2-channel-status">
            <template v-if="card.name === 'telegram'">
              <div>
                <dt>{{ t('v2.channels.telegram.tokenStatus') }}</dt>
                <dd>
                  <span
                    class="badge"
                    :class="state.telegram.token_configured ? 'badge-ok' : 'badge-warn'"
                    data-testid="channels-telegram-token"
                  >
                    {{
                      state.telegram.token_configured
                        ? t('v2.channels.telegram.tokenConfigured')
                        : t('v2.channels.telegram.tokenMissing')
                    }}
                  </span>
                </dd>
              </div>
            </template>
            <template v-else>
              <div>
                <dt>{{ t('v2.channels.weixin.loginStatus') }}</dt>
                <dd>
                  <span
                    class="badge"
                    :class="state.weixin.logged_in ? 'badge-ok' : 'badge-muted'"
                    data-testid="channels-weixin-login"
                  >
                    {{
                      state.weixin.logged_in
                        ? t('v2.channels.weixin.loggedIn')
                        : t('v2.channels.weixin.notLoggedIn')
                    }}
                  </span>
                </dd>
              </div>
              <div>
                <dt>{{ t('v2.channels.weixin.account') }}</dt>
                <dd class="mono">
                  {{ state.weixin.account_id || t('v2.channels.weixin.noAccount') }}
                </dd>
              </div>
              <div>
                <dt>{{ t('v2.channels.weixin.user') }}</dt>
                <dd class="mono">
                  {{ state.weixin.user_id || t('v2.channels.weixin.noAccount') }}
                </dd>
              </div>
              <div v-if="state.weixin.data_dir">
                <dt>{{ t('v2.channels.weixin.dataDir') }}</dt>
                <dd class="mono v2-break">{{ state.weixin.data_dir }}</dd>
              </div>
            </template>
          </dl>

          <p
            v-if="card.name === 'telegram' && !state.telegram.token_configured"
            class="v2-catalog-note"
            data-testid="channels-telegram-token-hint"
          >
            {{ t('v2.channels.telegram.tokenMissingHint') }}
          </p>
          <p
            v-else-if="card.name === 'weixin' && !state.weixin.logged_in"
            class="v2-catalog-note"
            data-testid="channels-weixin-login-hint"
          >
            {{ t('v2.channels.weixin.notLoggedInHint') }}
          </p>

          <div class="field-row">
            <div class="field-meta">
              <span class="field-label">{{ t('v2.channels.enabled') }}</span>
            </div>
            <div class="field-control">
              <label class="switch">
                <input
                  v-model="drafts[card.name].enabled"
                  type="checkbox"
                  :disabled="saving === card.name"
                  :data-testid="`${card.testid}-enable`"
                />
                <span class="track" />
                <span class="switch-text">
                  {{
                    drafts[card.name].enabled ? t('v2.common.enabled') : t('v2.common.disabled')
                  }}
                </span>
              </label>
            </div>
          </div>

          <div class="field-row">
            <div class="field-meta">
              <span class="field-label">{{ t('v2.channels.replyMode') }}</span>
            </div>
            <div class="field-control">
              <div
                class="v2-channel-modes"
                role="group"
                :aria-label="t('v2.channels.replyMode')"
              >
                <button
                  v-for="mode in REPLY_MODES"
                  :key="mode"
                  type="button"
                  class="btn btn-sm"
                  :class="{ 'btn-primary': drafts[card.name].replyMode === mode }"
                  :aria-pressed="drafts[card.name].replyMode === mode"
                  :disabled="saving === card.name"
                  :data-testid="`${card.testid}-reply-${mode}`"
                  @click="drafts[card.name].replyMode = mode"
                >
                  {{ t(REPLY_LABEL[mode]) }}
                </button>
              </div>
            </div>
          </div>

          <div class="field-row">
            <div class="field-meta">
              <span class="field-label">{{ t(card.idsLabelKey) }}</span>
              <p class="field-hint">{{ t('v2.channels.ids.hint') }}</p>
            </div>
            <div class="field-control wide">
              <p v-if="!drafts[card.name].ids.length" class="note">
                {{ t('v2.channels.ids.empty') }}
              </p>
              <div
                v-for="(id, index) in drafts[card.name].ids"
                :key="`${card.name}-${index}`"
                class="v2-id-row"
              >
                <input
                  class="input"
                  type="text"
                  spellcheck="false"
                  autocomplete="off"
                  :value="id"
                  :placeholder="t('v2.channels.ids.placeholder')"
                  :aria-label="t(card.idsLabelKey)"
                  :disabled="saving === card.name"
                  :data-testid="`${card.testid}-id-${index}`"
                  @input="setId(card.name, index, $event)"
                />
                <button
                  type="button"
                  class="btn btn-sm btn-ghost"
                  :title="t('v2.channels.ids.remove')"
                  :aria-label="t('v2.channels.ids.remove')"
                  :disabled="saving === card.name"
                  :data-testid="`${card.testid}-remove-${index}`"
                  @click="removeId(card.name, index)"
                >
                  <AppIcon name="trash" :size="14" />
                </button>
              </div>
              <button
                type="button"
                class="btn btn-sm"
                :disabled="saving === card.name"
                :data-testid="`${card.testid}-add-id`"
                @click="addId(card.name)"
              >
                <AppIcon name="plus" :size="14" />
                {{ t('v2.channels.ids.add') }}
              </button>
            </div>
          </div>

          <footer class="v2-channel-foot">
            <button
              type="button"
              class="btn btn-primary"
              :disabled="Boolean(saving) || Boolean(gatewayBusy)"
              :data-testid="`${card.testid}-save`"
              @click="save(card.name)"
            >
              {{ saving === card.name ? t('v2.channels.saving') : t('v2.channels.save') }}
            </button>
            <span
              v-if="feedbackFor(card.name)"
              class="v2-channel-feedback"
              :class="feedbackClass(card.name)"
              role="status"
              :data-testid="`${card.testid}-feedback`"
            >
              {{ feedbackFor(card.name)?.text }}
            </span>
            <span v-if="feedbackFor(card.name)?.kind === 'ok'" class="note">
              {{ t('v2.channels.savedRestart') }}
            </span>
          </footer>
        </section>
      </div>

      <section class="v2-card" data-testid="channels-gateway">
        <header class="v2-card-head">
          <h2 class="v2-card-title">
            <AppIcon name="activity" :size="16" />
            {{ t('v2.channels.gateway.title') }}
          </h2>
          <span
            class="badge"
            :class="gatewayStateTone"
            data-testid="channels-gateway-status"
          >
            {{ gatewayStateLabel }}
          </span>
        </header>
        <p class="v2-card-lede">{{ t('v2.channels.gateway.desc') }}</p>

        <dl class="v2-kv">
          <div>
            <dt>{{ t('v2.channels.gateway.status') }}</dt>
            <dd>{{ gatewayStateLabel }}</dd>
          </div>
          <div v-if="gatewayStatus?.pid">
            <dt>{{ t('v2.channels.gateway.pid') }}</dt>
            <dd class="mono">{{ gatewayStatus.pid }}</dd>
          </div>
          <div v-if="gatewayConfigPath">
            <dt>{{ t('v2.channels.gateway.configPath') }}</dt>
            <dd class="mono v2-break">{{ gatewayConfigPath }}</dd>
          </div>
          <div v-if="gatewayLastError">
            <dt>{{ t('v2.channels.gateway.lastError') }}</dt>
            <dd class="v2-fail-text v2-break">{{ gatewayLastError }}</dd>
          </div>
        </dl>

        <div class="v2-gateway-actions">
          <button
            type="button"
            class="btn btn-sm"
            :disabled="Boolean(gatewayBusy)"
            data-testid="channels-gateway-start"
            @click="runGateway('start')"
          >
            <AppIcon name="play" :size="14" />
            {{ t('v2.channels.gateway.start') }}
          </button>
          <button
            type="button"
            class="btn btn-sm"
            :disabled="Boolean(gatewayBusy)"
            data-testid="channels-gateway-stop"
            @click="runGateway('stop')"
          >
            <AppIcon name="stop" :size="14" />
            {{ t('v2.channels.gateway.stop') }}
          </button>
          <button
            type="button"
            class="btn btn-sm"
            :disabled="Boolean(gatewayBusy)"
            data-testid="channels-gateway-restart"
            @click="runGateway('restart')"
          >
            <AppIcon name="refresh" :size="14" />
            {{ t('v2.channels.gateway.restart') }}
          </button>
          <span v-if="gatewayBusy" class="meta">{{ t('v2.channels.gateway.busy') }}</span>
        </div>

        <div
          v-if="gatewayError"
          class="banner banner-fail v2-banner"
          role="alert"
          data-testid="channels-gateway-error"
        >
          <AppIcon name="alert" :size="17" />
          <div class="v2-banner-body">
            <strong>{{ t('v2.channels.gateway.actionFailed') }}</strong>
            <p class="note">{{ errorText(gatewayError, 'gateway') }}</p>
            <p v-if="gatewayError.hint" class="note">{{ gatewayError.hint }}</p>
          </div>
        </div>

        <p
          v-if="gatewayNotice"
          class="v2-catalog-note"
          role="status"
          data-testid="channels-gateway-notice"
        >
          {{ gatewayNotice }}
        </p>
      </section>
    </template>
  </div>
</template>
