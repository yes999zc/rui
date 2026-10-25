<script setup lang="ts">
/**
 * Run / house overview (M1 → M7).
 *
 * Reads `GET /api/v2/house` and renders the aggregate the contract exposes:
 * device counts, multi-device policy, room aggregates, and house warnings.
 * Since M7 the page is **exception-first**: `buildAttentionItems` ranks device
 * errors/offline/disabled states, per-device warnings, house warnings
 * (`duplicate_room`, fleet limits) and incomplete setup into one section, each
 * row linking to the device, rooms page, or wizard that can fix it.
 *
 * Room aggregation is used from `house.rooms` when present. Older/partial
 * backends that omit `rooms` fall back to an existing `GET /api/v2/devices`
 * aggregation — no new endpoint, no client-side invention beyond grouping by
 * `room`.
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import StateBlock from '../components/StateBlock.vue'
import AttentionList from '../components/run/AttentionList.vue'
import { api, promptForToken, type DeviceSummary, type HouseInfo } from '../api'
import { describeError } from '../errors'
import { metaFeature } from '../meta'
import { t } from '../i18n'
import { buildAttentionItems } from '../run'
import { useResource } from '../useResource'

const houseResource = useResource(() => api.house())
const devicesResource = useResource(() => api.devices())
const { data: house, error: houseError, loading: houseLoading } = houseResource
const { data: devicePage, error: devicesError, loading: devicesLoading } = devicesResource

const featureEnabled = metaFeature('house')
const booting = ref(false)

const houseErrorCopy = computed(() => (houseError.value ? describeError(houseError.value) : null))
const devicesErrorCopy = computed(() =>
  devicesError.value ? describeError(devicesError.value) : null,
)
const fatalErrorCopy = computed(() => houseErrorCopy.value ?? devicesErrorCopy.value)

const deviceItems = computed<DeviceSummary[]>(() => devicePage.value?.items ?? [])
const canShowOverview = computed(() => Boolean(house.value) || Boolean(devicePage.value))

const counts = computed(() => {
  const info: HouseInfo | null = house.value
  if (info?.devices) return info.devices
  const items = deviceItems.value
  const ready = items.filter((device) => device.ready).length
  return {
    total: items.length,
    enabled: items.filter((device) => device.enabled).length,
    online: ready,
    ready,
  }
})

const multi = computed(() => house.value?.multi_device ?? null)

interface RunRoom {
  room: string
  deviceIds: string[]
  total: number
  online: number
  defaultOutput: boolean
}

const rooms = computed<RunRoom[]>(() => {
  const info: HouseInfo | null = house.value
  if (info?.rooms?.length) {
    return info.rooms.map((room) => {
      const derived = deviceItems.value.filter((device) =>
        room.room === '' ? !device.room : device.room === room.room,
      )
      const ids = room.device_ids?.length ? room.device_ids : derived.map((device) => device.id)
      return {
        room: room.room,
        deviceIds: ids,
        total: ids.length,
        // The house aggregate is authoritative; the device rows are the fallback
        // for a partial backend (never "all online" just because the count is absent).
        online:
          typeof room.online === 'number'
            ? room.online
            : derived.filter((device) => device.ready).length,
        defaultOutput: Boolean(room.default_output),
      }
    })
  }

  const byRoom = new Map<string, DeviceSummary[]>()
  for (const device of deviceItems.value) {
    const key = device.room ?? ''
    const bucket = byRoom.get(key)
    if (bucket) bucket.push(device)
    else byRoom.set(key, [device])
  }
  const keys = [...byRoom.keys()].filter((key) => key !== '').sort((a, b) => a.localeCompare(b))
  if (byRoom.has('')) keys.push('')

  const defaultOutputId = info?.multi_device?.default_output ?? null
  return keys.map((room) => {
    const items = byRoom.get(room) ?? []
    return {
      room,
      deviceIds: items.map((device) => device.id),
      total: items.length,
      online: items.filter((device) => device.ready).length,
      defaultOutput: Boolean(
        defaultOutputId && items.some((device) => device.id === defaultOutputId),
      ),
    }
  })
})

const roomsDerived = computed(() => !house.value?.rooms?.length && deviceItems.value.length > 0)
const isEmpty = computed(() => counts.value.total === 0 && rooms.value.length === 0)
/**
 * M7 exceptions-first list: live per-device status wins over house warnings,
 * and every row is a link to the page that can resolve it (`run.ts`).
 */
const attention = computed(() => buildAttentionItems(house.value, deviceItems.value))
const defaultOutputLabel = computed(() => {
  const id = multi.value?.default_output
  if (!id) return t('v2.run.defaultOutputNone')
  const device = deviceItems.value.find((entry) => entry.id === id)
  if (device) return `${id} · ${device.room || t('v2.devices.unassigned')}`
  const room = house.value?.rooms?.find((entry) => entry.device_ids?.includes(id))
  if (room) return `${id} · ${room.room || t('v2.devices.unassigned')}`
  return id
})

const versionLabel = computed(() => {
  const info = house.value
  if (!info?.version) return t('v2.app.kicker')
  return t('v2.run.version', { rui: info.version.rui, config: info.version.config })
})

const initialLoading = computed(
  () => !canShowOverview.value && (booting.value || houseLoading.value || devicesLoading.value),
)
const fatalError = computed(
  () => !booting.value && !canShowOverview.value && Boolean(fatalErrorCopy.value),
)

async function load(): Promise<void> {
  if (!featureEnabled) return
  booting.value = true
  try {
    // Both are required since M7: the house aggregate carries rooms/warnings,
    // but only the device rows carry live `status`/`status_reason` for the
    // attention list (and the per-room online fallback).
    await Promise.all([houseResource.load(), devicesResource.load()])
  } finally {
    booting.value = false
  }
}

