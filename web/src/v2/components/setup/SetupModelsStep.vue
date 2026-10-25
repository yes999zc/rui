<script setup lang="ts">
/**
 * `models` step: evidence only. The plan advertises no action here, so the only
 * control is a plan reload (which re-runs the server's file checks).
 */
import { computed } from 'vue'
import type { SetupStep } from '../../api'
import { boolValue } from '../../setup'
import { t } from '../../i18n'

const props = defineProps<{ step: SetupStep }>()
const emit = defineEmits<{ refresh: [] }>()

const rows = computed(() => {
  const evidence = props.step.evidence ?? {}
  return [
    { id: 'asr', label: t('v2.setup.models.asr'), ok: boolValue(evidence.asr) },
    { id: 'vad', label: t('v2.setup.models.vad'), ok: boolValue(evidence.vad) },
    { id: 'kws', label: t('v2.setup.models.kws'), ok: boolValue(evidence.kws) },
  ]
})
</script>

<template>
  <div class="v2-setup-step-body">
    <h4 class="v2-setup-subtitle">{{ t('v2.setup.models.title') }}</h4>
    <p class="note">{{ t('v2.setup.models.hint') }}</p>
    <ul class="v2-setup-check-list">
      <li v-for="row in rows" :key="row.id" :data-testid="`setup-model-${row.id}`">
        <span>{{ row.label }}</span>
        <span class="badge" :class="row.ok ? 'badge-ok' : 'badge-warn'">
          {{ row.ok ? t('v2.setup.models.ready') : t('v2.setup.models.missing') }}
        </span>
      </li>
    </ul>
    <p class="note">{{ t('v2.setup.models.noActions') }}</p>
    <div class="v2-save-bar">
      <button type="button" class="btn btn-sm" data-testid="setup-models-refresh" @click="emit('refresh')">
        {{ t('v2.setup.refreshPlan') }}
      </button>
    </div>
  </div>
</template>
