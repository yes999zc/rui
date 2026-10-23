<script setup lang="ts">
/**
 * Voice audition (TTS only). Synthesises one short line through the same
 * provider the speaker will use, with the *draft* voice/rate/emotion, and plays
 * it locally. Nothing is written to the config.
 */
import { onUnmounted, ref } from 'vue'
import AppIcon from '../AppIcon.vue'
import { token } from '../../api'
import { t } from '../../i18n'

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

type Status = 'idle' | 'loading' | 'playing' | 'error'

const status = ref<Status>('idle')
const error = ref('')
let audio: HTMLAudioElement | null = null
let objectUrl = ''

function stop(): void {
  if (audio) {
    audio.pause()
    audio.onended = null
    audio = null
  }
  if (objectUrl) {
    URL.revokeObjectURL(objectUrl)
    objectUrl = ''
  }
  if (status.value === 'playing') status.value = 'idle'
}

function numberOrUndefined(value: unknown): number | undefined {
  if (value === '' || value == null) return undefined
  const number = Number(value)
  return Number.isFinite(number) ? number : undefined
}

async function play(): Promise<void> {
  if (!props.ready || !props.voice || status.value === 'loading') return
  if (status.value === 'playing') {
    stop()
    return
  }
  stop()
  status.value = 'loading'
  error.value = ''
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

    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    const authToken = token.get()
    if (authToken) headers['X-Auth-Token'] = authToken

    const response = await fetch(
      `/api/v1/providers/${props.stage}/${encodeURIComponent(props.provider)}/audition`,
      { method: 'POST', headers, body: JSON.stringify(body) },
    )
    if (!response.ok) {
      let detail = response.statusText
      try {
        const payload = (await response.json()) as { detail?: unknown }
        detail = typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail)
      } catch {
        /* not JSON: keep the status text */
      }
      throw new Error(detail || `HTTP ${response.status}`)
    }
    const blob = await response.blob()
    objectUrl = URL.createObjectURL(blob)
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
    <button
      type="button"
      class="btn btn-sm audition-btn"
      :class="{ 'btn-primary': status === 'playing' }"
      :disabled="!ready || !voice || status === 'loading'"
      :title="ready ? t('providers.auditionHint') : t('providers.auditionSavedOnly')"
      @click="play"
    >
      <AppIcon :name="status === 'playing' ? 'stop' : 'play'" :size="12" />
      {{ status === 'loading' ? t('providers.auditioning') : status === 'playing' ? t('providers.auditionStop') : t('providers.audition') }}
    </button>
    <span class="audition-note" aria-live="polite">
      <template v-if="!ready">{{ t('providers.auditionSavedOnly') }}</template>
      <template v-else-if="status === 'playing'">{{ t('providers.auditionPlaying') }}</template>
      <template v-else>{{ t('providers.auditionHint') }}</template>
    </span>
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

.audition-btn {
  flex: none;
}

.audition-note {
  color: var(--muted);
  font-size: 10.5px;
  line-height: 1.4;
  max-width: 42ch;
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

  .audition-btn {
    min-height: 44px;
    padding: 10px 14px;
  }

  .audition-note {
    max-width: none;
    font-size: 11.5px;
  }
}
</style>
