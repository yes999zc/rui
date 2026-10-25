<script setup lang="ts">
/**
 * Device identity card (M1 content, unchanged): id/room/model/vendor/plugin/
 * transport/host/port/enabled/status/last seen/latency/wake words/volume target
 * and the device warnings. Every value comes from `GET /devices/{id}`; no model
 * name is inspected.
 */
import { computed } from 'vue'
import AppIcon from '../../../components/AppIcon.vue'
import type { DeviceDetail, PluginInfo } from '../../api'
import {
  formatDateTime,
  relativeTime,
} from '../../format'
import {
  statusReasonText,
  t,
  transportLabel,
  warningKnown,
  warningTitle,
} from '../../i18n'
import StatusBadge from '../StatusBadge.vue'

const props = defineProps<{
  detail: DeviceDetail
  plugin: PluginInfo | null
  pluginMissing: boolean
}>()

const warningRows = computed(() => props.detail.warnings ?? [])

function boolText(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return t('v2.common.na')
  return value ? t('v2.common.yes') : t('v2.common.no')
}

function connectedText(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return t('v2.detail.connectedNone')
  return value ? t('v2.common.yes') : t('v2.common.no')
}

function pluginSummary(detail: DeviceDetail): string {
  const info = props.plugin
  if (!info) return detail.plugin_id
  return info.version ? `${info.name} · v${info.version}` : info.name
}
</script>

<template>
  <section class="v2-card">
    <h2 class="v2-card-title">{{ t('v2.detail.identity') }}</h2>
    <dl class="v2-kv">
      <div>
        <dt>{{ t('v2.detail.id') }}</dt>
        <dd class="mono">{{ detail.id }}</dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.room') }}</dt>
        <dd>{{ detail.room || t('v2.devices.unassigned') }}</dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.model') }}</dt>
        <dd>{{ detail.model || t('v2.common.na') }}</dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.vendor') }}</dt>
        <dd>{{ detail.vendor }}</dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.plugin') }}</dt>
        <dd>
          <span>{{ pluginSummary(detail) }}</span>
          <span v-if="plugin && !plugin.available" class="tag is-warn">
            {{ t('v2.detail.pluginUnavailable') }}
          </span>
          <span class="meta mono v2-block">{{ detail.plugin_id }}</span>
          <span v-if="pluginMissing" class="note">{{ t('v2.detail.pluginUnknown') }}</span>
        </dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.transport') }}</dt>
        <dd>{{ transportLabel(detail.transport) }}</dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.host') }}</dt>
        <dd class="mono">{{ detail.host }}</dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.listenPort') }}</dt>
        <dd class="mono">{{ detail.listen_port ?? t('v2.common.na') }}</dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.enabled') }}</dt>
        <dd>{{ boolText(detail.enabled) }}</dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.status') }}</dt>
        <dd>
          <StatusBadge :status="detail.status" :reason="detail.status_reason" />
          <span class="meta v2-block">{{ statusReasonText(detail.status_reason) }}</span>
        </dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.connected') }}</dt>
        <dd>{{ connectedText(detail.connected) }}</dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.lastSeen') }}</dt>
        <dd>
          <span v-if="detail.last_seen" class="mono" :title="formatDateTime(detail.last_seen)">
            {{ relativeTime(detail.last_seen) }}
          </span>
          <span v-else>{{ t('v2.common.na') }}</span>
        </dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.latency') }}</dt>
        <dd class="mono">
          {{
            detail.latency_ms === null
              ? t('v2.common.na')
              : t('v2.detail.latencyValue', { n: detail.latency_ms })
          }}
        </dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.wakeWords') }}</dt>
        <dd>
          <template v-if="detail.wake_words?.length">
            <span v-for="word in detail.wake_words" :key="word" class="tag">{{ word }}</span>
          </template>
          <span v-else>{{ t('v2.common.na') }}</span>
        </dd>
      </div>
      <div>
        <dt>{{ t('v2.detail.volumeTarget') }}</dt>
        <dd class="mono">
          {{
            detail.volume_target === null || detail.volume_target === undefined
              ? t('v2.common.na')
              : t('v2.detail.volumeUnit', { n: detail.volume_target })
          }}
        </dd>
      </div>
    </dl>
    <div v-if="warningRows.length" class="v2-identity-warn">
      <div class="v2-section-label">
        <AppIcon name="alert" :size="15" />
        {{ t('v2.warning.device') }}
      </div>
      <ul class="v2-warning-list">
        <li v-for="(warning, index) in warningRows" :key="`${warning.code}-${index}`">
          <span class="dot warn" aria-hidden="true" />
          <strong>{{ warningTitle(warning.code, warning.message) }}</strong>
          <span class="tag mono">{{ warning.code }}</span>
          <span v-if="warning.params?.length" class="tag mono">
            {{ warning.params.join(', ') }}
          </span>
          <span v-if="!warningKnown(warning.code) && warning.hint" class="note">
            {{ warning.hint }}
          </span>
        </li>
      </ul>
    </div>
  </section>
</template>
