<script setup lang="ts">
/**
 * `verify` step. `verify.say` really plays speech and updates the room's last
 * active position, so it is gated by an explicit second confirmation: the POST
 * is emitted only from the dialog's confirm action, never on page load/typing.
 */
import { computed, ref } from 'vue'
import type { SetupStep } from '../../api'
import { boolValue } from '../../setup'
import ConfirmDialog from '../ConfirmDialog.vue'
import { t } from '../../i18n'

const props = defineProps<{
  step: SetupStep
  busy: boolean
  /** A 429 cooldown blocks new speech requests but must never trap the dialog. */
  cooldown?: boolean
}>()

const emit = defineEmits<{
  submit: [action: string, values: Record<string, unknown>]
}>()

const text = ref('')
const dialogOpen = ref(false)
const localError = ref('')

const tested = computed(() => boolValue(props.step.evidence?.text_tested))
const canSay = computed(() => props.step.actions.includes('verify.say'))
const defaultText = computed(() => t('v2.setup.verify.defaultText'))
const effectiveText = computed(() => text.value.trim() || defaultText.value)

function openConfirm(): void {
  if (props.busy || props.cooldown || !canSay.value) return
  localError.value = ''
  if (effectiveText.value.length > 500) {
    localError.value = t('v2.setup.verify.tooLong')
    return
  }
  dialogOpen.value = true
}

function confirmSay(): void {
  if (props.busy || props.cooldown) return
  dialogOpen.value = false
  emit('submit', 'verify.say', { text: effectiveText.value })
}
</script>

<template>
  <div class="v2-setup-step-body">
    <h4 class="v2-setup-subtitle">{{ t('v2.setup.verify.title') }}</h4>
    <p class="note">{{ t('v2.setup.verify.hint') }}</p>

    <div class="banner banner-warn v2-banner" role="status" data-testid="setup-verify-warning">
      <p class="note">{{ t('v2.setup.verify.warning') }}</p>
    </div>

    <p v-if="tested" class="v2-setup-done" data-testid="setup-verify-done">
      {{ t('v2.setup.verify.done') }} · {{ t('v2.setup.verify.doneHint') }}
    </p>
    <p v-if="!step.required" class="note">{{ t('v2.setup.verify.optional') }}</p>

    <form v-if="canSay" class="v2-setup-subcard" data-testid="setup-verify-form" @submit.prevent="openConfirm">
      <div class="field-row">
        <div class="field-meta">
          <label class="field-label" for="setup-verify-text">{{ t('v2.setup.verify.textLabel') }}</label>
        </div>
        <div class="field-control">
          <textarea
            id="setup-verify-text"
            v-model="text"
            class="input"
            rows="2"
            maxlength="500"
            :placeholder="defaultText"
            :disabled="busy"
            data-testid="setup-verify-text"
          />
        </div>
      </div>
      <p v-if="localError" class="field-error" role="alert">{{ localError }}</p>
      <div class="v2-save-bar">
        <button
          type="submit"
          class="btn btn-primary btn-sm"
          data-testid="setup-verify-submit"
          :disabled="busy"
        >
          {{ busy ? t('v2.setup.verify.saying') : t('v2.setup.verify.say') }}
        </button>
        <p class="note">{{ t('v2.setup.verify.optional') }}</p>
      </div>
    </form>

    <ConfirmDialog
      v-if="dialogOpen"
      :title="t('v2.setup.verify.confirmTitle')"
      :confirm-label="t('v2.setup.verify.confirm')"
      :cancel-label="t('v2.actions.sayCancel')"
      :busy="busy"
      :busy-label="t('v2.setup.verify.saying')"
      :confirm-disabled="cooldown === true"
      data-testid="setup-verify-dialog"
      @confirm="confirmSay"
      @cancel="dialogOpen = false"
    >
      <p class="v2-confirm-lead">
        {{ t('v2.setup.verify.confirmBody', { text: effectiveText }) }}
      </p>
    </ConfirmDialog>
  </div>
</template>
