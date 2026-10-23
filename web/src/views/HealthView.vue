<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { api, type AudioStatus, type DoctorReport } from '../api'
import { formatCount, t, type MsgKey } from '../i18n'

const report = ref<DoctorReport | null>(null)
const audio = ref<AudioStatus | null>(null)
const busy = ref(false)
const error = ref('')

/**
 * The buffer lead is only meaningful while something is playing: the plane reports 0
 * when idle, so a bare `0 < 150` test would paint an idle speaker as a fault. Warn
 * only when audio is actually flowing and the lead has collapsed below ~half target.
 */
const leadThin = computed(() => {
  const metrics = audio.value?.metrics
  return !!metrics && metrics.playback_lead_ms > 0 && metrics.playback_lead_ms < 150
})

const VERDICTS: Record<string, MsgKey> = {
  ok: 'health.verdictOk',
  degraded: 'health.verdictDegraded',
  unsupported: 'health.verdictUnsupported',
}

function verdictText(verdict: string): string {
  const key = VERDICTS[verdict]
  return key ? t(key) : verdict
}

async function run() {
  busy.value = true
  error.value = ''
  try {
    report.value = await api.doctor()
    audio.value = await api.audio()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    busy.value = false
  }
}

onMounted(run)
</script>

<template>
  <header class="page-head">
    <div>
      <div class="kicker">{{ t('health.kicker') }}</div>
      <h1>{{ t('health.title') }}</h1>
      <p class="lede">{{ t('health.lede') }}</p>
    </div>
    <div class="page-head-side">
      <button type="button" class="btn btn-primary" :disabled="busy" @click="run">
        <AppIcon name="refresh" :size="14" />
        {{ busy ? t('health.running') : t('health.run') }}
      </button>
    </div>
  </header>

  <div v-if="error" class="banner banner-fail" role="alert">
    <AppIcon name="alert" :size="16" class="banner-icon" />
    <span>{{ t('health.error', { message: error }) }}</span>
  </div>

  <section v-if="report" class="health-summary">
    <div class="health-cell">
      <span class="health-k">{{ t('health.verdict') }}</span>
      <span class="health-v verdict" :class="report.verdict === 'ok' ? 'ok' : report.verdict === 'degraded' ? 'warn' : 'fail'">
        {{ verdictText(report.verdict) }}
      </span>
    </div>
    <div class="health-cell">
      <span class="health-k">{{ t('health.aecRtf') }}</span>
      <span class="health-v mono">{{ report.aec_rtf }} <small class="muted">· {{ report.cpu_share }}%</small></span>
    </div>
    <div class="health-cell">
      <span class="health-k">{{ t('health.capacity') }}</span>
      <span class="health-v mono">~{{ report.device_capacity }} <small class="muted">{{ t('health.devicesUnit') }}</small></span>
      <span class="health-basis mono">{{ report.capacity_basis }}</span>
    </div>
    <div class="health-cell">
      <span class="health-k">{{ t('health.measuredAt') }}</span>
      <span class="health-v mono measured">{{ report.measured_at }}</span>
    </div>
  </section>

  <section v-if="audio" class="block">
    <div class="block-head">
      <h2>{{ t('health.audioPlane') }}</h2>
      <span class="badge" :class="audio.running ? 'badge-ok' : 'badge-warn'">
        {{ audio.running ? t('health.runningPid', { pid: audio.pid ?? '—' }) : audio.error || t('health.notStarted') }}
      </span>
    </div>

    <template v-if="audio.metrics">
      <div class="row">
        <span class="k">{{ t('health.kws') }}</span>
        <span class="v" :class="audio.metrics.kws_enabled ? 'ok' : 'fail'">
          {{ audio.metrics.kws_enabled ? t('health.kwsLoaded') : t('health.kwsMissing') }}
          <span v-if="!audio.metrics.kws_enabled && audio.metrics.kws_error" class="tag">{{ audio.metrics.kws_error }}</span>
        </span>
      </div>
      <div class="row">
        <span class="k">{{ t('health.utterances') }}</span>
        <span class="v">{{ formatCount(audio.metrics.utterances) }} <span class="tag">{{ t('health.utterancesTag') }}</span></span>
      </div>
      <div class="row">
        <span class="k">{{ t('health.gateDiscarded') }}</span>
        <span class="v">{{ formatCount(audio.metrics.gate_discarded) }} <span class="tag">{{ t('health.gateTag') }}</span></span>
      </div>
      <div class="row">
        <span class="k">{{ t('health.ackSkipped') }}</span>
        <span class="v">{{ formatCount(audio.metrics.ack_skipped) }} <span class="tag">{{ t('health.ackTag') }}</span></span>
      </div>
      <div class="row">
        <span class="k">{{ t('health.lead') }}</span>
        <span class="v" :class="leadThin ? 'warn' : ''">
          {{ audio.metrics.playback_lead_ms }} ms
          <span v-if="leadThin" class="tag">{{ t('health.leadLow') }}</span>
          <span v-else-if="audio.metrics.playback_lead_ms === 0" class="tag">{{ t('health.leadIdle') }}</span>
        </span>
      </div>
      <div class="row">
        <span class="k">{{ t('health.frames') }}</span>
        <span class="v mono">{{ audio.metrics.frames_in }} / {{ audio.metrics.frames_out }} / {{ audio.metrics.frames_dropped }}</span>
      </div>
      <div class="row">
        <span class="k">{{ t('health.jitter') }}</span>
        <span class="v mono">{{ audio.metrics.jitter_p50_ms }} / {{ audio.metrics.jitter_p99_ms }} ms</span>
      </div>
      <div class="row">
        <span class="k">{{ t('health.rtf') }}</span>
        <span class="v mono" :class="audio.metrics.rtf <= 0.5 ? 'ok' : 'warn'">{{ audio.metrics.rtf }}</span>
      </div>
      <div class="row">
        <span class="k">{{ t('health.audioProcessed') }}</span>
        <span class="v mono">{{ audio.metrics.audio_s }} s</span>
      </div>
      <div class="row">
        <span class="k">{{ t('health.aec') }}</span>
        <span class="v" :class="audio.metrics.aec_enabled ? 'ok' : 'warn'">
          {{ audio.metrics.aec_enabled ? t('health.aecOn', { channel: audio.metrics.aec_channel }) : t('health.aecOff') }}
          <span v-if="audio.metrics.aec_erle_db !== null" class="tag">{{ t('health.erle', { db: audio.metrics.aec_erle_db }) }}</span>
          <span v-else class="tag">{{ t('health.erleNoRef') }}</span>
        </span>
      </div>
      <div class="row">
        <span class="k">{{ t('health.ref') }}</span>
        <span class="v" :class="audio.metrics.ref_active ? 'ok' : 'warn'">
          {{ audio.metrics.ref_active ? t('health.refActive') : t('health.refSilent') }}
        </span>
      </div>
      <div class="row">
        <span class="k">{{ t('health.speaker') }}</span>
        <span class="v" :class="audio.metrics.speaker_connected ? 'ok' : 'warn'">
          {{ audio.metrics.speaker_connected ? t('health.speakerConnected') : t('health.speakerMissing') }}
        </span>
      </div>
      <p class="field-hint" style="margin-top: var(--s3)">{{ t('health.leadNote') }}</p>
    </template>
  </section>

  <section v-if="report" class="block">
    <div class="block-head">
      <h2>{{ t('health.checks') }}</h2>
      <span class="meta">{{ report.checks.length }}</span>
    </div>
    <div v-for="check in report.checks" :key="check.name" class="row">
      <span class="k mono">{{ check.name }}</span>
      <span class="v" :class="check.status">{{ check.value }}</span>
      <span v-if="check.hint" class="tag">{{ check.hint }}</span>
    </div>
  </section>
