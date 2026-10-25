<script setup lang="ts">
/**
 * `connect` step: read-only. The console never reveals the full address here;
 * it shows the server's masked URL and the manifest's device-side steps so the
 * operator can install/restart the client without a model-specific branch.
 */
import { computed } from 'vue'
import type { SetupStep } from '../../api'
import { boolValue, isStepSettled } from '../../setup'
import { t, transportLabel } from '../../i18n'

const props = defineProps<{
  step: SetupStep
  prepareStep: SetupStep | null
}>()

const emit = defineEmits<{ refresh: [] }>()

const transport = computed(() => String(props.step.evidence?.transport ?? ''))
const outbound = computed(() => transport.value === 'outbound' || props.step.status === 'skipped')
const masked = computed(() => {
  const value = props.step.evidence?.ws_url_masked
  return typeof value === 'string' ? value : ''
})
const configured = computed(() => boolValue(props.step.evidence?.configured))
const tokenEnv = computed(() => {
  const value = props.step.evidence?.token_env
  return typeof value === 'string' ? value : ''
})
const installSteps = computed(() => props.prepareStep?.detail?.install_steps ?? [])
const settled = computed(() => isStepSettled(props.step))
</script>

<template>
  <div class="v2-setup-step-body">
    <h4 class="v2-setup-subtitle">{{ t('v2.setup.connect.title') }}</h4>

    <p v-if="outbound" class="note" data-testid="setup-connect-outbound">
      {{ t('v2.setup.connect.outbound') }}
    </p>

    <template v-else>
      <p class="note">{{ t('v2.setup.connect.hint') }}</p>

      <div class="v2-setup-evidence" data-testid="setup-connect-address">
        <span class="v2-summary-label">{{ t('v2.setup.connect.masked') }}</span>
        <p v-if="masked" class="mono v2-break">{{ masked }}</p>
        <p v-else class="note">{{ t('v2.setup.connect.noAddress') }}</p>
        <p class="note">{{ t('v2.setup.connect.maskedNote') }}</p>
      </div>

      <dl class="v2-kv v2-setup-kv">
        <div>
          <dt>{{ t('v2.setup.connect.transport') }}</dt>
          <dd>{{ transport ? transportLabel(transport) : t('v2.common.na') }}</dd>
        </div>
        <div>
          <dt>{{ t('v2.setup.connect.configured') }}</dt>
          <dd>
            <span class="badge" :class="configured ? 'badge-ok' : 'badge-warn'">
              {{ configured ? t('v2.setup.connect.configured') : t('v2.setup.connect.notConfigured') }}
            </span>
          </dd>
        </div>
        <div v-if="tokenEnv">
          <dt>{{ t('v2.setup.connect.tokenEnv') }}</dt>
          <dd class="mono">{{ tokenEnv }}</dd>
        </div>
      </dl>

      <p v-if="!settled" class="note">{{ t('v2.setup.connect.pending') }}</p>
      <p class="note">{{ t('v2.setup.connect.instruction') }}</p>

      <div v-if="installSteps.length" class="v2-setup-evidence">
        <span class="v2-summary-label">{{ t('v2.setup.connect.installSteps') }}</span>
        <ol class="v2-setup-steps-list">
          <li v-for="(line, index) in installSteps" :key="index">{{ line }}</li>
        </ol>
      </div>
    </template>

    <div class="v2-save-bar">
      <button type="button" class="btn btn-sm" data-testid="setup-connect-refresh" @click="emit('refresh')">
        {{ t('v2.setup.refreshPlan') }}
      </button>
    </div>
  </div>
</template>
