<script setup lang="ts">
/**
 * Blocking second confirmation used by every M3 write.
 *
 * The overlay is destructive-safe: the confirm button is the only way forward,
 * it can be disabled while a request is in flight, and Escape/cancel never
 * submits. Callers supply all copy so both languages come from `i18n.ts`.
 */
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
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

/**
 * `Teleport` moves the dialog out of the trigger's DOM, so the browser does not
 * move focus for us: the trigger stays focused behind the overlay. Move focus
 * in on mount, keep Tab/Shift+Tab inside, and hand focus back on unmount.
 */
const dialogRef = ref<HTMLElement | null>(null)
let previouslyFocused: HTMLElement | null = null

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

function focusableItems(): HTMLElement[] {
  const root = dialogRef.value
  if (!root) return []
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (element) =>
      !element.hasAttribute('disabled') &&
      element.getAttribute('aria-hidden') !== 'true' &&
      element.getClientRects().length > 0,
  )
}

function focusInitial(): void {
  const root = dialogRef.value
  if (!root) return
  const cancelButton = root.querySelector<HTMLElement>('[data-testid="confirm-cancel"]')
  if (cancelButton && !cancelButton.hasAttribute('disabled')) {
    cancelButton.focus({ preventScroll: true })
    return
  }
  root.focus({ preventScroll: true })
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.preventDefault()
    cancel()
    return
  }
  if (event.key !== 'Tab' || !dialogRef.value) return

  // Native focus order would escape to the page behind the overlay; wrap it at
  // both ends instead. If focus somehow starts outside the dialog, pull it in.
  const items = focusableItems()
  if (!items.length) {
    event.preventDefault()
    dialogRef.value.focus({ preventScroll: true })
    return
  }
  const first = items[0]
  const last = items[items.length - 1]
  const active = document.activeElement
  const inside = active instanceof HTMLElement && dialogRef.value.contains(active)
  if (event.shiftKey) {
    if (!inside || active === first || active === dialogRef.value) {
      event.preventDefault()
      last.focus({ preventScroll: true })
    }
    return
  }
  if (!inside || active === last) {
    event.preventDefault()
    first.focus({ preventScroll: true })
  }
}

onMounted(() => {
  previouslyFocused =
    document.activeElement instanceof HTMLElement ? document.activeElement : null
  document.addEventListener('keydown', onKeydown)
  void nextTick(focusInitial)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  const target = previouslyFocused
  previouslyFocused = null
  // The trigger may be gone (a confirmed write can rerender the page); only
  // restore focus to a still-focusable element.
  if (target && target.isConnected && !target.hasAttribute('disabled')) {
    target.focus({ preventScroll: true })
  }
})
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
        ref="dialogRef"
        class="v2-dialog"
        role="alertdialog"
        aria-modal="true"
        :aria-label="title"
        tabindex="-1"
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
