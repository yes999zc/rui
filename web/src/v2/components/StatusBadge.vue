<script setup lang="ts">
/** Status chip driven only by the contract's `status` enum + `status_reason`. */
import { computed } from 'vue'
import { statusLabel, statusReasonText } from '../i18n'

const props = defineProps<{
  status: string
  reason?: { code: string; message: string; hint: string } | null
}>()

const tone = computed(() => {
  switch (props.status) {
    case 'ready':
      return 'ok'
    case 'error':
    case 'unavailable':
      return 'fail'
    case 'disconnected':
    case 'unknown':
      return 'warn'
    default:
      return 'muted'
  }
})

const reasonText = computed(() => statusReasonText(props.reason))
</script>

<template>
  <span class="v2-status" :class="`is-${tone}`" :title="reasonText || undefined">
    <span class="dot" :class="tone === 'ok' ? 'ok' : tone === 'fail' ? 'fail' : tone === 'warn' ? 'warn' : ''" aria-hidden="true" />
    <span>{{ statusLabel(status) }}</span>
  </span>
</template>
