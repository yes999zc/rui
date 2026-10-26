<script setup lang="ts">
/**
 * `/v2/health` (M9b) — global health, dependency probes and config recovery.
 *
 * Three independent reads: `GET /health` (unauthenticated process/audio view),
 * `GET /api/v1/doctor` (measured/declared checks) and
 * `GET /api/v1/config/recovery`. They are merged into five sections and an
 * exceptions-first list; a failing probe never hides the sections that did load.
 *
 * The page is read-only except for two explicit actions: downloading the failed
 * config copy and restoring a fallback after a confirm dialog. Nothing repairs
 * itself silently.
 */
import { computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import StateBlock from '../components/StateBlock.vue'
import HealthAudioTable from '../components/health/HealthAudioTable.vue'
import HealthRecoveryCard from '../components/health/HealthRecoveryCard.vue'
import {
  api,
  type DoctorCheck,
  type HealthAudioDevice,
  type HealthWarning,
} from '../api'
import { formatUptime, severityRank } from '../config'
import { describeError } from '../errors'
import { audioSeverity, speakerText, warningDevice, warningIdentity } from '../health'
import { doctorCheckLabel, healthStatusLabel, t, warningTitle } from '../i18n'
import { useResource } from '../useResource'

const healthResource = useResource(() => api.health())
const doctorResource = useResource(() => api.doctor())
const recoveryResource = useResource(() => api.configRecovery())
const { data: health, error: healthError, loading: healthLoading } = healthResource
const { data: doctor, error: doctorError, loading: doctorLoading } = doctorResource
const { data: recovery, error: recoveryError, loading: recoveryLoading } = recoveryResource

const healthErrorCopy = computed(() =>
  healthError.value ? describeError(healthError.value) : null,
)
const doctorErrorCopy = computed(() =>
  doctorError.value ? describeError(doctorError.value) : null,
)
const recoveryErrorCopy = computed(() =>
  recoveryError.value ? describeError(recoveryError.value) : null,
)
const initialLoading = computed(
  () =>
    !health.value &&
    !doctor.value &&
    !recovery.value &&
    (healthLoading.value || doctorLoading.value || recoveryLoading.value),
)
const fatal = computed(
  () =>
    !health.value &&
    !doctor.value &&
    !recovery.value &&
    Boolean(healthErrorCopy.value ?? doctorErrorCopy.value ?? recoveryErrorCopy.value),
)

interface AttentionItem {
  id: string
  severity: 'fail' | 'warn'
  title: string
  detail: string
  hint: string
  to?: string
}

function warningSeverity(code: string): 'fail' | 'warn' {
  return ['fleet_start_failed', 'over_max_planes', 'plane_not_running', 'port_conflict'].includes(code)
    ? 'fail'
    : 'warn'
}

const attentionItems = computed<AttentionItem[]>(() => {
  const items: AttentionItem[] = []
  const seen = new Set<string>()
  const push = (item: AttentionItem) => {
    if (seen.has(item.id)) return
    seen.add(item.id)
    items.push(item)
  }

  if (health.value?.status && health.value.status !== 'ok') {
    push({
      id: 'health:status',
      severity: health.value.status === 'degraded' ? 'warn' : 'fail',
      title: t('v2.health.attention.serviceStatus'),
      detail: healthStatusLabel(health.value.status),
      hint: '',
    })
  }
  if (health.value && health.value.device_errors > 0) {
    push({
      id: 'health:device-errors',
      severity: 'fail',
      title: t('v2.health.attention.deviceErrors', { n: health.value.device_errors }),
      detail: '',
      hint: t('v2.health.attention.deviceErrorsHint'),
    })
  }
  for (const warning of health.value?.warnings ?? []) {
    push(warningItem(warning))
  }
  for (const row of health.value?.audio_devices ?? []) {
    const severity = audioSeverity(row)
    if (!severity) continue
    push({
      id: `health:plane:${row.id}`,
      severity,
      title: t('v2.health.attention.plane', { id: row.id }),
      detail: row.error || (row.speaker_connected === false
        ? t('v2.health.attention.speakerDisconnected')
        : t('v2.health.attention.planeStopped')),
      hint: row.restarts > 0 ? t('v2.health.attention.restarts', { n: row.restarts }) : '',
      to: row.id ? `/devices/${encodeURIComponent(row.id)}` : undefined,
    })
  }
  if (health.value?.multi_device.error) {
    push({
      id: 'health:fleet',
      severity: 'fail',
      title: t('v2.health.attention.fleet'),
      detail: health.value.multi_device.error,
      hint: '',
    })
  }
  if (health.value?.multi_device.refused?.length) {
    push({
      id: 'health:refused',
      severity: 'fail',
      title: t('v2.health.attention.refused'),
      detail: health.value.multi_device.refused.join(', '),
      hint: t('v2.health.attention.refusedHint'),
    })
  }
  for (const check of doctor.value?.checks ?? []) {
    if (check.status !== 'warn' && check.status !== 'fail') continue
    push({
      id: `health:doctor:${check.name}`,
      severity: check.status === 'fail' ? 'fail' : 'warn',
      title: doctorCheckLabel(check.name),
      detail: check.value,
      hint: check.hint,
    })
  }
  if (recovery.value?.active) {
    push({
      id: 'health:recovery',
      severity: 'warn',
      title: t('v2.health.attention.recovery'),
      detail: t('v2.health.recovery.mode', { mode: recovery.value.mode }),
      hint: recovery.value.note,
    })
  }

  return items.sort((a, b) => severityRank(a.severity) - severityRank(b.severity))
})

function warningItem(warning: HealthWarning): AttentionItem {
  const device = warningDevice(warning)
  return {
    id: `health:warning:${warning.code}:${warningIdentity(warning)}`,
    severity: warningSeverity(warning.code),
    title: warningTitle(warning.code, warning.message || warning.code),
    detail: warning.message ?? '',
    hint: warning.hint ?? '',
    to: device ? `/devices/${encodeURIComponent(device)}` : undefined,
  }
}

const doctorChecks = computed<DoctorCheck[]>(() => {
  const checks = [...(doctor.value?.checks ?? [])]
  // Abnormal first; stable within a severity so the server's order survives.
  return checks.sort((a, b) => severityRank(a.status) - severityRank(b.status))
})

const doctorAbnormal = computed(
  () => doctorChecks.value.filter((check) => check.status !== 'ok').length,
)

const audioRows = computed<HealthAudioDevice[]>(() => {
  const rows = [...(health.value?.audio_devices ?? [])]
  return rows.sort((a, b) => severityRank(audioSeverity(a) ?? 'ok') - severityRank(audioSeverity(b) ?? 'ok'))
})

function checkSeverityLabel(status: string): string {
  if (status === 'fail') return t('v2.health.severity.fail')
  if (status === 'warn') return t('v2.health.severity.warn')
  return t('v2.health.severity.ok')
}

async function reloadAfterRestore(): Promise<void> {
  await Promise.all([recoveryResource.load(), healthResource.load()])
}

async function boot(): Promise<void> {
  // Each resource records its own error; one failed read must not blank the page.
  await Promise.all([healthResource.load(), doctorResource.load(), recoveryResource.load()])
}

onMounted(() => {
  void boot()
})
</script>

<template>
  <div class="v2-page">
    <header class="v2-page-head">
      <div class="v2-page-title">
        <span class="kicker">{{ t('v2.nav.config') }}</span>
        <h1>{{ t('v2.health.title') }}</h1>
        <p class="lede">{{ t('v2.health.lede') }}</p>
      </div>
      <div class="v2-page-actions">
        <span v-if="attentionItems.length" class="badge badge-warn" data-testid="health-attention-count">
          {{ t('v2.health.attentionCount', { n: attentionItems.length }) }}
        </span>
        <button
          type="button"
          class="btn btn-sm"
          :disabled="healthLoading || doctorLoading || recoveryLoading"
          data-testid="health-refresh"
          @click="boot()"
        >
          <AppIcon name="refresh" :size="14" />
          {{ t('v2.common.refresh') }}
        </button>
      </div>
    </header>

    <StateBlock
      v-if="initialLoading"
      kind="loading"
      :title="t('v2.common.loading')"
    />

    <StateBlock
      v-else-if="fatal"
      kind="error"
      :title="t('v2.health.loadFailed')"
      :detail="(healthErrorCopy ?? doctorErrorCopy ?? recoveryErrorCopy)?.detail"
      :hint="t('v2.health.loadFailedHint')"
      :status="(healthErrorCopy ?? doctorErrorCopy ?? recoveryErrorCopy)?.status"
      show-retry
      @retry="boot()"
    />

    <template v-else>
      <!-- Exceptions first: this section precedes every detail section. -->
      <section class="v2-health-attention" data-testid="health-attention" aria-labelledby="v2-health-attention-title">
        <header class="v2-card-head">
          <h2 id="v2-health-attention-title" class="v2-card-title">
            <AppIcon name="alert" :size="16" />
            {{ t('v2.health.attentionTitle') }}
          </h2>
          <span class="meta">{{ t('v2.common.items', { n: attentionItems.length }) }}</span>
        </header>
        <ul v-if="attentionItems.length" class="v2-attention-rows">
          <li
            v-for="item in attentionItems"
            :key="item.id"
            class="v2-health-attention-row"
            :class="`is-${item.severity}`"
            :data-testid="'health-attention-row'"
            :data-severity="item.severity"
          >
            <span class="v2-health-attention-dot" aria-hidden="true" />
            <div class="v2-health-attention-body">
              <strong>{{ item.title }}</strong>
              <p v-if="item.detail" class="note mono v2-break">{{ item.detail }}</p>
              <p v-if="item.hint" class="note">{{ item.hint }}</p>
            </div>
            <RouterLink v-if="item.to" class="btn btn-sm" :to="item.to">
              {{ t('v2.health.openDevice') }}
            </RouterLink>
          </li>
        </ul>
        <p v-else class="v2-health-clear" data-testid="health-attention-empty">
          <AppIcon name="check" :size="16" />
          {{ t('v2.health.attentionEmpty') }}
        </p>
      </section>

      <div
        v-if="healthErrorCopy"
        class="banner banner-warn v2-banner"
        role="status"
        data-testid="health-service-fallback"
      >
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.health.healthUnavailable') }}</strong>
          <p class="note">{{ healthErrorCopy.hint }}</p>
        </div>
      </div>
      <div
        v-if="doctorErrorCopy"
        class="banner banner-warn v2-banner"
        role="status"
        data-testid="health-doctor-fallback"
      >
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.health.doctorUnavailable') }}</strong>
          <p class="note">{{ doctorErrorCopy.hint }}</p>
        </div>
      </div>
      <div
        v-if="recoveryErrorCopy"
        class="banner banner-warn v2-banner"
        role="status"
        data-testid="health-recovery-fallback"
      >
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.health.recoveryUnavailable') }}</strong>
          <p class="note">{{ recoveryErrorCopy.hint }}</p>
        </div>
      </div>

      <section v-if="health" class="v2-card" data-testid="health-service">
        <header class="v2-card-head">
          <h2 class="v2-card-title">{{ t('v2.health.service.title') }}</h2>
          <span
            class="badge"
            :class="health.status === 'ok' ? 'badge-ok' : 'badge-fail'"
            data-testid="health-status-badge"
          >
            {{ healthStatusLabel(health.status) }}
          </span>
        </header>
        <dl class="v2-kv">
          <div>
            <dt>{{ t('v2.health.service.version') }}</dt>
            <dd class="mono">{{ health.version }}</dd>
          </div>
          <div>
            <dt>{{ t('v2.health.service.configVersion') }}</dt>
            <dd class="mono">
              {{ health.config_version }} / {{ health.config_version_supported }}
            </dd>
          </div>
          <div>
            <dt>{{ t('v2.health.service.uptime') }}</dt>
            <dd>{{ formatUptime(health.uptime_s) }}</dd>
          </div>
          <div>
            <dt>{{ t('v2.health.service.devices') }}</dt>
            <dd>
              {{ health.devices }}
              <span class="meta">· {{ t('v2.health.service.enabledDevices', { n: health.enabled_devices }) }}</span>
            </dd>
          </div>
          <div>
            <dt>{{ t('v2.health.service.deviceErrors') }}</dt>
            <dd>
              <span :class="health.device_errors ? 'v2-warn-text' : ''">{{ health.device_errors }}</span>
            </dd>
          </div>
          <div>
            <dt>{{ t('v2.health.service.audioPlane') }}</dt>
            <dd>
              <span class="badge" :class="health.audio_plane ? 'badge-ok' : 'badge-fail'">
                {{ health.audio_plane ? t('v2.health.plane.running') : t('v2.health.plane.stopped') }}
              </span>
            </dd>
          </div>
          <div>
            <dt>{{ t('v2.health.service.speaker') }}</dt>
            <dd>
              {{ speakerText(health.speaker_connected) }}
              <span v-if="health.speaker_device" class="mono meta">· {{ health.speaker_device }}</span>
            </dd>
          </div>
          <div>
            <dt>{{ t('v2.health.service.adapterPlugins') }}</dt>
            <dd>
              {{ health.adapter_plugins.registered.join(', ') || t('v2.common.none') }}
              <span v-if="health.adapter_plugins.warnings" class="meta">
                · {{ t('v2.health.service.adapterWarnings', { n: health.adapter_plugins.warnings }) }}
              </span>
            </dd>
          </div>
          <div>
            <dt>{{ t('v2.health.service.ack') }}</dt>
            <dd>
              <span class="mono">{{ health.ack.mode }}</span>
              <span v-if="health.ack.note" class="meta">· {{ health.ack.note }}</span>
            </dd>
          </div>
        </dl>
      </section>

      <HealthAudioTable v-if="health" :rows="audioRows" />

      <section v-if="health" class="v2-card" data-testid="health-devices">
        <header class="v2-card-head">
          <h2 class="v2-card-title">{{ t('v2.health.devices.title') }}</h2>
        </header>
        <dl class="v2-kv">
          <div>
            <dt>{{ t('v2.health.devices.total') }}</dt>
            <dd>{{ health.devices }}</dd>
          </div>
          <div>
            <dt>{{ t('v2.health.devices.enabled') }}</dt>
            <dd>{{ health.enabled_devices }}</dd>
          </div>
          <div>
            <dt>{{ t('v2.health.devices.multiDevice') }}</dt>
            <dd>{{ health.multi_device.enabled ? t('v2.common.enabled') : t('v2.common.disabled') }}</dd>
          </div>
          <div>
            <dt>{{ t('v2.health.devices.defaultOutput') }}</dt>
            <dd class="mono">{{ health.multi_device.default_output || t('v2.common.none') }}</dd>
          </div>
          <div>
            <dt>{{ t('v2.health.devices.refused') }}</dt>
            <dd>{{ health.multi_device.refused.join(', ') || t('v2.common.none') }}</dd>
          </div>
          <div>
            <dt>{{ t('v2.health.devices.fleetError') }}</dt>
            <dd class="v2-break">{{ health.multi_device.error || t('v2.common.none') }}</dd>
          </div>
        </dl>
        <div v-if="health.warnings.length" class="v2-health-warnings">
          <h3 class="v2-section-label">{{ t('v2.health.devices.warnings') }}</h3>
          <ul class="v2-field-list">
            <li v-for="warning in health.warnings" :key="`${warning.code}:${warningIdentity(warning)}`">
              <span class="mono">{{ warning.code }}</span>
              <span>{{ warning.message || warningTitle(warning.code) }}</span>
            </li>
          </ul>
        </div>
      </section>

      <section class="v2-card" data-testid="health-doctor">
        <header class="v2-card-head">
          <h2 class="v2-card-title">{{ t('v2.health.doctor.title') }}</h2>
          <span
            v-if="doctor"
            class="badge"
            :class="doctor.verdict === 'ok' ? 'badge-ok' : doctor.verdict === 'unsupported' ? 'badge-fail' : 'badge-warn'"
            data-testid="doctor-verdict"
          >
            {{ healthStatusLabel(doctor.verdict) }}
          </span>
          <span v-if="doctorAbnormal" class="meta">
            {{ t('v2.health.doctor.abnormal', { n: doctorAbnormal }) }}
          </span>
        </header>
        <template v-if="doctor">
          <dl class="v2-kv">
            <div>
              <dt>{{ t('v2.health.doctor.aecRtf') }}</dt>
              <dd class="mono">{{ doctor.aec_rtf }}</dd>
            </div>
            <div>
              <dt>{{ t('v2.health.doctor.cpuShare') }}</dt>
              <dd class="mono">{{ doctor.cpu_share }}%</dd>
            </div>
            <div>
              <dt>{{ t('v2.health.doctor.capacity') }}</dt>
              <dd>{{ t('v2.health.doctor.capacityValue', { n: doctor.device_capacity }) }}</dd>
            </div>
            <div>
              <dt>{{ t('v2.health.doctor.measuredAt') }}</dt>
              <dd class="mono">{{ doctor.measured_at || t('v2.common.na') }}</dd>
            </div>
          </dl>
          <p v-if="doctor.capacity_basis" class="note v2-health-basis">
            {{ t('v2.health.doctor.basis', { basis: doctor.capacity_basis }) }}
          </p>
          <div v-if="doctorChecks.length" class="v2-health-checks">
            <div
              v-for="check in doctorChecks"
              :key="check.name"
              class="v2-health-check"
              :class="`is-${check.status}`"
              :data-testid="'doctor-check'"
              :data-check-status="check.status"
            >
              <span class="v2-health-check-status">{{ checkSeverityLabel(check.status) }}</span>
              <div class="v2-health-check-body">
                <strong>{{ doctorCheckLabel(check.name) }}</strong>
                <p class="mono v2-break">{{ check.value }}</p>
                <p v-if="check.hint" class="note">{{ check.hint }}</p>
              </div>
            </div>
          </div>
        </template>
        <p v-else class="note">{{ t('v2.health.doctor.unavailable') }}</p>
      </section>

      <HealthRecoveryCard :recovery="recovery" @changed="reloadAfterRestore" />

      <nav class="v2-config-links" :aria-label="t('v2.config.otherPages')">
        <span class="meta">{{ t('v2.config.otherPages') }}</span>
        <RouterLink class="btn btn-sm" to="/voice-assistant">
          {{ t('v2.nav.voiceAssistant') }}
        </RouterLink>
        <RouterLink class="btn btn-sm" to="/music">{{ t('v2.nav.music') }}</RouterLink>
        <RouterLink class="btn btn-sm" to="/credentials">{{ t('v2.nav.credentials') }}</RouterLink>
      </nav>
    </template>

  </div>
</template>
