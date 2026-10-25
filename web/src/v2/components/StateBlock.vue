<script setup lang="ts">
/** Shared loading / empty / error / not-found / unavailable block. */
import { computed } from 'vue'
import AppIcon from '../../components/AppIcon.vue'
import { t } from '../i18n'
import type { FieldIssue } from '../errors'

type Kind = 'loading' | 'error' | 'empty' | 'notfound' | 'unavailable'

const props = withDefaults(
  defineProps<{
    kind?: Kind
    title?: string
    detail?: string
    hint?: string
    status?: number | null
    requestId?: string | null
    retryLabel?: string
    showRetry?: boolean
    icon?: string
    fields?: FieldIssue[]
  }>(),
  {
    kind: 'empty',
    title: '',
    detail: '',
    hint: '',
    status: null,
    requestId: null,
    retryLabel: '',
    showRetry: false,
    icon: '',
    fields: () => [],
  },
)

const emit = defineEmits<{ retry: [] }>()

const iconName = computed(() => {
  if (props.icon) return props.icon
  switch (props.kind) {
    case 'notfound':
      return 'compass'
    case 'unavailable':
      return 'lock'
    case 'error':
      return 'alert'
    case 'loading':
      return 'clock'
    default:
      return 'list'
  }
})
</script>

<template>
  <div class="v2-state" :class="`is-${kind}`" role="status" aria-live="polite">
    <div v-if="kind === 'loading'" class="v2-skeleton" aria-hidden="true">
      <span /><span /><span />
    </div>
    <AppIcon v-else :name="iconName" :size="22" class="v2-state-icon" />
    <div class="v2-state-body">
      <h2 v-if="title" class="v2-state-title">{{ title }}</h2>
      <p v-if="detail" class="v2-state-detail">{{ detail }}</p>
      <p v-if="hint" class="note">{{ hint }}</p>
      <p v-if="status" class="meta">{{ t('v2.common.statusCode', { status }) }}</p>
      <p v-if="requestId" class="meta v2-break">{{ t('v2.common.requestId', { id: requestId }) }}</p>
      <ul v-if="fields.length" class="v2-field-list">
        <li v-for="field in fields" :key="field.path">
          <span class="mono">{{ field.path }}</span>
          <span v-if="field.message">{{ field.message }}</span>
        </li>
      </ul>
      <div v-if="showRetry" class="v2-state-actions">
        <button class="btn btn-sm" type="button" @click="emit('retry')">
          {{ retryLabel || t('v2.common.retry') }}
        </button>
      </div>
      <slot />
    </div>
  </div>
</template>
