<script setup lang="ts">
/**
 * Audio provenance card (M1 content, unchanged): effective value + the layer
 * that supplied it. The standalone `/audio` endpoint falls back to the audio
 * block embedded in `GET /devices/{id}` when it fails. Source selection is
 * never inferred from value equality — the server payload decides.
 */
import { computed } from 'vue'
import AppIcon from '../../../components/AppIcon.vue'
import type { AudioLayer, DeviceAudio } from '../../api'
import type { ErrorCopy } from '../../errors'
import {
  displayValue,
  dottedValue,
  jsonText,
} from '../../format'
import { provenanceDescription, t } from '../../i18n'
import ProvenanceTag from '../ProvenanceTag.vue'
import StateBlock from '../StateBlock.vue'

const props = defineProps<{
  deviceId: string
  audio: DeviceAudio | null
  loading: boolean
  errorCopy: ErrorCopy | null
  fromFallback: boolean
  provenanceEnabled: boolean
}>()

const emit = defineEmits<{ retry: [] }>()

const SOURCE_ORDER = [
  'device-override',
  'global-config',
  'device-manifest',
  'core-default',
  'unresolved',
] as const

interface AudioRow {
  path: string
  value: unknown
  source: string
  layer: AudioLayer | null
  /** A manifest-vs-global conflict reported by the server for this path. */
  manifestConflict: boolean
  /** The device-reported probe value (undefined = the device reported nothing). */
  reported: unknown
  /** `reported` exists and disagrees with the effective value. */
  reportedConflict: boolean
  conflictMessage: string
}

const AUDIO_GROUP_ORDER = ['capture', 'voice', 'playback', 'aec']

function audioGroup(path: string): string {
  if (path === 'ack.echo_ignore_ms') return 'ack'
  return path.split('.')[0]
}

function audioRank(path: string): number {
  const group = audioGroup(path)
  if (group === 'ack') return AUDIO_GROUP_ORDER.length
  const index = AUDIO_GROUP_ORDER.indexOf(group)
  return index === -1 ? AUDIO_GROUP_ORDER.length + 1 : index
}

/** The contract keeps the override path as `voice.ack_echo_ignore_ms`. */
function normalizeOverride(path: string): string {
  return path === 'voice.ack_echo_ignore_ms' ? 'ack.echo_ignore_ms' : path
}

const manifestConflicts = computed(
  () => new Map((props.audio?.conflicts ?? []).map((conflict) => [conflict.path, conflict])),
)
const reportedConflicts = computed(
  () => new Map((props.audio?.reported_conflicts ?? []).map((conflict) => [conflict.path, conflict])),
)

const audioRows = computed<AudioRow[]>(() => {
  const data = props.audio
  if (!data) return []
  const rows: AudioRow[] = []
  for (const [path, source] of Object.entries(data.sources ?? {})) {
    const layer = data.layers?.[path] ?? null
    const reported = layer?.reported
    const reportedConflict = reported !== undefined && reported !== null
      && reported !== dottedValue(data.effective, path)
    const manifestConflict = manifestConflicts.value.has(path)
    rows.push({
      path,
      value: dottedValue(data.effective, path),
      source,
      layer,
      reported,
      manifestConflict,
      reportedConflict,
      conflictMessage: reportedConflicts.value.get(path)?.message
        ?? manifestConflicts.value.get(path)?.message
        ?? '',
    })
  }
  for (const raw of data.overrides ?? []) {
    const path = normalizeOverride(raw)
    if (rows.some((row) => row.path === path)) continue
    const layer = data.layers?.[path] ?? null
    rows.push({
      path,
      value: dottedValue(data.effective, path),
      source: 'device-override',
      layer,
      reported: layer?.reported,
      manifestConflict: false,
      reportedConflict: false,
      conflictMessage: '',
    })
  }
  return rows.sort(
    (a, b) => audioRank(a.path) - audioRank(b.path) || a.path.localeCompare(b.path),
  )
})

function rowHasConflict(row: AudioRow): boolean {
  return row.manifestConflict || row.reportedConflict
}

const hasManifestDefaults = computed(
  () => Object.keys(props.audio?.manifest_defaults ?? {}).length > 0,
)
const manifestDefaultsText = computed(() => jsonText(props.audio?.manifest_defaults ?? {}))
</script>

