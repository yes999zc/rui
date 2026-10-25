<script setup lang="ts">
/**
 * `/v2/conversation` (M8) — the run-section conversation page.
 *
 * Contract C-002 §1.2 keeps non-device domains on stable v1 endpoints for this
 * delivery, so the page reads `GET /api/v1/turns` (newest first, additive
 * `device_id`) and maps that id onto `GET /api/v2/devices` for the room/model
 * badge. It never calls a v1 device endpoint and never imports a legacy view.
 *
 * Filtering happens on the loaded tail only: the v1 endpoint has no device
 * query, so the page counts what it filtered and says so ("N of M turns").
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppIcon from '../../components/AppIcon.vue'
import StateBlock from '../components/StateBlock.vue'
import TurnCard from '../components/run/TurnCard.vue'
import { api, promptForToken, type DeviceSummary, type TurnItem } from '../api'
import { describeError } from '../errors'
import { t } from '../i18n'
import { useResource } from '../useResource'

// The backend keeps a 50-turn ring, so larger limits only pretend to load more.
const LIMITS = [20, 50]
const HOUSE_FILTER = '__house__'
const POLL_MS = 5000

const limit = ref(50)
const autoRefresh = ref(true)
const manualBusy = ref(false)
const deviceFilter = ref('')
const roomFilter = ref('')

const turnsResource = useResource(() => api.turns(limit.value))
const devicesResource = useResource(() => api.devices())
const { data: turnPage, error, loading } = turnsResource
const { data: devicePage, error: deviceError } = devicesResource

const errorCopy = computed(() => (error.value ? describeError(error.value) : null))
const staleError = computed(() => Boolean(errorCopy.value) && allTurns.value.length > 0)

// A failed catalog request is not "this id is unknown": without it no turn badge
// can resolve a room/model, so say so once instead of mislabelling every turn.
const catalogUnavailable = computed(() => Boolean(deviceError.value))
const catalogErrorCopy = computed(() =>
  deviceError.value ? describeError(deviceError.value) : null,
)

const deviceItems = computed<DeviceSummary[]>(() => devicePage.value?.items ?? [])
const deviceMap = computed(() => {
  const map = new Map<string, DeviceSummary>()
  for (const device of deviceItems.value) map.set(device.id, device)
  return map
})

const allTurns = computed<TurnItem[]>(() => turnPage.value?.turns ?? [])

const deviceOptions = computed(() => {
  const ids = new Set<string>()
  for (const turn of allTurns.value) {
    const id = (turn.device_id ?? '').trim()
    if (id) ids.add(id)
  }
  for (const device of deviceItems.value) ids.add(device.id)
  return [...ids].sort((a, b) => a.localeCompare(b))
})

const roomOptions = computed(() => {
  const rooms = new Set<string>()
  for (const device of deviceItems.value) if (device.room) rooms.add(device.room)
  return [...rooms].sort((a, b) => a.localeCompare(b))
})

function mappedDevice(deviceId: string | undefined): DeviceSummary | undefined {
  const id = (deviceId ?? '').trim()
  return id ? deviceMap.value.get(id) : undefined
}

const filteredTurns = computed<TurnItem[]>(() =>
  allTurns.value.filter((turn) => {
    const id = (turn.device_id ?? '').trim()
    if (deviceFilter.value === HOUSE_FILTER) {
      if (id) return false
    } else if (deviceFilter.value && id !== deviceFilter.value) {
      return false
    }
    if (roomFilter.value) {
      const room = id ? mappedDevice(id)?.room ?? '' : ''
      if (room !== roomFilter.value) return false
    }
    return true
  }),
)

const filtered = computed(
  () => deviceFilter.value !== '' || roomFilter.value !== '',
)

const isEmpty = computed(() => allTurns.value.length === 0)
const emptyFiltered = computed(() => !isEmpty.value && filteredTurns.value.length === 0)

let timer: number | null = null

function stopTimer(): void {
  if (timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
}

function syncTimer(): void {
  stopTimer()
  if (!autoRefresh.value) return
  timer = window.setInterval(() => {
    if (document.hidden || loading.value) return
    void turnsResource.load()
  }, POLL_MS)
}

async function load(): Promise<void> {
  await Promise.all([turnsResource.load(), devicesResource.load()])
}

async function refresh(): Promise<void> {
  manualBusy.value = true
  try {
    await load()
  } finally {
    manualBusy.value = false
  }
}

function onLimit(event: Event): void {
  const value = Number((event.target as HTMLSelectElement).value)
  if (Number.isFinite(value) && value !== limit.value) {
    limit.value = value
    void turnsResource.load()
  }
}

watch(autoRefresh, syncTimer)

onMounted(() => {
  void refresh()
  syncTimer()
})

onBeforeUnmount(stopTimer)
</script>

<template>
  <div class="v2-page">
    <header class="v2-page-head">
      <div class="v2-page-title">
        <span class="kicker">{{ t('v2.nav.run') }}</span>
        <h1>{{ t('v2.conversation.title') }}</h1>
        <p class="lede">{{ t('v2.conversation.lede') }}</p>
      </div>
      <div class="v2-page-actions">
        <label class="v2-limit" :title="t('v2.conversation.limitHint')">
          <span class="meta">{{ t('v2.conversation.limit') }}</span>
          <select
            class="input v2-limit-select"
            :value="limit"
            :aria-label="t('v2.conversation.limit')"
            data-testid="conversation-limit"
            @change="onLimit"
          >
            <option v-for="n in LIMITS" :key="n" :value="n">{{ n }}</option>
          </select>
        </label>
        <label class="switch" :title="t('v2.conversation.autoHint')">
          <input v-model="autoRefresh" type="checkbox" data-testid="conversation-auto" />
          <span class="track" aria-hidden="true" />
          <span class="switch-text">{{ t('v2.conversation.autoRefresh') }}</span>
        </label>
        <button
          type="button"
          class="btn btn-sm"
          :disabled="manualBusy || loading"
          data-testid="conversation-refresh"
          @click="refresh"
        >
          <AppIcon name="refresh" :size="14" />
          {{ manualBusy ? t('v2.conversation.refreshing') : t('v2.common.refresh') }}
        </button>
      </div>
    </header>

    <StateBlock
      v-if="loading && !turnPage"
      kind="loading"
      :title="t('v2.common.loading')"
    />

    <StateBlock
      v-else-if="errorCopy && !turnPage"
      kind="error"
      :title="t('v2.conversation.loadFailed')"
      :detail="errorCopy.detail"
      :hint="errorCopy.hint"
      :status="errorCopy.status"
      :request-id="errorCopy.requestId"
      show-retry
      @retry="load"
    >
      <button
        v-if="errorCopy?.code === 'unauthorized'"
        type="button"
        class="btn btn-primary"
        @click="promptForToken()"
      >
        {{ t('v2.nav.token') }}
      </button>
    </StateBlock>

    <template v-else-if="turnPage">
      <div v-if="staleError" class="banner banner-warn v2-banner" role="status">
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.conversation.loadFailed') }}</strong>
          <p v-if="errorCopy" class="note">{{ errorCopy.hint }}</p>
        </div>
        <button type="button" class="btn btn-sm" @click="refresh">
          {{ t('v2.common.retry') }}
        </button>
      </div>

      <div v-if="catalogUnavailable" class="banner banner-warn v2-banner" role="status" data-testid="conversation-catalog-banner">
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.conversation.deviceCatalogUnavailable') }}</strong>
          <p v-if="catalogErrorCopy" class="note">{{ catalogErrorCopy.hint }}</p>
        </div>
        <button type="button" class="btn btn-sm" @click="refresh">
          {{ t('v2.common.retry') }}
        </button>
      </div>

      <div class="v2-run-toolbar" :aria-label="t('v2.conversation.filterAria')">
        <label class="v2-inline-field">
          <span class="meta">{{ t('v2.conversation.deviceFilter') }}</span>
          <select
            v-model="deviceFilter"
            class="input v2-select"
            data-testid="conversation-device-filter"
          >
            <option value="">{{ t('v2.conversation.allDevices') }}</option>
            <option :value="HOUSE_FILTER">{{ t('v2.conversation.wholeHouse') }}</option>
            <option v-for="id in deviceOptions" :key="id" :value="id">
              {{ id }}<template v-if="deviceMap.get(id)?.room"> · {{ deviceMap.get(id)?.room }}</template>
            </option>
          </select>
        </label>
        <label class="v2-inline-field">
          <span class="meta">{{ t('v2.conversation.roomFilter') }}</span>
          <select
            v-model="roomFilter"
            class="input v2-select"
            data-testid="conversation-room-filter"
          >
            <option value="">{{ t('v2.conversation.allRooms') }}</option>
            <option v-for="room in roomOptions" :key="room" :value="room">{{ room }}</option>
          </select>
        </label>
        <span class="meta v2-run-count" data-testid="conversation-count">
          <template v-if="filtered">
            {{ t('v2.conversation.filtered', { shown: filteredTurns.length, total: allTurns.length }) }}
          </template>
          <template v-else>
            {{ t('v2.conversation.showing', { n: allTurns.length }) }}
          </template>
        </span>
        <span v-if="loading" class="tag">{{ t('v2.common.loading') }}</span>
      </div>

      <StateBlock
        v-if="isEmpty"
        kind="empty"
        :title="t('v2.conversation.empty')"
        :hint="t('v2.conversation.emptyHint')"
      />
      <StateBlock
        v-else-if="emptyFiltered"
        kind="empty"
        :title="t('v2.conversation.emptyFiltered')"
        :hint="t('v2.conversation.emptyFilteredHint')"
      >
        <button
          type="button"
          class="btn btn-sm"
          @click="deviceFilter = ''; roomFilter = ''"
        >
          {{ t('v2.conversation.clearFilters') }}
        </button>
      </StateBlock>

      <div v-else class="v2-turn-list" data-testid="conversation-list">
        <TurnCard
          v-for="(turn, index) in filteredTurns"
          :key="`${turn.ts_epoch ?? 'turn'}-${index}`"
          :turn="turn"
          :room="mappedDevice(turn.device_id)?.room ?? ''"
          :model="mappedDevice(turn.device_id)?.model ?? ''"
          :mapped="Boolean(mappedDevice(turn.device_id))"
          :catalog-unavailable="catalogUnavailable"
        />
      </div>
    </template>
  </div>
</template>
