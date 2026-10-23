<script setup lang="ts">
/**
 * 试一试: type one turn and send it down the same runner, tools, TTS and playback
 * path as speech (POST /api/v1/debug/say). The cheapest way to test a turn
 * without a microphone, a wake word or standing next to the speaker.
 *
 * Declared by a PanelSpec `say` block, so any panel can grow this box.
 */
import { ref } from 'vue'
import type { PanelBlock } from '../panels'
import { api, ApiError } from '../api'

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
    reply.value = result.reply || '(空回复)'
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
  <div class="card">
    <h2>{{ block.title || '试一试' }}</h2>
    <div class="say-bar">
      <input
        v-model="text"
        :placeholder="block.placeholder || '现在几点？'"
        :disabled="busy"
        aria-label="试一试的文本"
        @keyup.enter="send"
      />
      <button :disabled="busy || !text.trim()" @click="send">{{ busy ? '…' : '说一句' }}</button>
    </div>
    <div v-if="error" class="note fail">{{ error }}</div>
    <div v-if="reply" class="say-reply">{{ reply }}</div>
    <p v-if="block.hint" class="sub">{{ block.hint }}</p>
  </div>
</template>

<style scoped>
.say-bar { display: flex; gap: 8px; }
.say-bar input {
  font: inherit; font-size: 13px; padding: 7px 10px; border: 1px solid var(--line);
  border-radius: 8px; flex: 1;
}
.say-bar button { white-space: nowrap; }
.say-reply {
  margin-top: 10px; padding: 10px 12px; background: #f7f8fa; border: 1px solid var(--line);
  border-radius: 8px; font-size: 13px; white-space: pre-wrap;
}
</style>
