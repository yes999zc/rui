<script setup lang="ts">
/**
 * The `restart` envelope returned by device writes (contract §4.5/§8.4).
 *
 * A write can be 200 and still have failed to rebuild the fleet, so this block
 * always shows scope/status/affected devices and renders `restart.error`
 * prominently in red when it is non-empty. The retry button only re-reads the
 * device state — it never re-submits the write.
 */
import { computed } from 'vue'
import AppIcon from '../../../components/AppIcon.vue'
import type { Restart } from '../../api'
import { restartScopeLabel, restartStatusLabel, t } from '../../i18n'

const props = withDefaults(
  defineProps<{
    restart: Restart
    retrying?: boolean
    showRetry?: boolean
  }>(),
  {
    retrying: false,
    showRetry: true,
  },
)

const emit = defineEmits<{ retry: [] }>()

const failed = computed(() => Boolean(props.restart.error) || props.restart.status === 'failed')
const statusClass = computed(() => {
  if (failed.value) return 'banner-fail'
  if (props.restart.status === 'stopped' || props.restart.status === 'pending') {
    return 'banner-warn'
  }
  return 'banner-ok'
})
const summary = computed(() => {
  if (failed.value) return t('v2.deviceConfig.restartFailed')
  switch (props.restart.status) {
    case 'stopped':
      return t('v2.deviceConfig.restartStopped')
    case 'pending':
      return t('v2.deviceConfig.restartPending')
    default:
      return t('v2.deviceConfig.restartOk')
  }
})
const affected = computed(() => props.restart.affected_devices ?? [])
const iconName = computed(() => {
  if (failed.value) return 'alert'
  return props.restart.status === 'ok' ? 'check' : 'clock'
})
</script>

<template>
  <div
    class="banner v2-banner v2-restart"
    :class="statusClass"
    role="status"
    data-testid="restart-notice"
  >
    <AppIcon :name="iconName" :size="18" />
    <div class="v2-banner-body">
      <strong>{{ summary }}</strong>
      <dl class="v2-kv v2-restart-kv">
        <div>
          <dt>{{ t('v2.deviceConfig.restartScope') }}</dt>
          <dd class="mono">{{ restartScopeLabel(restart.scope) }}</dd>
        </div>
        <div>
          <dt>{{ t('v2.deviceConfig.restartStatus') }}</dt>
          <dd class="mono">{{ restartStatusLabel(restart.status) }}</dd>
        </div>
        <div v-if="affected.length">
          <dt>{{ t('v2.deviceConfig.restartAffected') }}</dt>
          <dd class="mono">{{ affected.join(', ') }}</dd>
        </div>
        <div v-if="restart.duration_ms !== null && restart.duration_ms !== undefined">
          <dt>{{ t('v2.deviceConfig.restartDuration') }}</dt>
          <dd class="mono">{{ t('v2.common.ms', { n: restart.duration_ms }) }}</dd>
        </div>
      </dl>
      <p
        v-if="restart.error"
        class="v2-restart-error"
        data-testid="restart-error"
        role="alert"
      >
        <strong>{{ t('v2.deviceConfig.restartError') }}</strong>
        <span class="mono v2-break">{{ restart.error }}</span>
      </p>
      <button
        v-if="showRetry && failed"
        type="button"
        class="btn btn-sm"
        :disabled="retrying"
        @click="emit('retry')"
      >
        <AppIcon name="refresh" :size="14" />
        {{ retrying ? t('v2.deviceConfig.reloading') : t('v2.deviceConfig.retryRead') }}
      </button>
    </div>
  </div>
</template>
