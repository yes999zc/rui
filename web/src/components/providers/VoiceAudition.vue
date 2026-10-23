<script setup lang="ts">
/**
 * Voice audition (TTS only). Synthesises one short line through the same
 * provider the speaker will use, with the *draft* voice/rate/emotion.
 *
 * Default output is the room speaker (the real timbre). If the backend cannot
 * play on the speaker yet, the same click falls back to a browser WAV and says so;
 * the UI also lets the operator force browser playback. Nothing touches config.
 */
import { onUnmounted, ref } from 'vue'
import AppIcon from '../AppIcon.vue'
import { t } from '../../i18n'
import {
  readAuditionOutput,
  requestAudition,
  writeAuditionOutput,
  type AuditionOutput,
} from './audition'

const props = defineProps<{
  stage: 'tts'
  provider: string
  voice: string
  speechRate?: unknown
  emotion?: unknown
  emotionScale?: unknown
  /** False until the provider is saved into the running chain. */
  ready: boolean
}>()

type Status = 'idle' | 'loading' | 'playing' | 'speaker' | 'error'

const output = ref<AuditionOutput>(readAuditionOutput())
const status = ref<Status>('idle')
const error = ref('')
const notice = ref('')
let audio: HTMLAudioElement | null = null
let objectUrl = ''
let speakerTimer: number | undefined

function clearSpeakerTimer(): void {
  if (speakerTimer) window.clearTimeout(speakerTimer)
  speakerTimer = undefined
}

function stop(): void {
  clearSpeakerTimer()
  if (audio) {
    audio.pause()
    audio.onended = null
    audio = null
  }
  if (objectUrl) {
    URL.revokeObjectURL(objectUrl)
    objectUrl = ''
  }
  if (status.value === 'playing' || status.value === 'speaker') status.value = 'idle'
}

function chooseOutput(next: AuditionOutput): void {
  if (output.value === next) return
  output.value = next
  writeAuditionOutput(next)
  stop()
  error.value = ''
  notice.value = ''
}

function numberOrUndefined(value: unknown): number | undefined {
  if (value === '' || value == null) return undefined
  const number = Number(value)
  return Number.isFinite(number) ? number : undefined
}

async function play(): Promise<void> {
  if (!props.ready || !props.voice || status.value === 'loading' || status.value === 'speaker') return
  if (status.value === 'playing') {
    stop()
    return
  }
  stop()
  status.value = 'loading'
  error.value = ''
  notice.value = ''
  try {
    const body: Record<string, unknown> = {
      text: t('providers.auditionText'),
      voice: props.voice,
    }
    const rate = numberOrUndefined(props.speechRate)
    if (rate !== undefined) body.speech_rate = rate
    if (props.emotion !== undefined && props.emotion !== null && props.emotion !== '') {
      body.emotion = String(props.emotion)
    }
    const scale = numberOrUndefined(props.emotionScale)
    if (scale !== undefined) body.emotion_scale = scale

    const outcome = await requestAudition(
      { stage: props.stage, provider: props.provider, body },
      output.value,
    )
    if (outcome.played === 'speaker') {
      status.value = 'speaker'
      notice.value = outcome.duration_ms
        ? t('providers.auditionSpeakerDone', { ms: outcome.duration_ms })
        : t('providers.auditionSpeakerPlaying')
      speakerTimer = window.setTimeout(() => {
        if (status.value === 'speaker') status.value = 'idle'
      }, Math.max(1000, outcome.duration_ms + 600))
      return
    }

    if (outcome.fallback) notice.value = outcome.fallback
    objectUrl = URL.createObjectURL(outcome.blob)
    audio = new Audio(objectUrl)
    audio.onended = () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl)
      objectUrl = ''
      audio = null
      status.value = 'idle'
    }
    await audio.play()
    status.value = 'playing'
  } catch (exc) {
    stop()
    status.value = 'error'
    error.value = exc instanceof Error ? exc.message : String(exc)
  }
}

onUnmounted(stop)
</script>

<template>
  <div class="audition">
    <div class="audition-output" role="group" :aria-label="t('providers.auditionOutput')">
      <button
        type="button"
        class="audition-out"
        :class="{ on: output === 'speaker' }"
        :aria-pressed="output === 'speaker'"
        :disabled="status === 'loading'"
        @click="chooseOutput('speaker')"
      >
        {{ t('providers.auditionSpeaker') }}
      </button>
      <button
        type="button"
        class="audition-out"
        :class="{ on: output === 'browser' }"
        :aria-pressed="output === 'browser'"
        :disabled="status === 'loading'"
        @click="chooseOutput('browser')"
      >
        {{ t('providers.auditionLocal') }}
      </button>
    </div>
    <button
      type="button"
      class="btn btn-sm audition-btn"
      :class="{ 'btn-primary': status === 'playing' }"
      :disabled="!ready || !voice || status === 'loading' || status === 'speaker'"
      :title="ready ? t('providers.auditionHint') : t('providers.auditionSavedOnly')"
      @click="play"
    >
      <AppIcon :name="status === 'playing' ? 'stop' : 'play'" :size="12" />
      {{
        status === 'loading'
          ? t('providers.auditioning')
          : status === 'playing'
            ? t('providers.auditionStop')
            : status === 'speaker'
              ? t('providers.auditionSpeakerPlaying')
              : t('providers.audition')
      }}
    </button>
    <span class="audition-note" aria-live="polite">
      <template v-if="!ready">{{ t('providers.auditionSavedOnly') }}</template>
      <template v-else-if="status === 'speaker'">{{ notice || t('providers.auditionSpeakerPlaying') }}</template>
      <template v-else-if="status === 'playing'">{{ t('providers.auditionPlaying') }}</template>
      <template v-else>{{ output === 'speaker' ? t('providers.auditionSpeakerNote') : t('providers.auditionLocalNote') }}</template>
    </span>
    <p v-if="notice && status !== 'speaker'" class="audition-fallback" role="status">{{ notice }}</p>
    <p v-if="error" class="audition-error" role="alert">{{ t('providers.auditionFailed', { message: error }) }}</p>
  </div>
</template>

<style scoped>
.audition {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-width: 0;
}

.audition-output {
  display: inline-flex;
  padding: 2px;
  gap: 2px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface);
  flex: none;
}

.audition-out {
  border: 0;
  background: transparent;
  color: var(--muted);
  padding: 3px 7px;
  border-radius: var(--r-xs);
  font-size: 10.5px;
  line-height: 1.4;
  cursor: pointer;
  white-space: nowrap;
}

.audition-out:hover {
  color: var(--ink);
}

.audition-out.on {
  background: var(--accent-soft);
  color: var(--accent);
}

.audition-btn {
  flex: none;
}

.audition-note {
  color: var(--muted);
  font-size: 10.5px;
  line-height: 1.4;
  max-width: 46ch;
}

.audition-fallback {
  flex: 1 1 100%;
  margin: 0;
  color: var(--warn);
  font-size: 10.5px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.audition-error {
  flex: 1 1 100%;
  color: var(--fail);
  font-size: 11px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

@media (max-width: 720px) {
  .audition {
    width: 100%;
    gap: var(--s2);
  }

  .audition-out,
  .audition-btn {
    min-height: 44px;
    padding: 10px 12px;
  }

  .audition-note {
    max-width: none;
    font-size: 11.5px;
  }
}
</style>
