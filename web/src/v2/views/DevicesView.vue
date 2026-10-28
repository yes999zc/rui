<script setup lang="ts">
/**
 * Device list (M1, read-only): grouped by room, status first, capability chips
 * from the core catalog. No model names are inspected anywhere.
 */
import { computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import CapabilityChips from '../components/CapabilityChips.vue'
import StateBlock from '../components/StateBlock.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api, promptForToken, type DeviceSummary, type HouseInfo } from '../api'
import { capabilityError, loadCapabilities } from '../capabilities'
import { describeError } from '../errors'
import { relativeTime } from '../format'
import { meta, metaFeature } from '../meta'
import {
  statusReasonText,
  t,
  transportLabel,
  warningKnown,
  warningTitle,
} from '../i18n'
import { useResource } from '../useResource'

const devicesResource = useResource(() => api.devices())
const houseResource = useResource(() => api.house())
const { data: deviceData, error: deviceError, loading: devicesLoading } = devicesResource
const { data: houseData, error: houseError } = houseResource

const featureEnabled = metaFeature('devices')
const createEnabled = metaFeature('device_create')
const setupEnabled = metaFeature('setup_plan')

const deviceErrorCopy = computed(() => (deviceError.value ? describeError(deviceError.value) : null))
const houseErrorCopy = computed(() => (houseError.value ? describeError(houseError.value) : null))
const catalogOff = computed(() => Boolean(capabilityError.value) && deviceData.value !== null)

interface DeviceCard {
  device: DeviceSummary
  room: string
  defaultOutput: boolean
}

/**
 * One card per device, ordered by room so same-room cards stay adjacent. The
 * previous room sections are gone; the room name is the card title instead.
 */
const cards = computed<DeviceCard[]>(() => {
  const items = deviceData.value?.items ?? []
  const info: HouseInfo | null = houseData.value
  const byRoom = new Map<string, DeviceSummary[]>()
  for (const device of items) {
    const key = device.room ?? ''
    const bucket = byRoom.get(key)
    if (bucket) bucket.push(device)
    else byRoom.set(key, [device])
  }

  const order: string[] = []
  for (const room of info?.rooms ?? []) {
    if (byRoom.has(room.room) && !order.includes(room.room)) order.push(room.room)
  }
  const rest = [...byRoom.keys()]
    .filter((key) => key !== '' && !order.includes(key))
    .sort((a, b) => a.localeCompare(b))
  order.push(...rest)
  if (byRoom.has('')) order.push('')

  const defaultOutputId = info?.multi_device?.default_output ?? ''
  return order.flatMap((room) =>
    (byRoom.get(room) ?? []).map((device) => ({
      device,
      room,
      defaultOutput: Boolean(defaultOutputId && device.id === defaultOutputId),
    })),
  )
})

const counts = computed(() => {
  const info = houseData.value
  if (info) {
    return {
      total: info.devices.total,
      enabled: info.devices.enabled,
      online: info.devices.online,
      ready: info.devices.ready,
    }
  }
  const items = deviceData.value?.items ?? []
  const ready = items.filter((device) => device.ready).length
  return {
    total: items.length,
    enabled: items.filter((device) => device.enabled).length,
    online: ready,
    ready,
  }
})

const roomCount = computed(() => new Set(cards.value.map((card) => card.room)).size)

/**
 * An empty house gets the first-run wizard (`/setup`); once a device exists the
 * list keeps linking to the add wizard. The server also derives `first-run`, but
 * the entry point should not make the operator pick the add flow first.
 */
const devicesLoaded = computed(() => deviceData.value !== null)
const hasDevices = computed(() => (deviceData.value?.items.length ?? 0) > 0)
const firstRunEntry = computed(
  () => devicesLoaded.value && !hasDevices.value && setupEnabled,
)
const createPath = computed(() => (firstRunEntry.value ? '/setup' : '/devices/add'))
const emptyHint = computed(() => {
  if (firstRunEntry.value) return t('v2.devices.emptySetupHint')
  return createEnabled ? t('v2.devices.emptyHint') : t('v2.devices.emptyLegacyHint')
})

function lastSeen(device: DeviceSummary): string {
  if (!device.last_seen) return t('v2.devices.neverSeen')
  return t('v2.devices.lastSeen', { time: relativeTime(device.last_seen) })
}

function audioLine(device: DeviceSummary): string {
  const summary = device.audio_summary
  if (!summary) return t('v2.common.na')
  return t('v2.devices.audioSummary', {
    capture: summary.capture_rate,
    playback: summary.playback_rate,
    aec: summary.aec_who,
    ack: summary.ack_echo_ignore_ms,
  })
}

function firstWarning(device: DeviceSummary): string {
  const warning = device.warnings?.[0]
  return warning ? warningTitle(warning.code, warning.message) : ''
}

function refresh(): void {
  if (!featureEnabled) return
  void Promise.allSettled([devicesResource.load(), houseResource.load(), loadCapabilities()])
}

onMounted(() => {
  if (!featureEnabled) return
  void devicesResource.load()
  void houseResource.load()
  void loadCapabilities()
})
</script>

<template>
  <div class="v2-page">
    <header class="v2-page-head">
      <div class="v2-page-title">
        <span class="kicker">{{ t('v2.devices.m1badge') }}</span>
        <h1>{{ t('v2.devices.title') }}</h1>
        <p class="lede">{{ t('v2.devices.lede') }}</p>
      </div>
      <div class="v2-page-actions">
        <span v-if="meta" class="badge badge-muted mono">{{ meta.contract }}</span>
        <RouterLink
          v-if="createEnabled || firstRunEntry"
          class="btn btn-primary btn-sm"
          :to="createPath"
          data-testid="devices-add"
        >
          <AppIcon name="plus" :size="14" />
          {{ firstRunEntry ? t('v2.devices.firstRun') : t('v2.devices.add') }}
        </RouterLink>
        <button type="button" class="btn btn-sm" :disabled="devicesLoading" @click="refresh">
          <AppIcon name="refresh" :size="14" />
          {{ t('v2.common.refresh') }}
        </button>
      </div>
    </header>

    <StateBlock
      v-if="!featureEnabled"
      kind="unavailable"
      :title="t('v2.devices.featureOff')"
      :hint="t('v2.devices.featureOffHint')"
    />

    <StateBlock
      v-else-if="devicesLoading && !deviceData"
      kind="loading"
      :title="t('v2.common.loading')"
    />

    <StateBlock
      v-else-if="deviceErrorCopy && !deviceData"
      kind="error"
      :title="t('v2.devices.loadFailed')"
      :detail="deviceErrorCopy.detail"
      :hint="deviceErrorCopy.hint"
      :status="deviceErrorCopy.status"
      :request-id="deviceErrorCopy.requestId"
      :fields="deviceErrorCopy.fields"
      show-retry
      @retry="refresh"
    >
      <button
        v-if="deviceErrorCopy.code === 'unauthorized'"
        type="button"
        class="btn btn-primary"
        @click="promptForToken()"
      >
        {{ t('v2.nav.token') }}
      </button>
    </StateBlock>

    <StateBlock
      v-else-if="!deviceData || deviceData.items.length === 0"
      kind="empty"
      :title="t('v2.devices.empty')"
      :hint="emptyHint"
    >
      <RouterLink
        v-if="setupEnabled"
        class="btn btn-primary"
        to="/setup"
        data-testid="devices-first-run"
      >
        {{ t('v2.devices.emptySetupAction') }}
      </RouterLink>
      <RouterLink
        v-if="createEnabled"
        class="btn"
        to="/devices/add"
        data-testid="devices-empty-add"
      >
        {{ setupEnabled ? t('v2.devices.emptyAddAction') : t('v2.devices.emptyAction') }}
      </RouterLink>
      <RouterLink v-else-if="!setupEnabled" class="btn btn-primary" to="/setup">
        {{ t('v2.devices.emptyAction') }}
      </RouterLink>
    </StateBlock>

    <template v-else>
      <section class="v2-summary" :aria-label="t('v2.devices.summaryAria')">
        <div class="v2-summary-item">
          <span class="v2-summary-value mono">{{ counts.total }}</span>
          <span class="v2-summary-label">{{ t('v2.devices.summary.total') }}</span>
        </div>
        <div class="v2-summary-item">
          <span class="v2-summary-value mono">{{ counts.enabled }}</span>
          <span class="v2-summary-label">{{ t('v2.devices.summary.enabled') }}</span>
        </div>
        <div class="v2-summary-item">
          <span class="v2-summary-value mono is-ok">{{ counts.online }}</span>
          <span class="v2-summary-label">{{ t('v2.devices.summary.online') }}</span>
        </div>
        <div class="v2-summary-item">
          <span class="v2-summary-value mono">{{ counts.ready }}</span>
          <span class="v2-summary-label">{{ t('v2.devices.summary.ready') }}</span>
        </div>
        <div class="v2-summary-item">
          <span class="v2-summary-value mono">{{ roomCount }}</span>
          <span class="v2-summary-label">{{ t('v2.devices.summary.rooms') }}</span>
        </div>
      </section>

      <div v-if="houseErrorCopy" class="banner banner-warn v2-banner" role="status">
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.devices.houseFailed') }}</strong>
          <p class="note">{{ houseErrorCopy.hint }}</p>
        </div>
      </div>

      <div v-if="catalogOff" class="note v2-catalog-note">
        {{ t('v2.capabilities.catalogOff') }}
      </div>

      <section
        v-if="houseData?.warnings?.length"
        class="v2-card v2-warnings"
        :aria-label="t('v2.warning.house')"
      >
        <h2 class="v2-card-title">
          <AppIcon name="alert" :size="16" />
          {{ t('v2.warning.house') }}
        </h2>
        <ul class="v2-warning-list">
          <li v-for="(warning, index) in houseData.warnings" :key="`${warning.code}-${index}`">
            <span class="dot warn" aria-hidden="true" />
            <strong>{{ warningTitle(warning.code, warning.message) }}</strong>
            <span class="tag mono">{{ warning.code }}</span>
            <span v-if="warning.device_id" class="tag mono">{{ warning.device_id }}</span>
            <span v-if="warning.params?.length" class="tag mono">
              {{ warning.params.join(', ') }}
            </span>
            <span v-if="!warningKnown(warning.code) && warning.hint" class="note">
              {{ warning.hint }}
            </span>
          </li>
        </ul>
      </section>

      <div class="v2-device-grid">
        <RouterLink
          v-for="card in cards"
          :key="card.device.id"
          class="v2-device-card"
          :to="`/devices/${card.device.id}`"
          :aria-label="t('v2.devices.openAria', { id: card.device.id })"
        >
          <div class="v2-device-top">
            <div class="v2-device-title">
              <h2 class="v2-device-room">{{ card.room || t('v2.devices.unassigned') }}</h2>
              <span
                class="v2-device-card-sub mono"
                :title="`${card.device.id} · ${card.device.model || t('v2.devices.modelFallback')}`"
              >
                <span class="v2-device-id">{{ card.device.id }}</span>
                <span aria-hidden="true">·</span>
                <span class="v2-device-model">
                  {{ card.device.model || t('v2.devices.modelFallback') }}
                </span>
              </span>
            </div>
            <div class="v2-device-badges">
              <span v-if="card.defaultOutput" class="badge badge-accent">
                {{ t('v2.devices.defaultOutput') }}
              </span>
              <StatusBadge
                :status="card.device.status"
                :reason="card.device.status_reason"
              />
            </div>
          </div>

          <p class="v2-device-sub">
            <span>{{ card.device.vendor || t('v2.devices.vendorFallback') }}</span>
            <span aria-hidden="true">·</span>
            <span>{{ transportLabel(card.device.transport) }}</span>
            <span v-if="!card.device.enabled" class="tag">{{ t('v2.common.disabled') }}</span>
          </p>

          <p v-if="card.device.status !== 'ready'" class="v2-device-reason">
            {{ statusReasonText(card.device.status_reason) }}
          </p>

          <CapabilityChips
            :ids="card.device.capabilities ?? []"
            :unknown="card.device.unknown_capabilities ?? []"
          />

          <p class="v2-device-audio mono">{{ audioLine(card.device) }}</p>

          <div v-if="card.device.warnings?.length" class="v2-device-warn">
            <AppIcon name="alert" :size="13" />
            <span>{{ firstWarning(card.device) }}</span>
          </div>

          <div class="v2-device-foot">
            <span>{{ lastSeen(card.device) }}</span>
            <span class="v2-open">
              {{ t('v2.devices.open') }}
              <AppIcon name="arrowRight" :size="14" />
            </span>
          </div>
        </RouterLink>
      </div>
    </template>
  </div>
</template>