</template>

<style scoped>
.health-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border-top: 1px solid var(--line-strong);
  border-bottom: 1px solid var(--line);
  margin-bottom: var(--s6);
}

.health-cell {
  padding: 14px 16px;
  border-left: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.health-cell:first-child {
  border-left: 0;
  padding-left: 0;
}

.health-k {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  color: var(--muted);
}

.health-v {
  font-family: var(--font-mono);
  font-size: 20px;
  line-height: 1.2;
  letter-spacing: -0.02em;
  overflow-wrap: anywhere;
}

.health-v.verdict {
  font-family: var(--font-display);
  font-size: 24px;
  letter-spacing: -0.01em;
}

.health-v.measured {
  font-size: 12px;
  color: var(--ink-2);
}

.health-basis {
  font-size: 10.5px;
  color: var(--muted);
  overflow-wrap: anywhere;
}

@media (max-width: 960px) {
  .health-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .health-cell:nth-child(3) {
    border-left: 0;
    padding-left: 0;
  }
}

@media (max-width: 720px) {
  .health-summary {
    grid-template-columns: minmax(0, 1fr);
  }

  .health-cell {
    border-left: 0;
    padding-left: 0;
    border-top: 1px solid var(--line);
    padding-top: 10px;
  }

  .health-cell:first-child {
    border-top: 0;
  }
}
</style>
