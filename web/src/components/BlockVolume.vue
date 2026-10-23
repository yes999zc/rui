<script setup lang="ts">
/**
 * The speaker's real mixer value, read from the device and written back on demand.
 *
 * This is not a config form: `devices[].volume` is the *target* the reconciler
 * keeps (0 = leave it to the vendor), while mysoftvol is what the room is doing
 * right now. Reading it is the only honest volume display.
 */
import { onMounted, ref } from 'vue'
import type { PanelBlock } from '../panels'
import { api, ApiError } from '../api'
import { localizeBackend, t } from '../i18n'
import AppIcon from './AppIcon.vue'

const props = defineProps<{ block: PanelBlock; data: unknown }>()

const current = ref<number | null>(null)
const draft = ref(128)
const max = () => props.block.max ?? 255
const min = () => props.block.min ?? 1
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
    saved.value = t('block.volumeWritten')
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="block volume-surface">
    <div class="block-head">
      <h2>{{ localizeBackend(block.title) || t('block.volume') }}</h2>
      <span class="meta">{{ localizeBackend(block.label) }}</span>
    </div>
    <div class="volume-read">
      <span class="volume-value mono">{{ current == null ? '—' : current }}</span>
      <span class="volume-max mono">/ {{ max() }}</span>
      <span class="badge">{{ t('block.volumeCurrent') }}</span>
    </div>
    <div class="volume-bar">
      <input v-model.number="draft" type="range" :min="min()" :max="max()" :disabled="busy" :aria-label="t('block.volume')" />
      <input v-model.number="draft" type="number" :min="min()" :max="max()" :disabled="busy" class="volume-number" />
      <button class="btn btn-primary" :disabled="busy || draft === current" @click="save">
        <AppIcon name="volume" :size="14" />
        {{ busy ? '…' : t('block.volumeWrite') }}
      </button>
      <button class="btn" :disabled="busy" @click="load">{{ t('block.volumeReread') }}</button>
    </div>
    <p v-if="error" class="note fail" role="alert">{{ error }}</p>
    <p v-if="saved" class="note ok">{{ saved }}</p>
    <p v-if="block.note" class="field-hint">{{ localizeBackend(block.note) }}</p>
  </section>
</template>

<style scoped>
.volume-surface {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  padding: var(--s5);
}

.volume-read {
  display: flex;
  align-items: baseline;
  gap: var(--s2);
}

.volume-value {
  font-size: 34px;
  line-height: 1.1;
  letter-spacing: -0.03em;
}

.volume-max {
  color: var(--muted);
  font-size: 13px;
}

.volume-read .badge {
  margin-left: auto;
}

.volume-bar {
  display: flex;
  align-items: center;
  gap: var(--s3);
  margin-top: var(--s3);
  flex-wrap: wrap;
}

.volume-bar input[type="range"] {
  flex: 1;
  min-width: 140px;
  accent-color: var(--accent);
}

.volume-number {
  width: 84px;
  flex: none;
}

@media (max-width: 720px) {
  .volume-surface {
    padding: var(--s4);
  }

  .volume-bar .btn {
    flex: 1;
  }
}
</style>
