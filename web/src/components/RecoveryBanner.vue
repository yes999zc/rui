<script setup lang="ts">
/**
 * Minimal recovery banner: the config failed validation at startup and the service is
 * running from last-good or schema defaults. This is deliberately explicit — it never
 * auto-repairs, and restoring always asks before writing.
 */
import { onMounted, ref } from 'vue'
import { api, type ConfigRecovery } from '../api'
import { t } from '../i18n'
import AppIcon from './AppIcon.vue'

const recovery = ref<ConfigRecovery | null>(null)
const busy = ref<'' | 'last-good' | 'defaults'>('')
const message = ref('')
const error = ref('')
const showDetails = ref(false)

async function load(): Promise<void> {
  try {
    const state = await api.configRecovery()
    recovery.value = state.active ? state : null
  } catch {
    recovery.value = null
  }
}

async function restore(mode: 'last-good' | 'defaults'): Promise<void> {
  if (busy.value) return
  if (mode === 'defaults' && !window.confirm(t('recovery.confirmDefaults'))) return
  busy.value = mode
  error.value = ''
  try {
    const result = await api.restoreConfigRecovery(mode)
    message.value = result.message
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    busy.value = ''
  }
}

async function downloadBad(): Promise<void> {
  try {
    const blob = await api.recoveryBadConfig()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'config.failed.yaml'
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  }
}

onMounted(load)
</script>

<template>
  <section v-if="recovery" class="recovery-banner" role="alert" aria-live="polite">
    <div class="recovery-head">
      <span class="recovery-icon"><AppIcon name="alert" :size="18" /></span>
      <div class="recovery-copy">
        <strong>{{ t('recovery.title') }}</strong>
        <p>
          {{ recovery.mode === 'last-good' ? t('recovery.usingLastGood') : t('recovery.usingDefaults') }}
          <span v-if="recovery.at" class="mono">{{ new Date(recovery.at * 1000).toLocaleString() }}</span>
        </p>
        <p v-if="recovery.note" class="recovery-note mono">{{ recovery.note }}</p>
        <p v-if="message" class="recovery-ok">{{ message }} — {{ t('recovery.restartHint') }}</p>
        <p v-if="error" class="recovery-error">{{ error }}</p>
      </div>
      <button type="button" class="btn-icon recovery-chevron" :aria-expanded="showDetails"
              :aria-label="t('recovery.details')" @click="showDetails = !showDetails">
        <AppIcon :name="showDetails ? 'chevron-up' : 'chevron-down'" :size="16" />
      </button>
    </div>
    <div class="recovery-actions">
      <button type="button" class="btn btn-primary" :disabled="!recovery.last_good_exists || busy !== ''"
              @click="restore('last-good')">
        {{ busy === 'last-good' ? t('recovery.restoring') : t('recovery.restoreLastGood') }}
      </button>
      <button type="button" class="btn" :disabled="busy !== ''" @click="restore('defaults')">
        {{ busy === 'defaults' ? t('recovery.restoring') : t('recovery.restoreDefaults') }}
      </button>
      <button v-if="recovery.bad_path" type="button" class="btn" @click="downloadBad">
        {{ t('recovery.downloadBad') }}
      </button>
      <span class="recovery-hint">{{ t('recovery.restartHint') }}</span>
    </div>
    <div v-if="showDetails" class="recovery-details mono">
      <p>{{ recovery.config_path }}</p>
      <p v-if="recovery.last_good_path">{{ recovery.last_good_path }}</p>
      <p v-if="recovery.bad_path">{{ recovery.bad_path }}</p>
    </div>
  </section>
</template>

<style scoped>
.recovery-banner {
  margin: 16px clamp(16px, 3vw, 36px) 0;
  border: 1px solid var(--warn, #b26a00);
  background: color-mix(in srgb, var(--warn, #b26a00) 8%, var(--surface, #fff));
  border-radius: 12px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.recovery-head { display: flex; gap: 10px; align-items: flex-start; }
.recovery-icon { color: var(--warn, #b26a00); flex: none; margin-top: 1px; }
.recovery-copy { min-width: 0; flex: 1; }
.recovery-copy strong { font-size: 14px; }
.recovery-copy p { margin: 4px 0 0; font-size: 13px; line-height: 1.45; }
.recovery-note { opacity: .75; word-break: break-word; }
.recovery-ok { color: var(--ok, #1c7d4d); }
.recovery-error { color: var(--fail, #b3261e); }
.recovery-chevron { flex: none; }
.recovery-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.recovery-hint { font-size: 12px; opacity: .65; }
.recovery-details { font-size: 12px; opacity: .7; word-break: break-all; }
.recovery-details p { margin: 2px 0; }
@media (max-width: 520px) {
  .recovery-banner { margin: 10px 12px 0; padding: 12px; }
  .recovery-actions .btn { min-height: 44px; }
}
</style>
