<script setup lang="ts">
/**
 * Token input, drawn by v2 itself (contract §4.2: 401 → existing token prompt,
 * v2 redraws it). The value never leaves this browser except as `X-Auth-Token`.
 */
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import AppIcon from '../../components/AppIcon.vue'
import { token } from '../api'
import { t } from '../i18n'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()

const draft = ref('')
const configured = ref(false)
const input = ref<HTMLInputElement | null>(null)

watch(
  () => props.open,
  async (open) => {
    if (!open) return
    draft.value = token.get()
    configured.value = draft.value.length > 0
    await nextTick()
    input.value?.focus()
    input.value?.select()
  },
)

function save(): void {
  token.set(draft.value.trim())
  window.location.reload()
}

function clear(): void {
  token.set('')
  window.location.reload()
}

function onKey(event: KeyboardEvent): void {
  if (event.key === 'Escape' && props.open) emit('close')
}

onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div v-if="open" class="v2-overlay" @click.self="emit('close')">
    <div
      class="v2-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="v2-token-title"
    >
      <header class="v2-dialog-head">
        <h2 id="v2-token-title">{{ t('v2.token.title') }}</h2>
        <button
          type="button"
          class="btn btn-icon"
          :aria-label="t('v2.token.close')"
          :title="t('v2.token.close')"
          @click="emit('close')"
        >
          <AppIcon name="close" :size="17" />
        </button>
      </header>
      <form class="v2-dialog-body" @submit.prevent="save">
        <label class="field-label" for="v2-token-input">{{ t('v2.token.label') }}</label>
        <input
          id="v2-token-input"
          ref="input"
          v-model="draft"
          class="input v2-input mono"
          type="password"
          autocomplete="off"
          autocapitalize="off"
          spellcheck="false"
          :placeholder="t('v2.token.placeholder')"
        />
        <p class="note">{{ t('v2.token.hint') }}</p>
        <p class="note">{{ t('v2.token.recover') }}</p>
        <div class="v2-dialog-actions">
          <button type="button" class="btn btn-ghost" @click="emit('close')">
            {{ t('v2.token.cancel') }}
          </button>
          <button v-if="configured" type="button" class="btn btn-danger" @click="clear">
            {{ t('v2.token.clear') }}
          </button>
          <button type="submit" class="btn btn-primary">{{ t('v2.token.save') }}</button>
        </div>
      </form>
    </div>
  </div>
</template>
