<script setup lang="ts">
/**
 * The speaker's real mixer value, read from the device and written back on demand.
 *
 * This is not a config form: `devices[].volume` is the *target* the reconciler
 * keeps (0 = leave it to the vendor), while mysoftvol is what the room is doing
 * right now - the vendor rewrites it on its own. Reading it is the only honest
 * volume display, so this block takes its own GET/PUT pair from the spec.
 */
import { onMounted, ref } from 'vue'
import type { PanelBlock } from '../panels'
import { api, ApiError } from '../api'

const props = defineProps<{ block: PanelBlock; data: unknown }>()

const current = ref<number | null>(null)
const draft = ref(128)
const min = () => props.block.min ?? 1
const max = () => props.block.max ?? 255
const busy = ref(false)
const error = ref('')
const saved = ref('')

async function load() {
  try {
    const body = await api.deviceVolume()
    current.value = body.volume
    draft.value = body.volume
    error.value = ''
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  }
}

async function save() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  saved.value = ''
  try {
    const body = await api.setDeviceVolume(draft.value)
    current.value = body.volume ?? draft.value
    saved.value = '已写入设备'
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="card">
    <h2>{{ block.title || '设备音量' }}</h2>
    <div class="row">
      <span class="v">当前 {{ current == null ? '—' : current }}</span>
      <span class="k" style="width: auto">/ {{ max() }}</span>
    </div>
    <div class="bar">
      <input v-model.number="draft" type="range" :min="min()" :max="max()" :disabled="busy" />
      <input v-model.number="draft" type="number" :min="min()" :max="max()" :disabled="busy" />
      <button :disabled="busy || draft === current" @click="save">{{ busy ? '…' : '写入' }}</button>
      <button :disabled="busy" @click="load">重读</button>
    </div>
    <p v-if="block.label" class="sub">{{ block.label }}</p>
    <div v-if="error" class="note fail">{{ error }}</div>
    <div v-if="saved" class="note">{{ saved }}</div>
    <p v-if="block.note" class="sub">{{ block.note }}</p>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; align-items: center; }
.bar input[type='range'] { flex: 1; }
.bar input[type='number'] { width: 72px; font: inherit; font-size: 13px; padding: 6px 8px;
  border: 1px solid var(--line); border-radius: 8px; }
</style>
