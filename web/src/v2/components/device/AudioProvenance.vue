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

const audioRows = computed<AudioRow[]>(() => {
  const data = props.audio
  if (!data) return []
  const rows: AudioRow[] = []
  for (const [path, source] of Object.entries(data.sources ?? {})) {
    rows.push({
      path,
      value: dottedValue(data.effective, path),
      source,
      layer: data.layers?.[path] ?? null,
    })
  }
  for (const raw of data.overrides ?? []) {
    const path = normalizeOverride(raw)
    if (rows.some((row) => row.path === path)) continue
    rows.push({
      path,
      value: dottedValue(data.effective, path),
      source: 'device-override',
      layer: data.layers?.[path] ?? null,
    })
  }
  return rows.sort(
    (a, b) => audioRank(a.path) - audioRank(b.path) || a.path.localeCompare(b.path),
  )
})

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

      <div class="table-wrap">
        <table class="table v2-audio-table">
          <thead>
            <tr>
              <th>{{ t('v2.audio.field') }}</th>
              <th>{{ t('v2.audio.value') }}</th>
              <th>{{ t('v2.audio.source') }}</th>
              <th>{{ t('v2.audio.layers') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in audioRows" :key="row.path">
              <td class="mono">{{ row.path }}</td>
              <td class="mono">{{ displayValue(row.value) }}</td>
              <td><ProvenanceTag :source="row.source" /></td>
              <td>
                <details v-if="row.layer" class="v2-layers">
                  <summary>{{ t('v2.audio.layers') }}</summary>
                  <dl class="v2-layer-grid">
                    <div>
                      <dt>{{ t('v2.audio.layer.core') }}</dt>
                      <dd class="mono">{{ displayValue(row.layer.core) }}</dd>
                    </div>
                    <div>
                      <dt>{{ t('v2.audio.layer.manifest') }}</dt>
                      <dd class="mono">{{ displayValue(row.layer.manifest) }}</dd>
                    </div>
                    <div>
                      <dt>{{ t('v2.audio.layer.global') }}</dt>
                      <dd class="mono">
                        {{ displayValue(row.layer.global) }}
                        <span v-if="row.layer.global_explicit !== undefined" class="tag">
                          {{
                            row.layer.global_explicit
                              ? t('v2.audio.globalExplicit')
                              : t('v2.audio.globalImplicit')
                          }}
                        </span>
                      </dd>
                    </div>
                    <div>
                      <dt>{{ t('v2.audio.layer.device') }}</dt>
                      <dd class="mono">{{ displayValue(row.layer.device) }}</dd>
                    </div>
                  </dl>
                </details>
                <span v-else class="meta">{{ t('v2.common.na') }}</span>
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
