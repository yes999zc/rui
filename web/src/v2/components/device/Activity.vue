<script setup lang="ts">
/**
 * Device activity card: structured turns and text-matched logs are shown in two
 * separate groups, because `matched_by="literal"` log lines are evidence of a
 * string match, not proof that this device spoke. The `type` filter is a
 * server-side query (`type=turn|log|all`), so switching it never filters stale
 * rows on the client.
 */
import { computed } from 'vue'
import AppIcon from '../../../components/AppIcon.vue'
import type { ActivityItem, ActivityKind } from '../../api'
import type { ErrorCopy } from '../../errors'
import { formatDateTime, relativeTime } from '../../format'
import { t, type MsgKey } from '../../i18n'
import StateBlock from '../StateBlock.vue'

const props = defineProps<{
  items: ActivityItem[]
  total: number
  truncated: boolean
  loading: boolean
  errorCopy: ErrorCopy | null
  enabled: boolean
  filter: ActivityKind
  limit: number
}>()

const emit = defineEmits<{
  'update:filter': [value: ActivityKind]
  'update:limit': [value: number]
  retry: []
  refresh: []
}>()

const FILTERS: Array<{ value: ActivityKind; key: MsgKey }> = [
  { value: 'all', key: 'v2.activity.filter.all' },
  { value: 'turn', key: 'v2.activity.filter.turn' },
  { value: 'log', key: 'v2.activity.filter.log' },
]

const LIMITS = [20, 50, 100, 200]

const turns = computed(() => props.items.filter((item) => item.type === 'turn'))
const logs = computed(() => props.items.filter((item) => item.type === 'log'))
const showStale = computed(() => Boolean(props.errorCopy) && props.items.length > 0)

function activityTypeLabel(type: string): string {
  if (type === 'turn') return t('v2.activity.type.turn')
  if (type === 'log') return t('v2.activity.type.log')
  return type
}