function refresh(): void {
  if (!featureEnabled) return
  void load()
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="v2-page">
    <header class="v2-page-head">
      <div class="v2-page-title">
        <span class="kicker">{{ versionLabel }}</span>
        <h1>{{ t('v2.run.title') }}</h1>
        <p class="lede">{{ t('v2.run.lede') }}</p>
      </div>
      <div class="v2-page-actions">
        <button
          type="button"
          class="btn btn-sm"
          :disabled="houseLoading || devicesLoading"
          @click="refresh"
        >
          <AppIcon name="refresh" :size="14" />
          {{ t('v2.common.refresh') }}
        </button>
      </div>
    </header>

    <StateBlock
      v-if="!featureEnabled"
      kind="unavailable"
      :title="t('v2.run.featureOff')"
      :hint="t('v2.meta.featureOffHint')"
    />

    <StateBlock v-else-if="initialLoading" kind="loading" :title="t('v2.common.loading')" />

    <StateBlock
      v-else-if="fatalError"
      kind="error"
      :title="t('v2.run.loadFailed')"
      :detail="fatalErrorCopy?.detail"
      :hint="fatalErrorCopy?.hint"
      :status="fatalErrorCopy?.status ?? null"
      :request-id="fatalErrorCopy?.requestId ?? null"
      :fields="fatalErrorCopy?.fields ?? []"
      show-retry
      @retry="load"
    >
      <button
        v-if="fatalErrorCopy?.code === 'unauthorized'"
        type="button"
        class="btn btn-primary"
        @click="promptForToken()"
      >
        {{ t('v2.nav.token') }}
      </button>
    </StateBlock>

    <template v-else-if="canShowOverview">
      <div v-if="houseErrorCopy" class="banner banner-warn v2-banner" role="status">
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.run.houseFallback') }}</strong>
          <p class="note">{{ houseErrorCopy.hint }}</p>
        </div>
      </div>
      <div v-else-if="devicesErrorCopy" class="banner banner-warn v2-banner" role="status">
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.run.devicesUnavailable') }}</strong>
          <p class="note">{{ devicesErrorCopy.hint }}</p>
        </div>
      </div>

      <!-- Exceptions first (M7): every row opens the device/rooms/wizard. -->
      <AttentionList :items="attention" />

      <StateBlock
        v-if="isEmpty"
        kind="empty"
        :title="t('v2.run.empty')"
        :hint="t('v2.run.emptyHint')"
      >
        <a class="btn btn-primary" href="/">{{ t('v2.run.emptyAction') }}</a>
      </StateBlock>

      <template v-else>
        <section class="v2-summary" :aria-label="t('v2.run.summaryAria')">
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
            <span class="v2-summary-value mono">{{ multi?.planes_running ?? t('v2.common.na') }}</span>
            <span class="v2-summary-label">{{ t('v2.run.planesRunning') }}</span>
          </div>
        </section>

        <div class="v2-run-grid">
          <section class="v2-card">
            <h2 class="v2-card-title">{{ t('v2.run.multiDevice') }}</h2>
            <p v-if="!multi" class="empty">{{ t('v2.run.multiUnavailable') }}</p>
            <dl v-else class="v2-kv">
              <div>
                <dt>{{ t('v2.run.multiEnabled') }}</dt>
                <dd>{{ multi.enabled ? t('v2.common.yes') : t('v2.common.no') }}</dd>
              </div>
              <div>
                <dt>{{ t('v2.run.maxPlanes') }}</dt>
                <dd class="mono">{{ multi.max_planes ?? t('v2.common.na') }}</dd>
              </div>
              <div>
                <dt>{{ t('v2.run.planesRunning') }}</dt>
                <dd class="mono">{{ multi.planes_running ?? t('v2.common.na') }}</dd>
              </div>
              <div>
                <dt>{{ t('v2.run.defaultOutput') }}</dt>
                <dd class="mono">{{ defaultOutputLabel }}</dd>
              </div>
            </dl>
          </section>

          <section class="v2-card">
            <h2 class="v2-card-title">{{ t('v2.run.rooms') }}</h2>
            <p v-if="roomsDerived" class="note v2-card-lede">{{ t('v2.run.roomsDerived') }}</p>
            <p v-if="!rooms.length" class="empty">{{ t('v2.run.roomsEmpty') }}</p>
            <ul v-else class="v2-room-list">
              <li v-for="room in rooms" :key="room.room || 'unassigned'" class="v2-room-row">
                <div class="v2-room-name">
                  <strong>{{ room.room || t('v2.devices.unassigned') }}</strong>
                  <span v-if="room.defaultOutput" class="badge badge-accent">
                    {{ t('v2.devices.defaultOutput') }}
                  </span>
                </div>
                <div class="v2-room-counts">
                  <span class="meta">
                    {{ t('v2.devices.roomCount', { online: room.online, total: room.total }) }}
                  </span>
                  <span class="meta">{{ t('v2.devices.roomDevices', { n: room.total }) }}</span>
                </div>
                <div class="v2-chips">
                  <RouterLink
                    v-for="deviceId in room.deviceIds"
                    :key="deviceId"
                    class="v2-cap v2-room-device"
                    :to="`/devices/${deviceId}`"
                    :title="t('v2.devices.openAria', { id: deviceId })"
                  >
                    {{ deviceId }}
                  </RouterLink>
                </div>
              </li>
            </ul>
          </section>
        </div>

      </template>
    </template>
  </div>
</template>
