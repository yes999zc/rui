<script setup lang="ts">
/**
 * Blocking second confirmation used by every M3 write.
 *
 * The overlay is destructive-safe: the confirm button is the only way forward,
 * it can be disabled while a request is in flight, and Escape/cancel never
 * submits. Callers supply all copy so both languages come from `i18n.ts`.
 */
import { onBeforeUnmount, onMounted } from 'vue'
import AppIcon from '../../components/AppIcon.vue'

const props = withDefaults(
  defineProps<{
    title: string
    confirmLabel: string
    cancelLabel: string
    tone?: 'default' | 'danger'
    busy?: boolean
    busyLabel?: string
    confirmDisabled?: boolean
  }>(),
  {
    tone: 'default',
    busy: false,
    busyLabel: '',
    confirmDisabled: false,
  },
)

const emit = defineEmits<{ confirm: []; cancel: [] }>()

function cancel(): void {
  if (props.busy) return
  emit('cancel')
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.preventDefault()
    cancel()
  }
}

onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="v2-overlay" role="presentation" data-testid="confirm-dialog" @click.self="cancel">
    <div
      class="v2-dialog"
      role="alertdialog"
      aria-modal="true"
      :aria-label="title"
    >
      <div class="v2-dialog-head">
        <h2>{{ title }}</h2>
        <button
          type="button"
          class="btn btn-icon"
          :aria-label="cancelLabel"
          :disabled="busy"
          @click="cancel"
        >
          <AppIcon name="close" :size="16" />
        </button>
      </div>
      <div class="v2-dialog-body">
        <slot />
      </div>
      <div class="v2-dialog-actions">
        <button type="button" class="btn" :disabled="busy" data-testid="confirm-cancel" @click="cancel">
          {{ cancelLabel }}
        </button>
        <button
          type="button"
          class="btn"
          data-testid="confirm-accept"
          :class="tone === 'danger' ? 'btn-danger' : 'btn-primary'"
          :disabled="busy || confirmDisabled"
          @click="emit('confirm')"
        >
          {{ busy ? busyLabel || confirmLabel : confirmLabel }}
        </button>
      </div>
    </div>
  </div>
</template>
