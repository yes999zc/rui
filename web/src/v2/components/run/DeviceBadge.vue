<script setup lang="ts">
/**
 * One device/room badge for the M8 run pages.
 *
 * `/api/v1/turns` carries an additive `device_id`; the room/model come from the
 * v2 device catalog. When the id is not in the catalog the badge says so and
 * keeps the raw id instead of silently dropping the turn or inventing a room.
 * An empty id is the contract's "whole house" case, never a blank cell.
 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { t } from '../../i18n'

const props = withDefaults(
  defineProps<{
    deviceId?: string
    room?: string
    model?: string
    mapped?: boolean
    link?: boolean
    /** The catalog request failed: `!mapped` is "unknown", not "not listed". */
    catalogUnavailable?: boolean
  }>(),
  {
    deviceId: '',
    room: '',
    model: '',
    mapped: false,
    link: true,
    catalogUnavailable: false,
  },
)

const id = computed(() => (props.deviceId ?? '').trim())
const isHouse = computed(() => id.value === '')
// An id missing from the catalog has no detail page to open; render it as
// plain text rather than a RouterLink that would land on a 404.
const clickable = computed(() => props.link && props.mapped)
const title = computed(() =>
  isHouse.value
    ? t('v2.conversation.wholeHouse')
    : props.mapped
      ? [props.room, props.model].filter(Boolean).join(' · ') || id.value
      : props.catalogUnavailable
        ? t('v2.conversation.deviceCatalogUnknown', { id: id.value })
        : t('v2.conversation.deviceUnmapped', { id: id.value }),
)
</script>

<template>
  <span class="v2-run-badges">
    <span v-if="isHouse" class="v2-run-badge is-house" data-device-badge="house">
      {{ t('v2.conversation.wholeHouse') }}
    </span>
    <template v-else>
      <component
        :is="clickable ? RouterLink : 'span'"
        :to="clickable ? `/devices/${encodeURIComponent(id)}` : undefined"
        class="v2-run-badge v2-run-badge-device"
        :class="{ 'is-unknown': !mapped }"
        :title="title"
        data-device-badge="device"
        :data-device-id="id"
        :data-device-catalog="catalogUnavailable ? 'unavailable' : undefined"
      >
        {{ id }}
      </component>
      <span v-if="mapped && room" class="v2-run-badge" data-device-badge="room">{{ room }}</span>
      <span v-else-if="mapped" class="v2-run-badge is-unknown" data-device-badge="room">
        {{ t('v2.conversation.roomUnknown') }}
      </span>
      <span v-if="mapped && model" class="v2-run-badge is-model" data-device-badge="model">
        {{ model }}
      </span>
    </template>
  </span>
</template>
