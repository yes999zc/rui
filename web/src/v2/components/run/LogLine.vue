<script setup lang="ts">
/**
 * One rendered log line (M8).
 *
 * The service log format is `YYYY-MM-DD HH:MM:SS,mmm LEVEL logger: message`
 * (continuation lines such as tracebacks carry no level and inherit the level
 * of the last timestamped line in the page). The caller normalizes the trailing
 * newline before passing `raw`, so render and copy share the same text.
 */
import AppIcon from '../../../components/AppIcon.vue'
import { t } from '../../i18n'

defineProps<{
  raw: string
  level: string
  copied?: boolean
  failed?: boolean
}>()

const emit = defineEmits<{ copy: [] }>()

function levelClass(level: string): string {
  if (level === 'ERROR' || level === 'CRITICAL') return 'is-error'
  if (level === 'WARN' || level === 'WARNING') return 'is-warn'
  if (level === 'INFO') return 'is-info'
  return ''
}
</script>

<template>
  <div
    class="v2-log-line"
    :class="levelClass(level)"
    data-testid="log-line"
    :data-level="level"
  >
    <span class="v2-log-level tag mono" :title="level || t('v2.logs.noLevel')">
      {{ level || t('v2.logs.noLevel') }}
    </span>
    <span class="v2-log-text mono">{{ raw }}</span>
    <button
      type="button"
      class="btn btn-icon v2-log-copy"
      :title="failed ? t('v2.logs.copyFailed') : copied ? t('v2.logs.copied') : t('v2.logs.copyLine')"
      :aria-label="failed ? t('v2.logs.copyFailed') : copied ? t('v2.logs.copied') : t('v2.logs.copyLine')"
      @click="emit('copy')"
    >
      <AppIcon :name="failed ? 'alert' : copied ? 'check' : 'copy'" :size="13" />
    </button>
  </div>
</template>
