<script setup lang="ts">
/**
 * Danger zone (M3): disable-vs-delete explanation and irreversible deletion.
 *
 * `DELETE /devices/{id}` requires `?confirm=<id>` (contract §7.14). The second
 * confirmation dialog therefore contains the only editable confirm field: the
 * submit button stays disabled until the typed id matches exactly. Deletion
 * still rebuilds the fleet, and deleting the last enabled device stops the
 * audio plane — both are stated before the request.
 */
import { computed, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import AppIcon from '../../../components/AppIcon.vue'
import { api, type DeviceDetail, type HouseInfo } from '../../api'
import { describeError, type ErrorCopy } from '../../errors'
import { t } from '../../i18n'
import ConfirmDialog from '../ConfirmDialog.vue'
import ErrorNotice from './ErrorNotice.vue'

const props = defineProps<{
  detail: DeviceDetail
  house: HouseInfo | null
  featureEnabled: boolean
}>()

const route = useRoute()

const emit = defineEmits<{ deleted: []; refresh: [] }>()

const confirmOpen = ref(false)
const typed = ref('')
const deleting = ref(false)
const errorCopy = ref<ErrorCopy | null>(null)

/**
 * Only claim "this is the last enabled device" when the house counters are
 * actually loaded and say exactly one device is enabled. `house` is loaded
 * independently and may be null (still loading or failed); treating that as
 * "0 enabled" used to show the warning for every device in a multi-device home.
 */
const lastEnabled = computed(
  () => props.house !== null && props.house.devices.enabled === 1 && props.detail.enabled,
)
const canDelete = computed(
  () => props.featureEnabled && !deleting.value && typed.value === props.detail.id,
)

function openConfirm(): void {
  if (!props.featureEnabled || deleting.value) return
  errorCopy.value = null
  typed.value = ''
  confirmOpen.value = true
}

function closeConfirm(): void {
  if (deleting.value) return
  confirmOpen.value = false
}

async function submit(): Promise<void> {
  if (!canDelete.value) return
  deleting.value = true
  errorCopy.value = null
  try {
    await api.deleteDevice(props.detail.id, typed.value)
    confirmOpen.value = false
    emit('deleted')
  } catch (err) {
    errorCopy.value = describeError(err)
    confirmOpen.value = false
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <div class="v2-detail-stack">
    <section class="v2-card" data-testid="danger-zone">
      <div class="v2-card-head">
        <div>
          <h2 class="v2-card-title">{{ t('v2.danger.title') }}</h2>
          <p class="v2-card-lede">{{ t('v2.danger.lede') }}</p>
        </div>
        <span class="badge badge-warn">{{ detail.id }}</span>
      </div>

      <div class="v2-danger-explain">
        <AppIcon name="alert" :size="18" />
        <div>
          <h3>{{ t('v2.danger.disableTitle') }}</h3>
          <p class="note">{{ t('v2.danger.disableHint') }}</p>
          <RouterLink class="btn btn-sm" :to="{ query: { ...route.query, tab: 'config' } }">
            {{ t('v2.danger.openConfig') }}
          </RouterLink>
        </div>
      </div>

      <div class="v2-danger-delete">
        <h3>{{ t('v2.danger.deleteTitle') }}</h3>
        <p class="note">{{ t('v2.danger.deleteHint') }}</p>
        <p class="note">{{ t('v2.danger.lastEnabledGeneric') }}</p>
        <p
          v-if="lastEnabled"
          class="banner banner-warn v2-banner v2-danger-last"
          data-testid="last-enabled-warning"
        >
          <AppIcon name="alert" :size="17" />
          <span>{{ t('v2.danger.lastEnabled') }}</span>
        </p>
        <div v-if="!featureEnabled" class="banner banner-warn v2-banner" role="status">
          <AppIcon name="lock" :size="17" />
          <span>{{ t('v2.danger.featureOff') }}</span>
        </div>
        <button
          type="button"
          class="btn btn-danger"
          data-testid="delete-open"
          :disabled="!featureEnabled || deleting"
          @click="openConfirm"
        >
          <AppIcon name="trash" :size="15" />
          {{ t('v2.danger.deleteAction') }}
        </button>
      </div>
    </section>

    <ErrorNotice :error="errorCopy" show-retry @retry="emit('refresh')" />

    <ConfirmDialog
      v-if="confirmOpen"
      :title="t('v2.danger.confirmTitle')"
      :confirm-label="t('v2.danger.confirmAction')"
      :cancel-label="t('v2.danger.cancel')"
      :busy="deleting"
      :busy-label="t('v2.danger.deleting')"
      :confirm-disabled="!canDelete"
      tone="danger"
      @confirm="submit"
      @cancel="closeConfirm"
    >
      <p class="note v2-confirm-lead">
        {{ t('v2.danger.confirmBody', { id: detail.id }) }}
      </p>
      <ul class="v2-confirm-changes">
        <li>{{ t('v2.danger.confirmImpact') }}</li>
        <li>{{ t('v2.danger.confirmRestart') }}</li>
        <li v-if="lastEnabled">{{ t('v2.danger.lastEnabled') }}</li>
      </ul>
      <div class="v2-danger-typed">
        <label class="field-label" for="danger-confirm-id">
          {{ t('v2.danger.typeToConfirm', { id: detail.id }) }}
        </label>
        <input
          id="danger-confirm-id"
          v-model="typed"
          type="text"
          class="input mono"
          data-testid="delete-id-input"
          autocomplete="off"
          spellcheck="false"
          :placeholder="t('v2.danger.inputPlaceholder', { id: detail.id })"
          :disabled="deleting"
        />
        <p v-if="typed && typed !== detail.id" class="field-error" data-testid="delete-mismatch">
          {{ t('v2.danger.mismatch') }}
        </p>
      </div>
    </ConfirmDialog>
  </div>
</template>