function activityDetail(item: ActivityItem, key: string): string {
  const value = item.detail?.[key]
  if (value === null || value === undefined) return ''
  if (Array.isArray(value)) {
    return value
      .map((entry) => (entry && typeof entry === 'object' ? JSON.stringify(entry) : String(entry)))
      .join(', ')
  }
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function onLimit(event: Event): void {
  const value = Number((event.target as HTMLSelectElement).value)
  if (Number.isFinite(value)) emit('update:limit', value)
}
</script>

<template>
  <section class="v2-card">
    <div class="v2-card-head">
      <h2 class="v2-card-title">{{ t('v2.activity.title') }}</h2>
      <span v-if="total" class="meta">{{ t('v2.activity.total', { n: total }) }}</span>
    </div>
    <p class="note v2-card-lede">{{ t('v2.activity.lede') }}</p>

    <StateBlock
      v-if="!enabled"
      kind="unavailable"
      :title="t('v2.activity.featureOff')"
      :hint="t('v2.meta.featureOffHint')"
    />

    <template v-else>
      <div class="v2-activity-toolbar">
        <div class="v2-seg" role="group" :aria-label="t('v2.activity.filter')">
          <button
            v-for="option in FILTERS"
            :key="option.value"
            type="button"
            class="v2-seg-btn"
            :class="{ 'is-active': filter === option.value }"
            :aria-pressed="filter === option.value"
            @click="emit('update:filter', option.value)"
          >
            {{ t(option.key) }}
          </button>
        </div>
        <label class="v2-limit">
          <span class="meta">{{ t('v2.activity.limit') }}</span>
          <select
            class="input v2-limit-select"
            :value="limit"
            :aria-label="t('v2.activity.limit')"
            @change="onLimit"
          >
            <option v-for="n in LIMITS" :key="n" :value="n">{{ n }}</option>
          </select>
        </label>
        <button
          type="button"
          class="btn btn-sm v2-activity-refresh"
          :disabled="loading"
          @click="emit('refresh')"
        >
          <AppIcon name="refresh" :size="14" />
          {{ t('v2.common.refresh') }}
        </button>
        <span v-if="loading" class="tag">{{ t('v2.common.loading') }}</span>
      </div>

      <div v-if="showStale" class="banner banner-warn v2-banner v2-activity-stale" role="status">
        <AppIcon name="alert" :size="16" />
        <div class="v2-banner-body">
          <strong>{{ t('v2.activity.stale') }}</strong>
          <p v-if="errorCopy" class="note">{{ errorCopy.hint }}</p>
        </div>
        <button type="button" class="btn btn-sm" @click="emit('retry')">
          {{ t('v2.common.retry') }}
        </button>
      </div>

      <StateBlock
        v-if="loading && !items.length"
        kind="loading"
        :title="t('v2.common.loading')"
      />
      <StateBlock
        v-else-if="errorCopy && !items.length"
        kind="error"
        :title="t('v2.activity.unavailable')"
        :detail="errorCopy.detail"
        :hint="errorCopy.hint"
        :status="errorCopy.status"
        :request-id="errorCopy.requestId"
        show-retry
        @retry="emit('retry')"
      />
      <StateBlock
        v-else-if="items.length === 0"
        kind="empty"
        :title="t('v2.activity.empty')"
        :hint="t('v2.activity.emptyHint')"
      />

      <div v-else class="v2-activity-groups">
        <section v-if="filter !== 'log'" class="v2-activity-group">
          <header class="v2-activity-group-head">
            <h3>{{ t('v2.activity.turns') }}</h3>
            <span class="meta">{{ t('v2.activity.total', { n: turns.length }) }}</span>
          </header>
          <p v-if="!turns.length" class="empty">{{ t('v2.activity.turnEmpty') }}</p>
          <div v-else class="v2-activity">
            <article v-for="item in turns" :key="item.id" class="v2-activity-item">
              <header class="v2-activity-head">
                <span class="meta mono" :title="formatDateTime(item.ts)">
                  {{ relativeTime(item.ts) }}
                </span>
                <span class="tag">{{ activityTypeLabel(item.type) }}</span>
                <span v-if="item.level" class="tag mono">{{ item.level }}</span>
                <span class="tag mono" :title="t('v2.activity.matchedBy')">
                  {{ t('v2.activity.matchedBy') }}:
                  {{ item.matched_by === 'device_id' ? t('v2.activity.matchedByDevice') : item.matched_by }}
                </span>
              </header>
              <p class="v2-activity-title">{{ item.title }}</p>
              <p v-if="activityDetail(item, 'reply')" class="v2-activity-detail">
                <span class="v2-k">{{ t('v2.activity.reply') }}</span>
                <span>{{ activityDetail(item, 'reply') }}</span>
              </p>
              <p v-if="activityDetail(item, 'tools')" class="v2-activity-detail">
                <span class="v2-k">{{ t('v2.activity.tools') }}</span>
                <span class="mono">{{ activityDetail(item, 'tools') }}</span>
              </p>
              <p v-if="activityDetail(item, 'error')" class="v2-activity-detail is-fail">
                <span class="v2-k">{{ t('v2.activity.error') }}</span>
                <span>{{ activityDetail(item, 'error') }}</span>
              </p>
            </article>
          </div>
        </section>

        <section v-if="filter !== 'turn'" class="v2-activity-group">
          <header class="v2-activity-group-head">
            <h3>{{ t('v2.activity.logs') }}</h3>
            <span class="meta">{{ t('v2.activity.total', { n: logs.length }) }}</span>
          </header>
          <p class="note v2-activity-log-notice">{{ t('v2.activity.logNotice') }}</p>
          <p v-if="!logs.length" class="empty">{{ t('v2.activity.logEmpty') }}</p>
          <div v-else class="v2-activity">
            <article v-for="item in logs" :key="item.id" class="v2-activity-item">
              <header class="v2-activity-head">
                <span class="meta mono" :title="formatDateTime(item.ts)">
                  {{ relativeTime(item.ts) }}
                </span>
                <span class="tag">{{ activityTypeLabel(item.type) }}</span>
                <span v-if="item.level" class="tag mono">{{ item.level }}</span>
                <span
                  v-if="item.matched_by === 'literal'"
                  class="badge badge-warn"
                  :title="t('v2.activity.literalHint')"
                >
                  {{ t('v2.activity.matchedByLiteral') }}
                </span>
                <span v-else class="tag mono">{{ item.matched_by }}</span>
              </header>
              <p class="v2-activity-title">{{ item.title }}</p>
              <p v-if="activityDetail(item, 'line')" class="v2-activity-line mono">
                {{ activityDetail(item, 'line') }}
              </p>
            </article>
          </div>
        </section>
      </div>

      <p v-if="truncated" class="note">
        {{ t('v2.activity.truncated', { n: items.length }) }}
      </p>
    </template>
  </section>
</template>
