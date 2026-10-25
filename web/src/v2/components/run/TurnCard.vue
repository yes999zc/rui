<script setup lang="ts">
/**
 * One conversation turn (M8).
 *
 * `/api/v1/turns` rows carry the transcript, the reply, the tools that ran, and
 * per-stage latency reports. Everything shown here comes from those fields; a
 * turn without `device_id` is labelled "whole house" by `DeviceBadge`, and a
 * turn that names a device missing from the catalog keeps the raw id.
 */
import { computed } from 'vue'
import AppIcon from '../../../components/AppIcon.vue'
import type { TurnItem, TurnStage } from '../../api'
import { formatDateTime, relativeTime } from '../../format'
import { t } from '../../i18n'
import DeviceBadge from './DeviceBadge.vue'

const props = defineProps<{
  turn: TurnItem
  /** Catalog room/model for `turn.device_id`, when the catalog knows it. */
  room?: string
  model?: string
  mapped?: boolean
  /** The catalog request failed, so `!mapped` means "unknown", not "not listed". */
  catalogUnavailable?: boolean
}>()

const STAGE_ORDER = ['asr', 'llm', 'tts']

const epoch = computed(() => {
  const value = props.turn.ts_epoch
  return typeof value === 'number' && Number.isFinite(value) ? value : null
})

const relative = computed(() => (epoch.value === null ? '' : relativeTime(epoch.value)))
const absolute = computed(() => {
  if (epoch.value !== null) return formatDateTime(epoch.value)
  return props.turn.ts ?? ''
})
const timeLabel = computed(() => relative.value || props.turn.ts || t('v2.conversation.timeUnknown'))

function stageLabel(name: string): string {
  if (name === 'asr') return t('v2.conversation.stage.asr')
  if (name === 'llm') return t('v2.conversation.stage.llm')
  if (name === 'tts') return t('v2.conversation.stage.tts')
  return t('v2.conversation.stage.other', { name })
}

interface StageEntry {
  key: string
  stage: TurnStage
  label: string
  /**
   * Only measured (>0) times are shown. `asr` is the whole transcription call,
   * `llm` the time-to-first-token of the speaking round, `tts` the synthesis
   * time summed over every sentence.
   */
  ms: number | null
  /**
   * Attempts that failed and were retried on the next provider. TTS emits one
   * successful attempt per sentence, so `attempts.length` is not a fallback
   * count; only entries carrying an `error` are.
   */
  fallbacks: number
}

function measuredMs(stage: TurnStage): number | null {
  const ms = stage.elapsed_ms
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms <= 0) return null
  return Math.round(ms)
}

function fallbackCount(stage: TurnStage): number {
  if (!Array.isArray(stage.attempts)) return 0
  // A successful attempt carries `error: ""`; only failed ones are fallbacks.
  return stage.attempts.filter((attempt) => Boolean(attempt?.error)).length
}

const stages = computed<StageEntry[]>(() => {
  const raw = props.turn.stages ?? {}
  const keys = Object.keys(raw)
  keys.sort((a, b) => {
    const ai = STAGE_ORDER.indexOf(a)
    const bi = STAGE_ORDER.indexOf(b)
    if (ai === -1 && bi === -1) return a.localeCompare(b)
    if (ai === -1) return 1
    if (bi === -1) return -1
    return ai - bi
  })
  const out: StageEntry[] = []
  for (const key of keys) {
    const stage = raw[key]
    if (!stage || typeof stage !== 'object') continue
    out.push({
      key,
      stage,
      label: stageLabel(key),
      ms: measuredMs(stage),
      fallbacks: fallbackCount(stage),
    })
  }
  return out
})

const measuredTotalMs = computed<number | null>(() => {
  let total = 0
  let seen = false
  for (const entry of stages.value) {
    if (entry.ms === null) continue
    total += entry.ms
    seen = true
  }
  return seen ? total : null
})

const tools = computed(() => (props.turn.tools ?? []).filter((tool) => tool !== ''))
const transcript = computed(() => (props.turn.transcript ?? '').trim())
const reply = computed(() => (props.turn.reply ?? '').trim())
const error = computed(() => (props.turn.error ?? '').trim())
</script>

<template>
  <article
    class="v2-turn"
    data-testid="turn"
    :data-device-id="turn.device_id ?? ''"
  >
    <header class="v2-turn-head">
      <span class="meta mono" :title="absolute">{{ timeLabel }}</span>
      <DeviceBadge
        :device-id="turn.device_id ?? ''"
        :room="room"
        :model="model"
        :mapped="Boolean(mapped)"
        :catalog-unavailable="Boolean(catalogUnavailable)"
      />
      <span v-if="measuredTotalMs !== null" class="tag mono v2-turn-duration">
        {{ t('v2.conversation.duration') }} · {{ measuredTotalMs }} ms
      </span>
    </header>

    <div class="v2-turn-body">
      <div class="v2-turn-block">
        <span class="v2-turn-k">{{ t('v2.conversation.user') }}</span>
        <p class="v2-turn-text" :class="{ 'is-muted': !transcript }">
          {{ transcript || t('v2.conversation.noTranscript') }}
        </p>
      </div>
      <div class="v2-turn-block">
        <span class="v2-turn-k">{{ t('v2.conversation.reply') }}</span>
        <p class="v2-turn-text" :class="{ 'is-muted': !reply }">
          {{ reply || t('v2.conversation.noReply') }}
        </p>
      </div>
      <p v-if="error" class="v2-turn-error">
        <AppIcon name="alert" :size="14" />
        <span><strong>{{ t('v2.conversation.error') }}</strong> {{ error }}</span>
      </p>
    </div>

    <footer v-if="stages.length || tools.length" class="v2-turn-foot">
      <div v-if="stages.length" class="v2-chips" :aria-label="t('v2.conversation.stages')">
        <span
          v-for="entry in stages"
          :key="entry.key"
          class="v2-cap mono"
          :title="`${entry.stage.used || entry.key} · ${t('v2.conversation.stages')}`"
        >
          {{ entry.label }}
          <template v-if="entry.ms !== null"> · {{ entry.ms }} ms</template>
          <template v-if="entry.fallbacks > 0"> · ×{{ entry.fallbacks }}</template>
        </span>
      </div>
      <div v-if="tools.length" class="v2-chips" :aria-label="t('v2.conversation.tools')">
        <span v-for="tool in tools" :key="tool" class="v2-cap mono">{{ tool }}</span>
      </div>
    </footer>
  </article>
</template>
