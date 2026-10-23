<script setup lang="ts">
/**
 * Try a sentence: type one turn and send it down the same runner, tools, TTS and playback
 * path as speech (POST /api/v1/debug/say). The cheapest way to test a turn without
 * a microphone, a wake word or standing next to the speaker.
 *
 * This is the overview's primary action, so it is the one block that gets a
 * raised surface and a real focus ring (docs/console-redesign.md §5.1).
 */
import { ref } from 'vue'
import type { PanelBlock } from '../panels'
import { api, ApiError } from '../api'
import { localizeBackend, t } from '../i18n'
import AppIcon from './AppIcon.vue'

const props = defineProps<{ block: PanelBlock; data: unknown }>()

const text = ref('')
const reply = ref('')
const error = ref('')
const busy = ref(false)

async function send() {
  const ask = text.value.trim()
  if (!ask || busy.value) return
  busy.value = true
  error.value = ''
  reply.value = ''
  try {
    const result = await api.say(ask)
    reply.value = result.reply || '—'
    if (result.error) error.value = result.error
  } catch (exc) {
    // A 409 here means the speaker is mid-turn; the backend's sentence is the answer.
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="block say-surface">
    <div class="block-head">
      <div>
        <div class="kicker">{{ t('block.tryKicker') }}</div>
        <h2 class="say-title">{{ localizeBackend(block.title) || t('block.try') }}</h2>
      </div>
    </div>
    <div class="say-bar">
      <label class="sr-only" for="say-input">{{ t('block.send') }}</label>
      <input
        id="say-input"
        v-model="text"
        :placeholder="localizeBackend(block.placeholder) || t('block.tryPlaceholder')"
        :disabled="busy"
        @keyup.enter="send"
      />
      <button type="button" class="btn btn-primary" :disabled="busy || !text.trim()" @click="send">
        <AppIcon name="arrowRight" :size="15" />
        {{ busy ? t('block.sending') : t('block.send') }}
      </button>
    </div>
    <p v-if="error" class="note fail" role="alert">{{ error }}</p>
    <blockquote v-if="reply" class="say-reply">
      <AppIcon name="quote" :size="14" class="say-quote" />
      <span>{{ reply }}</span>
    </blockquote>
    <p v-if="block.hint" class="field-hint say-hint">{{ localizeBackend(block.hint) }}</p>
  </section>
</template>

<style scoped>
.say-surface {
  background: var(--surface);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-md);
  padding: var(--s5);
}

.say-title {
  font-size: 22px;
  margin-top: 2px;
}

.say-bar {
  display: flex;
  gap: var(--s2);
}

.say-bar input {
  flex: 1;
  min-width: 0;
  min-height: 42px;
  font-size: 14px;
}

.say-bar .btn {
  min-height: 42px;
  padding-inline: 16px;
}

.say-reply {
  display: flex;
  gap: var(--s2);
  margin: var(--s4) 0 0;
  padding: 12px 14px;
  background: var(--surface-2);
  border-left: 2px solid var(--accent);
  border-radius: 0 var(--r-sm) var(--r-sm) 0;
  font-size: 14px;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.say-quote {
  flex: none;
  margin-top: 3px;
  color: var(--accent);
}

.say-hint {
  margin-top: var(--s3);
}

@media (max-width: 720px) {
  .say-surface {
    padding: var(--s4);
  }

  .say-bar {
    flex-direction: column;
  }

  .say-bar .btn {
    width: 100%;
  }
}
</style>
