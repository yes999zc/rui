<script setup lang="ts">
/**
 * Device detail (M2): layout + data assembly only.
 *
 * Tabs map to the design IA (`?tab=overview|test|activity|connect`). Every
 * section still loads independently and keeps its own error/loading state, so a
 * flaky `/activity` or `/connection` never blanks identity/capabilities/audio.
 * Rendering of each section lives in `components/device/*`.
 *
 * M2 adds interactions (test evidence, capability-gated actions, reveal/rotate)
 * but no config writes; those stay in M3/M4.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import StateBlock from '../components/StateBlock.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ActionsPanel from '../components/device/ActionsPanel.vue'
import ActivityPanel from '../components/device/Activity.vue'
import AudioProvenance from '../components/device/AudioProvenance.vue'
import Capabilities from '../components/device/Capabilities.vue'
import Connection from '../components/device/Connection.vue'
import Identity from '../components/device/Identity.vue'
import ParamsCard from '../components/device/ParamsCard.vue'
import PlaneCard from '../components/device/PlaneCard.vue'
import SetupCard from '../components/device/SetupCard.vue'
import TestPanel from '../components/device/TestPanel.vue'
import {
  api,
  promptForToken,
  type ActivityKind,
  type DeviceAudio,
} from '../api'
import { loadCapabilities } from '../capabilities'
import { describeError, isNotFound } from '../errors'
import { metaFeature } from '../meta'
import { t, transportLabel, type MsgKey } from '../i18n'
import { useResource } from '../useResource'

type DetailTab = 'overview' | 'test' | 'activity' | 'connect'

const TABS: Array<{ id: DetailTab; key: MsgKey }> = [
  { id: 'overview', key: 'v2.detail.tab.overview' },
  { id: 'test', key: 'v2.detail.tab.test' },
  { id: 'activity', key: 'v2.detail.tab.activity' },
  { id: 'connect', key: 'v2.detail.tab.connect' },
]

const route = useRoute()
const router = useRouter()
const id = computed(() => String(route.params.id ?? ''))

const tab = computed<DetailTab>(() => {
  const raw = String(route.query.tab ?? 'overview')
  return (TABS.some((entry) => entry.id === raw) ? raw : 'overview') as DetailTab
})

function selectTab(next: DetailTab): void {
  if (next === tab.value) return
  void router.replace({ query: { ...route.query, tab: next } })
}

const deviceResource = useResource(() => api.device(id.value))
const audioResource = useResource(() => api.deviceAudio(id.value))
const activityFilter = ref<ActivityKind>('all')
const activityLimit = ref(50)
const activityResource = useResource(() =>
  api.deviceActivity(id.value, { limit: activityLimit.value, type: activityFilter.value }),
)
const connectionResource = useResource(() => api.deviceConnection(id.value))
const pluginResource = useResource(() => api.plugins())

const { data: detail, error: deviceError, loading: deviceLoading } = deviceResource
const { data: audioFromApi, error: audioError, loading: audioLoading } = audioResource
const { data: activityPage, error: activityError, loading: activityLoading } = activityResource
const { data: connection, error: connectionError, loading: connectionLoading } = connectionResource

const devicesEnabled = metaFeature('devices')
const audioProvenanceEnabled = metaFeature('device_audio_provenance')
const activityEnabled = metaFeature('device_activity')
const actionsEnabled = metaFeature('device_actions')

const deviceErrorCopy = computed(() => (deviceError.value ? describeError(deviceError.value) : null))
const notFound = computed(() => isNotFound(deviceError.value))
const audioErrorCopy = computed(() => (audioError.value ? describeError(audioError.value) : null))
const activityErrorCopy = computed(() =>
  activityError.value ? describeError(activityError.value) : null,
)
const connectionErrorCopy = computed(() =>
  connectionError.value ? describeError(connectionError.value) : null,
)

const audio = computed<DeviceAudio | null>(() => audioFromApi.value ?? detail.value?.audio ?? null)
const audioFromFallback = computed(() => !audioFromApi.value && Boolean(detail.value?.audio))

const plugin = computed(
  () => pluginResource.data.value?.items.find((item) => item.id === detail.value?.plugin_id) ?? null,
)
const pluginMissing = computed(
  () => Boolean(pluginResource.data.value) && Boolean(detail.value) && !plugin.value,
)

const activityItems = computed(() => activityPage.value?.items ?? [])
const activityTotal = computed(() => activityPage.value?.total ?? 0)
const activityTruncated = computed(() => Boolean(activityPage.value?.truncated))

async function loadAll(): Promise<void> {
  if (!devicesEnabled) return
  if (!id.value) return
  const jobs: Array<Promise<void>> = [
    deviceResource.load(),
    connectionResource.load(),
    pluginResource.load(),
  ]
  if (audioProvenanceEnabled) jobs.push(audioResource.load())
  if (activityEnabled) jobs.push(activityResource.load())
  await Promise.allSettled(jobs)
}

function onSpoken(): void {
  // `audio.say` updates the room's last-active state; refresh the evidence.
  if (activityEnabled) void activityResource.load()
}

watch(
  id,
  () => {
    // Same route record, different :id — drop the previous device before the new
    // request resolves, otherwise the old detail can mask the new 404/500.
    deviceResource.reset()
    audioResource.reset()
    activityResource.reset()
    connectionResource.reset()
    void loadAll()
  },
  { immediate: true },
)

watch([activityFilter, activityLimit], () => {
  if (devicesEnabled && activityEnabled && id.value) void activityResource.load()
})

onMounted(() => {
  if (devicesEnabled) void loadCapabilities()
})
</script>

<template>
  <div class="v2-page">
    <StateBlock
      v-if="!devicesEnabled"
      kind="unavailable"
      :title="t('v2.detail.featureOff')"
      :hint="t('v2.meta.featureOffHint')"
    />

    <StateBlock
      v-else-if="deviceLoading && !detail"
      kind="loading"
      :title="t('v2.common.loading')"
    />

    <StateBlock
      v-else-if="deviceErrorCopy && !detail"
      :kind="notFound ? 'notfound' : 'error'"
      :title="notFound ? t('v2.detail.notFound') : t('v2.detail.loadFailed')"
      :detail="deviceErrorCopy.detail"
      :hint="notFound ? t('v2.detail.notFoundHint') : deviceErrorCopy.hint"
      :status="deviceErrorCopy.status"
      :request-id="deviceErrorCopy.requestId"
      :fields="deviceErrorCopy.fields"
      :show-retry="!notFound"
      @retry="loadAll"
    >
      <RouterLink v-if="notFound" class="btn btn-primary" to="/devices">
        {{ t('v2.detail.back') }}
      </RouterLink>
      <button
        v-if="deviceErrorCopy.code === 'unauthorized'"
        type="button"
        class="btn btn-primary"
        @click="promptForToken()"
      >
        {{ t('v2.nav.token') }}
      </button>
    </StateBlock>

    <template v-else-if="detail">
      <RouterLink class="v2-back" to="/devices">
        <AppIcon name="arrowRight" :size="14" class="v2-back-icon" />
        {{ t('v2.detail.back') }}
      </RouterLink>

      <header class="v2-detail-head">
        <div class="v2-page-title">
          <span class="kicker">{{ detail.room || t('v2.devices.unassigned') }}</span>
          <h1 class="mono">{{ detail.id }}</h1>
          <p class="v2-detail-sub">
            {{ detail.vendor }} · {{ detail.model || t('v2.common.na') }} ·
            {{ transportLabel(detail.transport) }}
          </p>
        </div>
        <div class="v2-page-actions">
          <StatusBadge :status="detail.status" :reason="detail.status_reason" />
          <span class="badge badge-muted">{{ t('v2.detail.m2badge') }}</span>
          <button type="button" class="btn btn-sm" @click="loadAll">
            <AppIcon name="refresh" :size="14" />
            {{ t('v2.common.refresh') }}
          </button>
        </div>
      </header>
      <p class="v2-readonly-note">{{ t('v2.detail.interactiveNote') }}</p>

      <nav class="v2-tabs" role="tablist" :aria-label="t('v2.detail.tabsLabel')">
        <button
          v-for="entry in TABS"
          :key="entry.id"
          type="button"
          class="v2-tab"
          :class="{ 'is-active': tab === entry.id }"
          role="tab"
          :aria-selected="tab === entry.id"
          @click="selectTab(entry.id)"
        >
          {{ t(entry.key) }}
        </button>
      </nav>

      <!-- overview -->
      <div v-if="tab === 'overview'" class="v2-detail-grid">
        <div class="v2-detail-main">
          <Identity :detail="detail" :plugin="plugin" :plugin-missing="pluginMissing" />
          <Capabilities :detail="detail" />
          <AudioProvenance
            :device-id="detail.id"
            :audio="audio"
            :loading="audioLoading"
            :error-copy="audioErrorCopy"
            :from-fallback="audioFromFallback"
            :provenance-enabled="audioProvenanceEnabled"
            @retry="audioResource.load()"
          />
        </div>
        <aside class="v2-detail-side">
          <PlaneCard :plane="detail.plane" />
          <SetupCard :setup="detail.setup" />
          <ParamsCard :params="detail.params" :unknown-params="detail.unknown_params" />
        </aside>
      </div>

      <!-- test / actions -->
      <div v-else-if="tab === 'test'" class="v2-detail-stack">
        <TestPanel :device-id="detail.id" />
        <ActionsPanel :detail="detail" :enabled="actionsEnabled" @spoken="onSpoken" />
      </div>

      <!-- activity -->
      <ActivityPanel
        v-else-if="tab === 'activity'"
        :items="activityItems"
        :total="activityTotal"
        :truncated="activityTruncated"
        :loading="activityLoading"
        :error-copy="activityErrorCopy"
        :enabled="activityEnabled"
        :filter="activityFilter"
        :limit="activityLimit"
        @update:filter="activityFilter = $event"
        @update:limit="activityLimit = $event"
        @retry="activityResource.load()"
        @refresh="activityResource.load()"
      />

      <!-- connection -->
      <div v-else class="v2-detail-stack">
        <Connection
          :device-id="detail.id"
          :connection="connection"
          :loading="connectionLoading"
          :error-copy="connectionErrorCopy"
          @retry="connectionResource.load()"
          @refresh="connectionResource.load()"
        />
      </div>
    </template>
  </div>
</template>
