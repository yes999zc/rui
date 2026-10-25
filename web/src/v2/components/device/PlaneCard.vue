<script setup lang="ts">
/** Per-device audio plane counters (read-only projection of `device.plane`). */
import type { DevicePlane } from '../../api'
import { t } from '../../i18n'

defineProps<{ plane: DevicePlane }>()

function boolText(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return t('v2.common.na')
  return value ? t('v2.common.yes') : t('v2.common.no')
}

function connectedText(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return t('v2.detail.connectedNone')
  return value ? t('v2.common.yes') : t('v2.common.no')
}
</script>

<template>
  <section v-if="plane" class="v2-card">
    <h2 class="v2-card-title">{{ t('v2.detail.plane') }}</h2>
    <dl class="v2-kv">
      <div>
        <dt>{{ t('v2.detail.planeRunning') }}</dt>
        <dd>{{ boolText(plane.running) }}</dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.planeSpeaker') }}</dt>
        <dd>{{ connectedText(plane.speaker_connected) }}</dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.planeRestarts') }}</dt>
        <dd class="mono">{{ plane.restarts }}</dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.planeUtterances') }}</dt>
        <dd class="mono">{{ plane.utterances }}</dd>
      </div>
      <div v-if="plane.error">
        <dt>{{ t('v2.detail.planeError') }}</dt>
        <dd class="v2-fail-text">{{ plane.error }}</dd>
      </div>
    </dl>
  </section>
</template>
