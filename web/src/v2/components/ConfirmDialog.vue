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

// The root is a `<Teleport>`; forward caller attrs (`data-testid`, ...) to the
// inner dialog instead of losing them to the teleport boundary.
defineOptions({ inheritAttrs: false })

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
  <!--
    Teleport out of the caller's DOM. Setup steps live inside a
    `<fieldset :disabled="cooldownActive">`; a native disabled fieldset also
    disables every control inside a dialog and would trap the operator in it
    during a 429 cooldown. Cancel/close stay usable because the dialog is no
    longer a fieldset descendant; callers can still disable only `confirm`.
  -->
  <Teleport to="body">
    <div class="v2-overlay" role="presentation" data-testid="confirm-dialog" @click.self="cancel">
      <div
        class="v2-dialog"
        role="alertdialog"
        aria-modal="true"
        :aria-label="title"
        v-bind="$attrs"
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
  </Teleport>
</template>
