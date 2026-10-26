<script setup lang="ts">
/**
 * Config-recovery card from `/api/v1/config/recovery`. Read-only until the
 * operator downloads the failed copy or confirms a restore; the card owns those
 * two actions and emits `changed` so the page can reload health + recovery.
 */
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../../components/AppIcon.vue'
import ConfirmDialog from '../ConfirmDialog.vue'
import ErrorNotice from '../device/ErrorNotice.vue'
import {
  api,
  type ConfigRecoveryResponse,
  type ConfigRecoveryRestoreResponse,
} from '../../api'
import { describeError, type ErrorCopy } from '../../errors'
import { t } from '../../i18n'

const props = defineProps<{
  recovery: ConfigRecoveryResponse | null
}>()

const emit = defineEmits<{ changed: [] }>()

const downloading = ref(false)
const restoreOpen = ref(false)
const restoreBusy = ref(false)
const restoreError = ref<ErrorCopy | null>(null)
const restoreDone = ref<ConfigRecoveryRestoreResponse | null>(null)
const downloadError = ref<ErrorCopy | null>(null)

const restoreMode = computed<'last-good' | 'defaults'>(() =>
  props.recovery?.last_good_exists ? 'last-good' : 'defaults',
)

const recoveryAt = computed(() => {
  const at = props.recovery?.at
  if (!at) return t('v2.common.na')
  return new Date(at * 1000).toLocaleString()
})

function basename(path: string): string {
  const parts = path.split(/[\\/]/)
  return parts[parts.length - 1] || path
}

async function downloadBadConfig(): Promise<void> {
  if (downloading.value) return
  downloading.value = true
  downloadError.value = null
  try {
    const blob = await api.recoveryBadFile()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = props.recovery?.bad_path
      ? basename(props.recovery.bad_path)
      : 'config.failed.yaml'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (err) {
    downloadError.value = describeError(err)
  } finally {
    downloading.value = false
  }
}

function requestRestore(): void {
  restoreError.value = null
  restoreDone.value = null
  restoreOpen.value = true
}

async function runRestore(): Promise<void> {
  if (restoreBusy.value) return
  restoreBusy.value = true
  restoreError.value = null
  try {
    restoreDone.value = await api.restoreConfigRecovery(restoreMode.value)
    restoreOpen.value = false
    emit('changed')
  } catch (err) {
    restoreError.value = describeError(err)
  } finally {
    restoreBusy.value = false
  }
}
</script>

<template>
  <section class="v2-card" data-testid="health-recovery">
    <header class="v2-card-head">
      <h2 class="v2-card-title">{{ t('v2.health.recovery.title') }}</h2>
      <span
        v-if="recovery"
        class="badge"
        :class="recovery.active ? 'badge-warn' : 'badge-ok'"
        data-testid="recovery-active-badge"
      >
        {{ recovery.active ? t('v2.health.recovery.active') : t('v2.health.recovery.inactive') }}
      </span>
    </header>
    <p class="v2-card-lede">{{ t('v2.health.recovery.lede') }}</p>

    <template v-if="recovery">
      <dl class="v2-kv">
        <div>
          <dt>{{ t('v2.health.recovery.modeLabel') }}</dt>
          <dd>{{ recovery.mode ? t('v2.health.recovery.mode', { mode: recovery.mode }) : t('v2.common.na') }}</dd>
        </div>
        <div>
          <dt>{{ t('v2.health.recovery.at') }}</dt>
          <dd>{{ recoveryAt }}</dd>
        </div>
        <div>
          <dt>{{ t('v2.health.recovery.badPath') }}</dt>
          <dd class="mono v2-break">{{ recovery.bad_path || t('v2.common.na') }}</dd>
        </div>
        <div>
          <dt>{{ t('v2.health.recovery.lastGood') }}</dt>
          <dd class="mono v2-break">{{ recovery.last_good_path || t('v2.common.na') }}</dd>
        </div>
        <div>
          <dt>{{ t('v2.health.recovery.lastGoodExists') }}</dt>
          <dd>{{ recovery.last_good_exists ? t('v2.common.yes') : t('v2.common.no') }}</dd>
        </div>
        <div>
          <dt>{{ t('v2.health.recovery.configPath') }}</dt>
          <dd class="mono v2-break">{{ recovery.config_path || t('v2.common.na') }}</dd>
        </div>
      </dl>
      <p v-if="recovery.note" class="note mono v2-break">{{ recovery.note }}</p>

      <div class="v2-health-actions">
        <button
          v-if="recovery.active && recovery.bad_path"
          type="button"
          class="btn btn-sm"
          :disabled="downloading"
          data-testid="recovery-download"
          @click="downloadBadConfig"
        >
          <AppIcon name="download" :size="14" />
          {{ downloading ? t('v2.common.loading') : t('v2.health.recovery.download') }}
        </button>
        <button
          v-if="recovery.active"
          type="button"
          class="btn btn-sm"
          :disabled="restoreBusy"
          data-testid="recovery-restore"
          @click="requestRestore"
        >
          <AppIcon name="wrench" :size="14" />
          {{ t('v2.health.recovery.restore') }}
        </button>
        <RouterLink v-if="!recovery.active" class="btn btn-sm" to="/voice-assistant">
          {{ t('v2.health.recovery.openConfig') }}
        </RouterLink>
      </div>

      <ErrorNotice :error="downloadError" data-testid="recovery-download-error" />
      <ErrorNotice :error="restoreError" data-testid="recovery-restore-error" />

      <div
        v-if="restoreDone"
        class="banner banner-ok v2-banner"
        role="status"
        data-testid="recovery-restore-done"
      >
        <AppIcon name="check" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ restoreDone.message }}</strong>
          <p class="note">{{ t('v2.health.recovery.restartRequired') }}</p>
        </div>
      </div>
    </template>
    <p v-else class="note">{{ t('v2.health.recovery.unavailable') }}</p>

    <ConfirmDialog
      v-if="restoreOpen"
      :title="t('v2.health.recovery.restoreTitle')"
      :confirm-label="t('v2.health.recovery.restoreConfirm')"
      :cancel-label="t('v2.common.cancel')"
      tone="danger"
      :busy="restoreBusy"
      :busy-label="t('v2.common.saving')"
      data-testid="recovery-restore-dialog"
      @confirm="runRestore"
      @cancel="restoreOpen = false"
    >
      <p class="v2-confirm-lead">
        {{ restoreMode === 'last-good'
          ? t('v2.health.recovery.restoreLastGoodBody')
          : t('v2.health.recovery.restoreDefaultsBody') }}
      </p>
      <p class="note">{{ t('v2.health.recovery.restoreHint') }}</p>
    </ConfirmDialog>
  </section>
</template>
