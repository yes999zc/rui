<script setup lang="ts">
import { ref } from 'vue'
import type { PanelBlock } from '../panels'
import { enabledWhen } from '../panels'
import { api, ApiError } from '../api'

const props = defineProps<{ block: PanelBlock; data: unknown; refresh: () => void }>()
const busy = ref('')
const error = ref('')

async function run(button: NonNullable<PanelBlock['buttons']>[number]) {
  if (busy.value) return
  if (button.confirm && !window.confirm(button.confirm)) return
  busy.value = button.id
  error.value = ''
  try {
    await api.action(button.method, button.href, button.body ?? {})
    if (button.refresh) props.refresh()
  } catch (exc) {
    // The backend's own words: a 409 says why, a 502 says the library is down.
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}
</script>

<template>
  <div class="card">
    <h2 v-if="block.title">{{ block.title }}</h2>
    <div class="actions">
      <button
        v-for="b in block.buttons" :key="b.id"
        :class="{ danger: b.danger }"
        :disabled="busy !== '' || !enabledWhen(b.enabled_when, data)"
        @click="run(b)"
      >{{ busy === b.id ? '…' : b.label }}</button>
    </div>
    <div v-if="error" class="note fail">{{ error }}</div>
  </div>
</template>

<style scoped>
.actions { display: flex; flex-wrap: wrap; gap: 8px; }
button.danger { color: var(--fail); border-color: #f3c6c6; }
</style>
