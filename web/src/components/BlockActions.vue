<script setup lang="ts">
import { ref } from 'vue'
import type { PanelBlock } from '../panels'
import { enabledWhen } from '../panels'
import { api, ApiError } from '../api'
import { localizeBackend } from '../i18n'
import AppIcon from './AppIcon.vue'

const props = defineProps<{ block: PanelBlock; data: unknown; refresh: () => void }>()
const busy = ref('')
const error = ref('')

const ICONS: Record<string, string> = {
  prev: 'skipBack',
  pause: 'pause',
  resume: 'play',
  next: 'skipForward',
  play: 'play',
  stop: 'stop',
  stopAudio: 'stop',
  mute: 'volume',
}

function run(button: NonNullable<PanelBlock['buttons']>[number]) {
  if (busy.value) return
  if (button.confirm && !window.confirm(localizeBackend(button.confirm))) return
  busy.value = button.id
  error.value = ''
  void (async () => {
    try {
      await api.action(button.method, button.href, button.body ?? {})
      if (button.refresh) props.refresh()
    } catch (exc) {
      // The backend's own words: a 409 says why, a 502 says the library is down.
      error.value = exc instanceof ApiError ? exc.message : String(exc)
    } finally {
      busy.value = ''
    }
  })()
}
</script>

<template>
  <section class="block">
    <div v-if="block.title" class="block-head">
      <h2>{{ localizeBackend(block.title) }}</h2>
    </div>
    <div class="actions">
      <button
        v-for="b in block.buttons"
        :key="b.id"
        class="btn"
        :class="{ 'btn-danger': b.danger }"
        :disabled="busy !== '' || !enabledWhen(b.enabled_when, data)"
        @click="run(b)"
      >
        <AppIcon v-if="ICONS[b.id]" :name="ICONS[b.id]" :size="14" />
        {{ busy === b.id ? '…' : localizeBackend(b.label) }}
      </button>
    </div>
    <p v-if="error" class="note fail" role="alert">{{ error }}</p>
  </section>
</template>
