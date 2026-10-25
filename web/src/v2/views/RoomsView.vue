<script setup lang="ts">
/**
 * Rooms & house (M5): the "why does the whole house behave like this?" page.
 *
 * Data sources (contract C-002 §7.2–7.6):
 *  - `GET /house` for room aggregates, multi-device policy and warnings;
 *  - `GET /devices` for per-device status and model/vendor labels;
 *  - `GET /house/audio` for the global audio defaults and their `explicit` set;
 *  - `GET /devices/{id}/audio` only to count devices with their own overrides
 *    (no new endpoint, per the M5 brief).
 *
 * Rooms are never edited here: `room` is a device attribute, not an entity.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, RouterLink, useRouter } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import StateBlock from '../components/StateBlock.vue'
import HouseAudioEditor from '../components/house/HouseAudioEditor.vue'
import HousePolicyEditor from '../components/house/HousePolicyEditor.vue'
import RoomsPanel from '../components/house/RoomsPanel.vue'
import {
  api,
  promptForToken,
  type HouseAudioInfo,
  type HouseInfo,
  type DeviceSummary,
} from '../api'
import { describeError } from '../errors'
import { meta, metaFeature } from '../meta'
import { t, warningKnown, warningTitle } from '../i18n'
import { useResource } from '../useResource'

const houseResource = useResource(() => api.house())
const devicesResource = useResource(() => api.devices())
const audioResource = useResource(() => api.houseAudio())

const { data: houseData, error: houseError, loading: houseLoading } = houseResource
const { data: deviceData, error: deviceError, loading: deviceLoading } = devicesResource
const { data: audioData, error: audioError, loading: audioLoading } = audioResource

const houseEnabled = metaFeature('house')
const audioEnabled = metaFeature('house_audio')
const devicesEnabled = metaFeature('devices')
const pageEnabled = houseEnabled || audioEnabled || devicesEnabled

const booting = ref(false)
const overrides = ref<Record<string, number>>({})
const overrideUnreadable = ref(0)
// `device_audio_provenance=false` means no override request may be sent, so the
// count is unknown rather than "every device failed".
const overrideUnavailable = ref(false)
let overrideSeq = 0

const policyRef = ref<{ setDefaultOutput: (id: string) => void } | null>(null)

const router = useRouter()
const policyDirty = ref(false)
const audioDirty = ref(false)
const houseDirty = computed(() => policyDirty.value || audioDirty.value)
const pendingLeave = ref<string | null>(null)
// Set only while a confirmed navigation is in flight; the guard then passes
// through and resets as soon as the router settles (or the push is rejected).
let leaveApproved = false

const devices = computed<DeviceSummary[]>(() => deviceData.value?.items ?? [])
const houseErrorCopy = computed(() => (houseError.value ? describeError(houseError.value) : null))
const deviceErrorCopy = computed(() =>
  deviceError.value ? describeError(deviceError.value) : null,
)
const audioErrorCopy = computed(() => (audioError.value ? describeError(audioError.value) : null))
const fatalErrorCopy = computed(() => houseErrorCopy.value ?? deviceErrorCopy.value)
const fatalError = computed(
  () => !booting.value && !houseData.value && !deviceData.value && Boolean(fatalErrorCopy.value),
)
const initialLoading = computed(
  () => !houseData.value && !deviceData.value && (booting.value || houseLoading.value || deviceLoading.value),
)

const hasRoomData = computed(() =>
  Boolean((houseData.value?.rooms?.length ?? 0) > 0 || devices.value.length > 0),
)

const summary = computed(() => {
  const house: HouseInfo | null = houseData.value
  const items = devices.value
  const roomKeys = new Set<string>()
  for (const room of house?.rooms ?? []) roomKeys.add(room.room)
  for (const device of items) roomKeys.add(device.room ?? '')
  return {
    rooms: roomKeys.size,
    devices: house?.devices?.total ?? items.length,
    online: house?.devices?.online ?? items.filter((device) => device.ready).length,
    overrides: Object.keys(overrides.value).length,
  }
})

const warnings = computed(() => houseData.value?.warnings ?? [])

// The provenance feature gate means "unknown", not "all devices unreadable":
// no request was ever sent, so the partial-read wording would be a lie.
const overrideSummaryLabel = computed(() => {
  if (overrideUnavailable.value) return t('v2.rooms.summary.overridesUnavailable')
  if (overrideUnreadable.value > 0) {
    return t('v2.rooms.summary.overridesPartial', {
      failed: overrideUnreadable.value,
      known: summary.value.overrides,
    })
  }
  return t('v2.rooms.summary.overrides')
})

const overrideDevices = computed(() =>
  Object.entries(overrides.value)
    .filter(([, count]) => count > 0)
    .map(([id, count]) => ({ id, count }))
    .sort((a, b) => a.id.localeCompare(b.id)),
)

async function loadOverrides(): Promise<void> {
  const ids =
    deviceData.value?.items.map((device) => device.id) ??
    houseData.value?.rooms?.flatMap((room) => room.device_ids) ??
    []
  const mine = ++overrideSeq
  if (!ids.length) {
    overrides.value = {}
    overrideUnreadable.value = 0
    overrideUnavailable.value = false
    return
  }
  if (!metaFeature('device_audio_provenance')) {
    overrides.value = {}
    overrideUnreadable.value = 0
    overrideUnavailable.value = true
    return
  }
  const results = await Promise.allSettled(ids.map((id) => api.deviceAudio(id)))
  if (mine !== overrideSeq) return
  const map: Record<string, number> = {}
  let failed = 0
  results.forEach((result, index) => {
    const id = ids[index]
    if (result.status !== 'fulfilled') {
      failed += 1
      return
    }
    const count = result.value.overrides?.length ?? 0
    if (count > 0) map[id] = count
  })
  overrides.value = map
  overrideUnreadable.value = failed
  overrideUnavailable.value = false
}

async function loadAll(): Promise<void> {
  if (!pageEnabled) return
  booting.value = true
  try {
    // Respect `meta.features`: a disabled surface must not issue its request.
    // The editors stay mounted so they can explain the feature flag; only the
    // loads they would need are skipped.
    const loads: Array<Promise<unknown>> = []
    if (houseEnabled) loads.push(houseResource.load())
    if (devicesEnabled) loads.push(devicesResource.load())
    if (audioEnabled) loads.push(audioResource.load())
    await Promise.allSettled(loads)
    await loadOverrides()
  } finally {
    booting.value = false
  }
}

function refresh(): void {
  void loadAll()
}

function onHouseUpdated(next: HouseInfo): void {
  houseResource.error.value = null
  houseData.value = next
  void loadOverrides()
}

function onAudioUpdated(next: HouseAudioInfo): void {
  audioResource.error.value = null
  audioData.value = next
}

function onRestarted(): void {
  void loadAll()
}

function setDefault(id: string): void {
  policyRef.value?.setDefaultOutput(id)
}

function onPolicyDirty(value: boolean): void {
  policyDirty.value = value
}

function onAudioDirty(value: boolean): void {
  audioDirty.value = value
}

function onBeforeUnload(event: BeforeUnloadEvent): void {
  if (!houseDirty.value) return
  event.preventDefault()
  // Required by older Chromium/Firefox to show the native confirmation.
  event.returnValue = ''
}

// React to the *next* navigation; the in-app confirmation keeps this page
// mounted, so cancel leaves the draft untouched.
onBeforeRouteLeave((to) => {
  if (!houseDirty.value || leaveApproved) return true
  pendingLeave.value = to.fullPath
  return false
})

function confirmLeave(): void {
  const target = pendingLeave.value
  pendingLeave.value = null
  if (!target) return
  leaveApproved = true
  void router.push(target).finally(() => {
    leaveApproved = false
  })
}

function cancelLeave(): void {
  pendingLeave.value = null
}

onMounted(() => {
  window.addEventListener('beforeunload', onBeforeUnload)
  void loadAll()
})

onBeforeUnmount(() => window.removeEventListener('beforeunload', onBeforeUnload))
</script>

<template>
  <div class="v2-page">
    <header class="v2-page-head">
      <div class="v2-page-title">
        <span class="kicker">{{ t('v2.rooms.badge') }}</span>
        <h1>{{ t('v2.rooms.title') }}</h1>
        <p class="lede">{{ t('v2.rooms.lede') }}</p>
      </div>
      <div class="v2-page-actions">
        <span v-if="meta" class="badge badge-muted mono">{{ meta.contract }}</span>
        <button
          type="button"
          class="btn btn-sm"
          :disabled="houseLoading || deviceLoading || audioLoading"
          @click="refresh"
        >
          <AppIcon name="refresh" :size="14" />
          {{ t('v2.common.refresh') }}
        </button>
      </div>
    </header>

    <StateBlock
      v-if="!pageEnabled"
      kind="unavailable"
      :title="t('v2.rooms.featureOff')"
      :hint="t('v2.rooms.featureOffHint')"
    />

    <StateBlock
      v-else-if="initialLoading"
      kind="loading"
      :title="t('v2.common.loading')"
    />

    <StateBlock
      v-else-if="fatalError"
      kind="error"
      :title="t('v2.rooms.loadFailed')"
      :detail="fatalErrorCopy?.detail"
      :hint="fatalErrorCopy?.hint"
      :status="fatalErrorCopy?.status ?? null"
      :request-id="fatalErrorCopy?.requestId ?? null"
      :fields="fatalErrorCopy?.fields ?? []"
      show-retry
      @retry="refresh"
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

    <template v-else>
      <div v-if="houseErrorCopy" class="banner banner-warn v2-banner" role="status">
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.rooms.houseUnavailable') }}</strong>
          <p class="note">{{ t('v2.rooms.houseUnavailableHint') }}</p>
        </div>
      </div>
      <div v-else-if="deviceErrorCopy" class="banner banner-warn v2-banner" role="status">
        <AppIcon name="alert" :size="17" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.rooms.devicesUnavailable') }}</strong>
          <p class="note">{{ t('v2.rooms.devicesUnavailableHint') }}</p>
        </div>
      </div>

      <section class="v2-summary" :aria-label="t('v2.rooms.title')">
        <div class="v2-summary-item">
          <span class="v2-summary-value mono">{{ summary.rooms }}</span>
          <span class="v2-summary-label">{{ t('v2.rooms.summary.rooms') }}</span>
        </div>
        <div class="v2-summary-item">
          <span class="v2-summary-value mono">{{ summary.devices }}</span>
          <span class="v2-summary-label">{{ t('v2.rooms.summary.devices') }}</span>
        </div>
        <div class="v2-summary-item">
          <span class="v2-summary-value mono is-ok">{{ summary.online }}</span>
          <span class="v2-summary-label">{{ t('v2.rooms.summary.online') }}</span>
        </div>
        <div class="v2-summary-item">
          <span class="v2-summary-value mono" data-testid="summary-overrides">
            <template v-if="overrideUnavailable">—</template>
            <template v-else>
              {{ summary.overrides }}<span
                v-if="overrideUnreadable > 0"
                class="v2-summary-unknown"
                aria-hidden="true"
              >?</span>
            </template>
          </span>
          <span class="v2-summary-label">{{ overrideSummaryLabel }}</span>
        </div>
      </section>

      <section
        v-if="warnings.length"
        class="v2-card v2-warnings"
        :aria-label="t('v2.warning.house')"
      >
        <h2 class="v2-card-title">
          <AppIcon name="alert" :size="16" />
          {{ t('v2.warning.house') }}
        </h2>
        <ul class="v2-warning-list">
          <li v-for="(warning, index) in warnings" :key="`${warning.code}-${index}`">
            <span class="dot warn" aria-hidden="true" />
            <strong>{{ warningTitle(warning.code, warning.message) }}</strong>
            <span class="tag mono">{{ warning.code }}</span>
            <span v-if="warning.device_id" class="tag mono">{{ warning.device_id }}</span>
            <span v-if="!warningKnown(warning.code) && warning.message" class="note">
              {{ warning.message }}
            </span>
          </li>
        </ul>
      </section>

      <StateBlock
        v-if="!hasRoomData"
        kind="empty"
        :title="t('v2.rooms.empty')"
        :hint="t('v2.rooms.emptyHint')"
      >
        <RouterLink class="btn btn-primary" to="/devices/add">
          {{ t('v2.rooms.emptyAction') }}
        </RouterLink>
      </StateBlock>
      <RoomsPanel
        v-else
        :house="houseData"
        :devices="devices"
        :write-enabled="houseEnabled && Boolean(houseData)"
        :override-counts="overrides"
        @set-default="setDefault"
      />

      <!--
        Both editors stay mounted even when their feature flag is off: the
        editor itself explains the flag, and `loadAll` skips the corresponding
        request instead of fetching data that cannot be used.
      -->
      <HousePolicyEditor
        ref="policyRef"
        :house="houseData"
        :devices="devices"
        :write-enabled="houseEnabled"
        @updated="onHouseUpdated"
        @restarted="onRestarted"
        @dirty-change="onPolicyDirty"
      />

      <HouseAudioEditor
        :data="audioData"
        :loading="audioLoading"
        :error-copy="audioErrorCopy"
        :write-enabled="audioEnabled"
        :override-devices="overrideDevices"
        :override-unreadable="overrideUnreadable"
        :override-unavailable="overrideUnavailable"
        @retry="refresh"
        @updated="onAudioUpdated"
        @restarted="onRestarted"
        @dirty-change="onAudioDirty"
      />
    </template>

    <ConfirmDialog
      v-if="pendingLeave"
      :title="t('v2.rooms.unsavedTitle')"
      :confirm-label="t('v2.rooms.unsavedConfirm')"
      :cancel-label="t('v2.rooms.unsavedCancel')"
      @confirm="confirmLeave"
      @cancel="cancelLeave"
    >
      <p class="note v2-confirm-lead">{{ t('v2.rooms.unsavedBody') }}</p>
    </ConfirmDialog>
  </div>
</template>

<style scoped>
.v2-summary-unknown {
  margin-left: 2px;
  color: var(--warn);
}
</style>
