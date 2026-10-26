<script setup lang="ts">
/**
 * The catalog's `devices` group is deliberately read-only here: wake words,
 * volume and host edits live on the device pages and restart the audio plane.
 * This card is the summary + the link to the page that can actually edit them.
 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../../components/AppIcon.vue'
import type { ConfigGroupSpec } from '../../api'
import { pickLocalized, t } from '../../i18n'

const props = defineProps<{
  group: ConfigGroupSpec
  devices: Array<Record<string, unknown>>
}>()

interface DeviceRow {
  id: string
  room: string
  wakeWords: string[]
  volume: number | null
  enabled: boolean
}

const rows = computed<DeviceRow[]>(() =>
  props.devices
    .map((device) => ({
      id: String(device.id ?? ''),
      room: String(device.room ?? ''),
      wakeWords: Array.isArray(device.wake_words) ? device.wake_words.map(String) : [],
      volume: typeof device.volume === 'number' ? device.volume : null,
      enabled: device.enabled !== false,
    }))
    .filter((device) => device.id !== ''),
)

function devicePath(id: string): string {
  return `/devices/${encodeURIComponent(id)}`
}
</script>

<template>
  <section
    class="v2-card v2-config-group"
    :data-group-id="group.id"
    :aria-labelledby="`v2-config-group-${group.id}`"
  >
    <header class="v2-config-group-head">
      <div>
        <h2 :id="`v2-config-group-${group.id}`" class="v2-card-title">
          {{ pickLocalized(group.title) }}
          <span class="badge badge-restart">
            <AppIcon name="refresh" :size="11" />
            {{ t('v2.config.effect.restart') }}
          </span>
          <span class="badge badge-muted">{{ t('v2.common.readOnly') }}</span>
        </h2>
        <p class="v2-card-lede">{{ pickLocalized(group.hint) }}</p>
      </div>
      <span class="meta">{{ t('v2.common.items', { n: rows.length }) }}</span>
    </header>

    <p class="v2-catalog-note" data-testid="device-config-note">
      {{ t('v2.config.devices.note') }}
    </p>

    <div v-if="rows.length" class="v2-device-config-list">
      <div
        v-for="device in rows"
        :key="device.id"
        class="v2-device-config-row"
        data-testid="device-config-row"
      >
        <div class="v2-device-config-main">
          <strong class="mono">{{ device.id }}</strong>
          <span class="meta">{{ device.room || t('v2.config.devices.noRoom') }}</span>
          <span class="badge" :class="device.enabled ? 'badge-ok' : 'badge-muted'">
            {{ device.enabled ? t('v2.common.enabled') : t('v2.common.disabled') }}
          </span>
        </div>
        <dl class="v2-device-config-kv">
          <div>
            <dt>{{ t('v2.config.devices.wakeWords') }}</dt>
            <dd>{{ device.wakeWords.join(' / ') || t('v2.common.na') }}</dd>
          </div>
          <div>
            <dt>{{ t('v2.config.devices.volume') }}</dt>
            <dd>{{ device.volume ?? t('v2.common.na') }}</dd>
          </div>
        </dl>
        <RouterLink
          class="btn btn-sm v2-device-config-link"
          :to="devicePath(device.id)"
          data-testid="device-config-link"
        >
          {{ t('v2.config.devices.open') }}
          <AppIcon name="arrowRight" :size="13" />
        </RouterLink>
      </div>
    </div>

    <p v-else class="note v2-config-empty">
      {{ t('v2.config.devices.empty') }}
      <RouterLink to="/devices">{{ t('v2.config.openDevices') }}</RouterLink>
    </p>
  </section>
</template>
