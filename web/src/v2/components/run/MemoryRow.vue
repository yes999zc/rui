<script setup lang="ts">
/**
 * One memory entry (M8).
 *
 * `POST /api/v1/memory` is the only write path: `confirm` promotes a candidate
 * and `remove` deletes one entry. Copy and buttons live here; the page owns the
 * request and the confirm dialog so a failed write never mutates the list.
 */
import { computed } from 'vue'
import AppIcon from '../../../components/AppIcon.vue'
import type { MemoryItem } from '../../api'
import { formatDateTime } from '../../format'
import { t } from '../../i18n'

const props = defineProps<{
  item: MemoryItem
  busy?: boolean
}>()

const emit = defineEmits<{ confirm: []; remove: [] }>()

const created = computed(() =>
  props.item.created > 0 ? formatDateTime(props.item.created) : '',
)

function sourceLabel(source: string): string {
  if (source === 'voice') return t('v2.memory.source.voice')
  if (source === 'console') return t('v2.memory.source.console')
  if (source === 'turn') return t('v2.memory.source.turn')
  return source ? t('v2.memory.source.other', { value: source }) : t('v2.common.na')
}
</script>

<template>
  <li
    class="v2-memory-row"
    data-testid="memory-item"
    :data-memory-id="item.id"
    :data-confirmed="item.confirmed ? 'true' : 'false'"
  >
    <div class="v2-memory-main">
      <p class="v2-memory-text">{{ item.text }}</p>
      <div class="v2-memory-meta">
        <span class="badge" :class="item.confirmed ? 'badge-accent' : 'badge-warn'">
          {{ item.confirmed ? t('v2.memory.confirmed') : t('v2.memory.candidate') }}
        </span>
        <span class="tag mono" :title="t('v2.memory.source')">{{ sourceLabel(item.source) }}</span>
        <span v-if="created" class="meta">{{ t('v2.memory.created') }} {{ created }}</span>
        <span class="meta mono">#{{ item.id }}</span>
      </div>
    </div>
    <div class="v2-memory-actions" :aria-label="t('v2.memory.actionAria', { text: item.text })">
      <button
        v-if="!item.confirmed"
        type="button"
        class="btn btn-sm"
        :disabled="busy"
        data-testid="memory-confirm"
        @click="emit('confirm')"
      >
        <AppIcon name="check" :size="13" />
        {{ t('v2.memory.confirmAction') }}
      </button>
      <button
        type="button"
        class="btn btn-sm btn-ghost"
        :disabled="busy"
        data-testid="memory-remove"
        @click="emit('remove')"
      >
        <AppIcon name="trash" :size="13" />
        {{ t('v2.memory.removeAction') }}
      </button>
    </div>
  </li>
</template>
