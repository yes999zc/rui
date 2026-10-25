<script setup lang="ts">
/**
 * Room view (M5). Rooms are derived from `devices[].room` and are not a CRUD
 * resource (contract §3); this panel only groups and links. A device can be
 * staged as `multi_device.default_output` here; the actual PATCH belongs to the
 * policy editor, which owns the draft and the second confirmation.
 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../../../components/AppIcon.vue'
import type { DeviceSummary, HouseInfo } from '../../api'
import { t } from '../../i18n'
import StatusBadge from '../StatusBadge.vue'

const props = defineProps<{
  house: HouseInfo | null
  devices: DeviceSummary[]
  writeEnabled: boolean
  overrideCounts: Record<string, number>
}>()

const emit = defineEmits<{ 'set-default': [id: string] }>()

interface RoomItem {
  id: string
  device: DeviceSummary | null
}

interface RoomGroup {
  room: string
  items: RoomItem[]
  total: number
  online: number
  defaultOutput: boolean
  duplicate: boolean
}

const deviceById = computed(() => {
  const map = new Map<string, DeviceSummary>()
  for (const device of props.devices) map.set(device.id, device)
  return map
})

const defaultOutputId = computed(() => props.house?.multi_device?.default_output ?? '')

const groups = computed<RoomGroup[]>(() => {
  const byRoom = new Map<string, DeviceSummary[]>()
  for (const device of props.devices) {
    const key = device.room ?? ''
    const bucket = byRoom.get(key)
    if (bucket) bucket.push(device)
    else byRoom.set(key, [device])
  }

  const order: string[] = []
  const push = (room: string): void => {
    if (!order.includes(room)) order.push(room)
  }
  for (const room of props.house?.rooms ?? []) push(room.room)
  const rest = [...byRoom.keys()].filter((key) => key !== '').sort((a, b) => a.localeCompare(b))
  for (const room of rest) push(room)
  if (byRoom.has('')) push('')

  return order.map((room) => {
    const houseRoom = props.house?.rooms?.find((entry) => entry.room === room)
    const ids = houseRoom?.device_ids?.length
      ? [...houseRoom.device_ids]
      : (byRoom.get(room) ?? []).map((device) => device.id)
    const seen = new Set<string>()
    const items: RoomItem[] = []
    for (const id of ids) {
      if (seen.has(id)) continue
      seen.add(id)
      items.push({ id, device: deviceById.value.get(id) ?? null })
    }
    const online =
      houseRoom?.online ?? items.filter((item) => item.device?.ready).length
    return {
      room,
      items,
      total: items.length,
      online,
      defaultOutput: Boolean(
        houseRoom?.default_output ||
          (defaultOutputId.value && items.some((item) => item.id === defaultOutputId.value)),
      ),
      duplicate: items.length > 1,
    }
  })
})

function isDefault(id: string): boolean {
  return defaultOutputId.value === id
}

function canSetDefault(item: RoomItem): boolean {
  return props.writeEnabled && Boolean(item.device?.enabled) && !isDefault(item.id)
}

function overrideCount(id: string): number {
  return props.overrideCounts[id] ?? 0
}

function setDefault(id: string): void {
  emit('set-default', id)
}
</script>

<template>
  <section class="v2-rooms" :aria-label="t('v2.rooms.roomsAria')">
    <article
      v-for="group in groups"
      :key="`room:${group.room}`"
      class="v2-room"
      :data-testid="group.room ? `room:${group.room}` : 'room-unassigned'"
    >
      <header class="v2-room-head">
        <h2>{{ group.room || t('v2.devices.unassigned') }}</h2>
        <span class="meta">
          {{ t('v2.devices.roomCount', { online: group.online, total: group.total }) }}
        </span>
        <span v-if="group.defaultOutput" class="badge badge-accent">
          {{ t('v2.devices.defaultOutput') }}
        </span>
        <span
          v-if="group.duplicate"
          class="badge badge-warn"
          data-testid="room-duplicate"
        >
          {{ t('v2.warning.code.duplicate_room') }}
        </span>
      </header>

      <p v-if="group.duplicate" class="note v2-room-duplicate-note">
        {{ t('v2.rooms.duplicate', { n: group.total }) }}
      </p>

      <p v-if="!group.items.length" class="empty">{{ t('v2.rooms.noDevices') }}</p>

      <div v-else class="v2-house-device-grid">
        <div
          v-for="item in group.items"
          :key="item.id"
          class="v2-house-device"
          :data-testid="`room-device-${item.id}`"
        >
          <div class="v2-house-device-top">
            <RouterLink
              class="mono v2-house-device-id"
              :to="`/devices/${item.id}`"
              :aria-label="t('v2.rooms.deviceAria', { id: item.id })"
            >
              {{ item.id }}
            </RouterLink>
            <StatusBadge
              v-if="item.device"
              :status="item.device.status"
              :reason="item.device.status_reason"
            />
            <span v-else class="tag">{{ t('v2.common.na') }}</span>
          </div>

          <p class="v2-house-device-sub">
            <span>{{ item.device?.model || t('v2.devices.modelFallback') }}</span>
            <span aria-hidden="true">·</span>
            <span>{{ item.device?.vendor || t('v2.devices.vendorFallback') }}</span>
            <span v-if="item.device && !item.device.enabled" class="tag">
              {{ t('v2.common.disabled') }}
            </span>
          </p>

          <div class="v2-house-device-badges">
            <span v-if="item.device?.ready" class="tag is-ok">{{ t('v2.rooms.ready') }}</span>
            <span v-else-if="item.device" class="tag">{{ t('v2.rooms.notReady') }}</span>
            <span
              v-if="overrideCount(item.id) > 0"
              class="tag badge-accent"
              :data-testid="`override-chip-${item.id}`"
            >
              {{ t('v2.house.overrideChip', { id: item.id, n: overrideCount(item.id) }) }}
            </span>
          </div>

          <div class="v2-house-device-actions">
            <RouterLink class="btn btn-sm" :to="`/devices/${item.id}`">
              <AppIcon name="arrowRight" :size="14" />
              {{ t('v2.rooms.openDevice') }}
            </RouterLink>
            <span v-if="isDefault(item.id)" class="badge badge-accent">
              {{ t('v2.rooms.defaultSet') }}
            </span>
            <button
              v-else-if="canSetDefault(item)"
              type="button"
              class="btn btn-sm"
              :data-testid="`set-default-${item.id}`"
              @click="setDefault(item.id)"
            >
              {{ t('v2.rooms.setDefault') }}
            </button>
          </div>
        </div>
      </div>
    </article>
  </section>
</template>

<style scoped>
.v2-rooms {
  display: flex;
  flex-direction: column;
  gap: var(--s4);
}

.v2-room-duplicate-note {
  margin: 0 0 var(--s3);
  color: var(--warn);
}

.v2-house-device-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: var(--s3);
}

.v2-house-device {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: var(--s3);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: var(--surface);
}

.v2-house-device-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s2);
}

.v2-house-device-id {
  font-size: 13.5px;
  font-weight: 650;
  color: var(--ink);
  overflow-wrap: anywhere;
}

.v2-house-device-sub {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  margin: 0;
  color: var(--ink-2);
  font-size: 11.5px;
}

.v2-house-device-badges {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  min-height: 18px;
}

.v2-house-device-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s2);
  flex-wrap: wrap;
  margin-top: var(--s1);
}
</style>