<template>
  <section class="v2-card">
    <h2 class="v2-card-title">{{ t('v2.audio.title') }}</h2>
    <p class="note v2-card-lede">{{ t('v2.audio.lede') }}</p>

    <StateBlock v-if="loading && !audio" kind="loading" :title="t('v2.common.loading')" />
    <StateBlock
      v-else-if="errorCopy && !audio"
      kind="error"
      :title="t('v2.error.audioUnavailable')"
      :detail="errorCopy.detail"
      :hint="t('v2.audio.unavailableHint', { id: deviceId })"
      :status="errorCopy.status"
      :request-id="errorCopy.requestId"
      show-retry
      @retry="emit('retry')"
    />
    <StateBlock
      v-else-if="!audio || audioRows.length === 0"
      kind="empty"
      :title="t('v2.audio.unavailable')"
      :hint="t('v2.audio.unavailableHint', { id: deviceId })"
    />
    <template v-else>
      <div v-if="!provenanceEnabled" class="note v2-catalog-note">
        {{ t('v2.audio.provenanceOff') }}
      </div>
      <div v-else-if="errorCopy && fromFallback" class="note v2-catalog-note">
        {{ t('v2.audio.fallback') }}
      </div>
      <div v-if="!audio.plug_resolved" class="note v2-warn-text">
        {{ t('v2.audio.plugUnresolved') }}
        <span v-if="audio.manifest_error" class="mono"> — {{ audio.manifest_error }}</span>
      </div>
      <div
        v-if="audio.conflicts && audio.conflicts.length"
        class="note v2-warn-text"
        data-testid="audio-conflicts"
      >
        <h3>
          <AppIcon name="alert" :size="16" />
          {{ t('v2.common.warning') }}
        </h3>
        <ul>
          <li v-for="conflict in audio.conflicts" :key="conflict.path" class="mono">
            {{ conflict.message }}
          </li>
        </ul>
      </div>
      <div
        v-if="audio.reported_conflicts && audio.reported_conflicts.length"
        class="note v2-warn-text"
        data-testid="audio-reported-conflicts"
      >
        <h3>
          <AppIcon name="alert" :size="16" />
          {{ t('v2.audio.reportedConflicts') }}
        </h3>
        <ul>
          <li v-for="conflict in audio.reported_conflicts" :key="conflict.path" class="mono">
            {{ conflict.message }}
          </li>
        </ul>
      </div>

      <p v-if="audio.device_reported" class="note" data-testid="audio-device-reported-source">
        {{ t('v2.audio.reportedFrom', { source: audio.device_reported.source }) }}
      </p>

      <div class="table-wrap">
        <table class="table v2-audio-table">
          <thead>
            <tr>
              <th>{{ t('v2.audio.field') }}</th>
              <th>{{ t('v2.audio.value') }}</th>
              <th>{{ t('v2.audio.source') }}</th>
              <th>{{ t('v2.audio.layer.manifest') }}</th>
              <th>{{ t('v2.audio.layer.global') }}</th>
              <th>{{ t('v2.audio.layer.reported') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in audioRows"
              :key="row.path"
              :class="{ 'is-conflict': rowHasConflict(row) }"
              :data-testid="rowHasConflict(row) ? 'audio-conflict-row' : undefined"
            >
              <td class="mono">
                {{ row.path }}
                <span v-if="rowHasConflict(row)" class="tag v2-conflict-badge">
                  {{ t('v2.audio.conflictBadge') }}
                </span>
                <details v-if="row.layer" class="v2-layers">
                  <summary>{{ t('v2.audio.moreLayers') }}</summary>
                  <dl class="v2-layer-grid">
                    <div>
                      <dt>{{ t('v2.audio.layer.core') }}</dt>
                      <dd class="mono">{{ displayValue(row.layer.core) }}</dd>
                    </div>
                    <div>
                      <dt>{{ t('v2.audio.layer.device') }}</dt>
                      <dd class="mono">{{ displayValue(row.layer.device) }}</dd>
                    </div>
                  </dl>
                </details>
              </td>
              <td class="mono">{{ displayValue(row.value) }}</td>
              <td><ProvenanceTag :source="row.source" /></td>
              <td
                class="mono"
                :title="row.manifestConflict ? row.conflictMessage : undefined"
              >
                {{ displayValue(row.layer?.manifest) }}
              </td>
              <td
                class="mono"
                :title="row.manifestConflict ? row.conflictMessage : undefined"
              >
                {{ displayValue(row.layer?.global) }}
                <span v-if="row.layer?.global_explicit !== undefined" class="tag">
                  {{
                    row.layer.global_explicit
                      ? t('v2.audio.globalExplicit')
                      : t('v2.audio.globalImplicit')
                  }}
                </span>
              </td>
              <td
                class="mono"
                :title="row.reportedConflict
                  ? row.conflictMessage
                  : (row.layer?.reported_source || undefined)"
              >
                <template v-if="row.reported !== undefined && row.reported !== null">
                  {{ displayValue(row.reported) }}
                </template>
                <span v-else class="meta">{{ t('v2.audio.notReported') }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="v2-provenance-legend">
        <p class="note">{{ t('v2.audio.legend') }}</p>
        <ul>
          <li v-for="source in SOURCE_ORDER" :key="source">
            <ProvenanceTag :source="source" />
            <span class="note">{{ provenanceDescription(source) }}</span>
          </li>
        </ul>
      </div>

      <div class="v2-audio-meta">
        <div>
          <h3>{{ t('v2.audio.overrides') }}</h3>
          <p v-if="!audio.overrides?.length" class="empty">
            {{ t('v2.audio.noOverrides') }}
          </p>
          <div v-else class="chips">
            <span v-for="path in audio.overrides" :key="path" class="chip mono">
              {{ path }}
            </span>
          </div>
        </div>
        <div>
          <h3>{{ t('v2.audio.manifestDefaults') }}</h3>
          <p v-if="!hasManifestDefaults" class="empty">
            {{ t('v2.audio.manifestDefaultsEmpty') }}
          </p>
          <pre v-else class="cmd v2-json">{{ manifestDefaultsText }}</pre>
        </div>
      </div>

      <div class="v2-restart-note" role="note">
        <AppIcon name="alert" :size="18" />
        <p>{{ t('v2.audio.restartNotice') }}</p>
      </div>
    </template>
  </section>
</template>
