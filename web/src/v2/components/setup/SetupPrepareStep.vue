<script setup lang="ts">
/**
 * `prepare` step: renders the manifest's device-side guide and records the
 * operator's confirmation. Class-B manifests also require `rollback_ack=true`;
 * the server re-checks it, the checkbox is only an early guard.
 */
import { computed, ref, watch } from 'vue'
import type { SetupStep } from '../../api'
import { boolValue } from '../../setup'
import { t } from '../../i18n'

const props = defineProps<{
  step: SetupStep
  busy: boolean
}>()

const emit = defineEmits<{
  submit: [action: string, values: Record<string, unknown>]
}>()

const confirmed = ref(false)
const rollbackAck = ref(false)
const localError = ref('')

const rollbackRequired = computed(() => boolValue(props.step.evidence?.rollback_required))
const alreadyConfirmed = computed(() => boolValue(props.step.evidence?.confirm))
const artifactNone = computed(
  () => props.step.status === 'skipped' || props.step.evidence?.artifact === 'none',
)
const links = computed(() => props.step.detail?.links ?? [])
const installSteps = computed(() => props.step.detail?.install_steps ?? [])
const rollback = computed(() => props.step.detail?.rollback ?? '')
const deviceName = computed(() => props.step.detail?.name ?? '')
const canSubmit = computed(
  () => props.step.actions.includes('manual.confirm') && !alreadyConfirmed.value,
)

watch(
  () => props.step.evidence?.confirm,
  (value) => {
    if (value === true) confirmed.value = true
  },
  { immediate: true },
)

function submit(): void {
  if (props.busy) return
  localError.value = ''
  if (!confirmed.value) return
  if (rollbackRequired.value && !rollbackAck.value) {
    localError.value = t('v2.setup.prepare.rollbackAckRequired')
    return
  }
  emit('submit', 'manual.confirm', {
    confirm: true,
    rollback_ack: rollbackRequired.value ? rollbackAck.value : false,
  })
}
</script>

<template>
  <div class="v2-setup-step-body">
    <h4 class="v2-setup-subtitle">{{ t('v2.setup.prepare.title') }}</h4>

    <p v-if="artifactNone" class="note" data-testid="setup-prepare-none">
      {{ t('v2.setup.prepare.artifactNone') }}
    </p>

    <template v-else>
      <div v-if="deviceName" class="v2-setup-evidence">
        <span class="v2-summary-label">{{ t('v2.setup.prepare.deviceSide') }}</span>
        <p>{{ deviceName }}</p>
      </div>

      <div v-if="links.length" class="v2-setup-links">
        <span class="v2-summary-label">{{ t('v2.setup.prepare.links') }}</span>
        <ul class="v2-setup-list">
          <li v-for="link in links" :key="link.url">
            <a :href="link.url" target="_blank" rel="noopener noreferrer">{{ link.label }}</a>
          </li>
        </ul>
      </div>

      <div v-if="installSteps.length" class="v2-setup-evidence">
        <span class="v2-summary-label">{{ t('v2.setup.prepare.installSteps') }}</span>
        <ol class="v2-setup-steps-list" data-testid="setup-prepare-steps">
          <li v-for="(line, index) in installSteps" :key="index">{{ line }}</li>
        </ol>
      </div>

      <div v-if="rollback" class="v2-setup-rollback" data-testid="setup-prepare-rollback">
        <span class="v2-summary-label">{{ t('v2.setup.prepare.rollback') }}</span>
        <p class="note v2-break">{{ rollback }}</p>
        <p v-if="rollbackRequired" class="note v2-setup-warn-text">
          {{ t('v2.setup.prepare.rollbackRequired') }}
        </p>
      </div>

      <p class="note">{{ t('v2.setup.prepare.hint') }}</p>

      <p v-if="alreadyConfirmed" class="v2-setup-done" data-testid="setup-prepare-confirmed">
        {{ t('v2.setup.prepare.confirmed') }}
      </p>

      <form
        v-else-if="canSubmit"
        class="v2-setup-subcard"
        data-testid="setup-prepare-form"
        @submit.prevent="submit"
      >
        <label class="v2-setup-check">
          <input v-model="confirmed" type="checkbox" :disabled="busy" data-testid="setup-prepare-confirm" />
          <span>{{ t('v2.setup.prepare.confirmLabel') }}</span>
        </label>
        <label v-if="rollbackRequired" class="v2-setup-check">
          <input
            v-model="rollbackAck"
            type="checkbox"
            :disabled="busy"
            data-testid="setup-prepare-rollback-ack"
          />
          <span>{{ t('v2.setup.prepare.rollbackAckLabel') }}</span>
        </label>
        <p v-if="localError" class="field-error" role="alert">{{ localError }}</p>
        <div class="v2-save-bar">
          <button
            type="submit"
            class="btn btn-primary btn-sm"
            data-testid="setup-prepare-submit"
            :disabled="busy || !confirmed || (rollbackRequired && !rollbackAck)"
          >
            {{ busy ? t('v2.setup.action.busy') : t('v2.setup.prepare.submit') }}
          </button>
        </div>
      </form>
    </template>
  </div>
</template>
